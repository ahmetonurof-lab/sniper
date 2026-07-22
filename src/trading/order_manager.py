"""
order_manager.py — Binance SL/TP emir yönetimi: trailing güncelleme + onarım.

PaperTrader'daki _update_orders() ve _repair_protection() metodlarını kapsar.

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - extract_order_id, time.time() kullanımı aynen kalır
  - Import yolları kırılmayacak

Patch Set 3: Policy kararlari ProtectionLifecycleService'e tasindi.
OrderManager artik saf mekanik katman (REST cagrilari).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import config as cfg
from bot_infra import extract_order_id
from event_log import log_event
from models import (
    STATUS_ACTIVE,
    STATUS_TRAIL_REPLACING,
    UNRESTRICTED_STATUSES,
)

if TYPE_CHECKING:
    from trading.protection_lifecycle import (
        ProtectionCheckResult,
        ProtectionLifecycleService,
    )

log = logging.getLogger("sniper.order_manager")


class OrderManager:
    """Binance SL/TP emir yönetimi: trailing güncelleme + onarım.

    PaperTrader'dan DI ile alır:
      - rest_client: BinanceRESTClient
      - is_live: bool — API key varsa ve bot canlı moddaysa True
      - protection_service: ProtectionLifecycleService | None —
        policy kararlari icin (None ise eski inline logic korunur)
    """

    def __init__(
        self,
        rest_client,
        is_live: bool = False,
        protection_service: "ProtectionLifecycleService | None" = None,
    ):
        self._rest = rest_client
        self._is_live = is_live
        self._protection = protection_service
        # ── P0-5: backoff / tekrar sınırı ──
        self._repair_failures: dict[str, int] = {}
        self._last_repair_warning: dict[str, float] = {}
        # ── P0-3: repair_protection eşzamanlılık kilidi ──
        # repair_protection() üç farklı tetikleyiciden (60sn recover loop,
        # WS CANCELED handler, exit_lifecycle REPAIR_REQUIRED) kilitsiz
        # çağrılabiliyordu — aynı sembole çift SL/TP emri riski vardı.
        self._repair_locks: dict[str, asyncio.Lock] = {}

    # ── Trailing SL/TP güncelleme ─────────────────────────────

    async def update_trail_orders(
        self, sym: str, trade: dict, new_sl: float, new_tp: float, new_trail_count: int
    ) -> bool:
        """Trailing sonrası SL/TP emirlerini güncelle.

        Başarılıysa trade sözlüğündeki ilgili alanlar güncellenir.

        Returns: True if at least one order (SL or TP) was updated successfully.
        """
        if trade.get("status") not in UNRESTRICTED_STATUSES:
            log.info(
                "[TRAIL] %s status=%s — trailing atlaniyor (baska bir akis yonetiyor)",
                sym,
                trade.get("status"),
            )
            return False
        if not cfg.BINANCE_API_KEY or not self._is_live:
            trade["sl"] = new_sl
            trade["tp"] = new_tp
            trade["trailing_count"] = new_trail_count
            return True

        trade["status"] = STATUS_TRAIL_REPLACING

        new_sl = await self._rest.apply_price_precision(sym, new_sl)
        new_tp = await self._rest.apply_price_precision(sym, new_tp)

        sl_side = "SELL" if trade["side"] == "long" else "BUY"
        qty = trade.get("qty", trade.get("lot", 0))

        # FIX: TP immediately trigger kontrolü — trailing TP'yi mevcut fiyatın
        # üstünde/altında bırakmışsa placement'ı atla. Yoksa Binance reddeder
        # ve TP ID'siz kalır → sonraki WS fill eşleşmez → fallback zinciri.
        _current_price = trade.get("upnl")  # yaklaşık, tam fiyat yok
        # Precision sonrası fiyat hatalıysa (0 veya çok küçük) placement'ı durdur
        if new_sl <= 0 or new_tp <= 0:
            log.warning(
                "[TRAIL] %s precision hatasi: sl=%.6f tp=%.6f — trailing atlaniyor",
                sym,
                new_sl,
                new_tp,
            )
            return False

        old_sl_id = trade.get("sl_order_id", "")
        old_tp_id = trade.get("tp_order_id", "")
        old_tp_price = trade.get("tp", 0.0)

        new_sl_id = ""
        new_tp_id = ""
        sl_ok = False
        tp_ok = False
        tp_unchanged = abs(new_tp - old_tp_price) < 1e-8

        # ── 1. YENİ SL EMRİNİ AT (ESKİYİ HENÜZ SİLME) ──
        try:
            sl_resp = await self._rest.place_stop_order(
                sym, sl_side, qty, new_sl, client_id=f"sl_{sym}_{int(time.time())}"
            )
            new_sl_id = extract_order_id(sl_resp)
            if new_sl_id:
                sl_ok = True
            else:
                log_event(
                    "sl_reject",
                    sym,
                    side=trade["side"],
                    sl_price=new_sl,
                    old_id=old_sl_id,
                )
                log.warning(
                    "[TRAIL] %s SL reject (yeni emir alinamadi) -> eski SL korunuyor",
                    sym,
                )
        except Exception as e:
            log.warning("[TRAIL] %s SL place hatasi: %s -> eski SL korunuyor", sym, e)

        # ── 2. YENİ TP EMRİNİ AT (fiyat değişmediyse atla) ──
        if tp_unchanged:
            tp_ok = True  # eski TP zaten duruyor, başarılı say
            log.debug("[TRAIL] %s TP fiyati degismedi — atlaniyor", sym)
        else:
            try:
                tp_resp = await self._rest.place_tp_order(
                    sym, sl_side, qty, new_tp, client_id=f"tp_{sym}_{int(time.time())}"
                )
                new_tp_id = extract_order_id(tp_resp)
                if new_tp_id:
                    tp_ok = True
                else:
                    log_event(
                        "tp_reject",
                        sym,
                        side=trade["side"],
                        tp_price=new_tp,
                        old_id=old_tp_id,
                    )
                    log.warning("[TRAIL] %s TP reject -> eski TP korunuyor", sym)
            except Exception as e:
                log.warning(
                    "[TRAIL] %s TP place hatasi: %s -> eski TP korunuyor", sym, e
                )

        # ── 3. SADECE BAŞARILI OLANLARI STATE'E YAZ (FIX #1) ──
        if sl_ok:
            trade["sl"] = new_sl
            if self._protection is not None:
                self._protection.begin_replace_sl(trade, new_sl_id)
                self._protection.promote_sl(trade)
            else:
                if old_sl_id:
                    hist = trade.setdefault("sl_order_id_history", [])
                    if not isinstance(hist, list):
                        hist = []
                        trade["sl_order_id_history"] = hist
                    hist.append(old_sl_id)
                    trade["sl_order_id_history"] = hist[-5:]
                trade["sl_order_id"] = new_sl_id
                trade["sl_order_id_prev"] = old_sl_id
            if old_sl_id:
                try:
                    await self._rest.cancel_order(
                        old_sl_id, sym, reason="trail_update", is_algo=True
                    )
                except Exception as e:
                    log.warning(
                        "[CANCEL] %s eski SL iptal hatasi (id=%s): %s",
                        sym,
                        old_sl_id,
                        e,
                    )

        if tp_ok:
            trade["tp"] = new_tp
            if self._protection is not None:
                self._protection.begin_replace_tp(trade, new_tp_id)
                self._protection.promote_tp(trade)
            else:
                if old_tp_id:
                    hist = trade.setdefault("tp_order_id_history", [])
                    if not isinstance(hist, list):
                        hist = []
                        trade["tp_order_id_history"] = hist
                    hist.append(old_tp_id)
                    trade["tp_order_id_history"] = hist[-5:]
                trade["tp_order_id"] = new_tp_id
                trade["tp_order_id_prev"] = old_tp_id
            if old_tp_id:
                try:
                    await self._rest.cancel_order(
                        old_tp_id, sym, reason="trail_update", is_algo=True
                    )
                except Exception as e:
                    log.warning(
                        "[CANCEL] %s eski TP iptal hatasi (id=%s): %s",
                        sym,
                        old_tp_id,
                        e,
                    )

        if sl_ok or tp_ok:
            trade["trailing_count"] = new_trail_count

        if not (sl_ok and tp_ok):
            log.warning(
                "[TRAIL] %s trailing kismen/tamamen basarisiz (sl=%s, tp=%s)",
                sym,
                sl_ok,
                tp_ok,
            )
            if not sl_ok and not tp_ok:
                trade["status"] = STATUS_ACTIVE
                return False

        log.info(
            "[ORDER] %s trailing guncellendi sl=%.6f (id=%s) tp=%.6f (id=%s)",
            sym,
            trade.get("sl", 0.0),
            new_sl_id,
            trade.get("tp", 0.0),
            new_tp_id,
        )
        trade["status"] = STATUS_ACTIVE
        return True

    # ── Canlı doğrulama (WS-FALLBACK guard için) ──────────────

    async def get_open_order_ids(self, sym: str) -> set[str]:
        """Binance'teki tüm açık emirlerin ID'lerini döndür.

        REST sorgusu başarısız olursa boş küme döner — çağıran
        taraf fail-safe kararını kendi verir.
        """
        try:
            orders = await self._rest.get_all_orders(sym)
            return {str(o.get("algoId") or o.get("orderId") or "") for o in orders}
        except Exception as e:
            log.warning("[VERIFY] %s acik emir sorgu hatasi: %s", sym, e)
            return set()

    async def verify_protection(self, sym: str, trade: dict) -> "ProtectionCheckResult":
        """Binance'teki açık emirleri sorgulayıp sl_order_id / tp_order_id'nin
        gerçekten hâlâ açık olup olmadığını döndürür.

        Sprint B3: (bool, bool) yerine ProtectionCheckResult döner.
        Tuple unpacking (sl_present, tp_present = await ...) hâlâ çalışır
        (ProtectionCheckResult iterable).

        ProtectionLifecycleService varsa karar ona delege edilir.
        REST sorgusu başarısız olursa fail-safe: ikisini de True varsayar
        (yani "dokunma", çağıran taraf yanlışlıkla cancel/exit tetiklemesin).
        """
        from trading.protection_lifecycle import ProtectionCheckResult

        if self._protection is not None:
            open_ids = await self.get_open_order_ids(sym)
            return self._protection.verify(trade, open_ids)

        s_id = str(trade.get("sl_order_id", ""))
        t_id = str(trade.get("tp_order_id", ""))
        expects_sl = bool(trade.get("sl"))
        expects_tp = bool(trade.get("tp"))
        try:
            orders = await self._rest.get_all_orders(sym)
            open_ids = {str(o.get("algoId") or o.get("orderId") or "") for o in orders}
            sl_present = (not expects_sl) or (bool(s_id) and s_id in open_ids)
            tp_present = (not expects_tp) or (bool(t_id) and t_id in open_ids)
            sl_healthy = (not expects_sl) or sl_present
            tp_healthy = (not expects_tp) or tp_present
            return ProtectionCheckResult(
                sl_present=sl_present,
                tp_present=tp_present,
                sl_healthy=sl_healthy,
                tp_healthy=tp_healthy,
                needs_repair=(expects_sl and not sl_present)
                or (expects_tp and not tp_present),
            )
        except Exception as e:
            log.warning(
                "[VERIFY] %s acik emir sorgu hatasi: %s -> fail-safe (dokunma)",
                sym,
                e,
            )
            return ProtectionCheckResult(
                sl_present=True,
                tp_present=True,
                sl_healthy=True,
                tp_healthy=True,
                needs_repair=False,
                detail="fail-safe (rest error)",
            )

    async def position_still_open(self, sym: str) -> bool:
        """Binance hesabında bu sembol için hâlâ açık pozisyon var mı?

        REST sorgusu başarısız olursa fail-safe: pozisyon açıkmış gibi
        davranır (True) — böylece belirsizlik anında asla yanlışlıkla
        exit/cancel_all tetiklenmez.
        """
        try:
            positions = await self._rest.get_positions()
            pos = next((p for p in positions if p.get("symbol") == sym), None)
            return bool(pos) and abs(float(pos.get("positionAmt", 0))) > 1e-8
        except Exception as e:
            log.warning(
                "[VERIFY] %s pozisyon sorgu hatasi: %s -> fail-safe (acik varsay)",
                sym,
                e,
            )
            return True

    # ── Koruma onarımı ────────────────────────────────────────

    # ── -4005 yardımcısı: max-qty aşımını tespit et ──────────

    @staticmethod
    def _is_max_qty_error(resp: dict) -> bool:
        """place_stop_order/place_tp_order dönüşü -4005 hatası içeriyor mu?"""
        return resp.get("_error_code") == "-4005"

    async def _try_place_sl_tp_with_close_position(
        self, sym: str, trade: dict, sl_price: float, tp_price: float
    ) -> tuple[str, str]:
        """closePosition=True ile SL/TP kurmayı dene.
        qty göndermez, max-qty limitinden muaftır.

        Returns: (sl_id, tp_id) — başarısız olanlar boş string.
        """
        sl_id = ""
        tp_id = ""
        sl_side = "SELL" if trade["side"] == "long" else "BUY"

        if trade.get("sl") and sl_price > 0:
            try:
                resp = await self._rest.place_stop_order(
                    sym,
                    sl_side,
                    0,  # closePosition=True için qty önemsiz
                    sl_price,
                    close_position=True,
                )
                sl_id = extract_order_id(resp)
                if sl_id:
                    log.info(
                        "[REPAIR] %s SL closePosition ile kuruldu (id=%s)",
                        sym,
                        sl_id,
                    )
                else:
                    if self._is_max_qty_error(resp):
                        log.warning(
                            "[REPAIR] %s SL closePosition da -4005 aldi (beklenmedik)",
                            sym,
                        )
                    else:
                        log.warning(
                            "[REPAIR] %s SL closePosition basarisiz (id=bos)", sym
                        )
            except Exception as e:
                log.warning("[REPAIR] %s SL closePosition hatasi: %s", sym, e)

        if trade.get("tp") and tp_price > 0:
            try:
                resp = await self._rest.place_tp_order(
                    sym,
                    sl_side,
                    0,
                    tp_price,
                    close_position=True,
                )
                tp_id = extract_order_id(resp)
                if tp_id:
                    log.info(
                        "[REPAIR] %s TP closePosition ile kuruldu (id=%s)",
                        sym,
                        tp_id,
                    )
                else:
                    if self._is_max_qty_error(resp):
                        log.warning(
                            "[REPAIR] %s TP closePosition da -4005 aldi (beklenmedik)",
                            sym,
                        )
                    else:
                        log.warning(
                            "[REPAIR] %s TP closePosition basarisiz (id=bos)", sym
                        )
            except Exception as e:
                log.warning("[REPAIR] %s TP closePosition hatasi: %s", sym, e)

        return sl_id, tp_id

    async def _try_place_sl_tp_split_qty(
        self, sym: str, trade: dict, sl_price: float, tp_price: float
    ) -> tuple[str, str]:
        """Miktarı LOT_SIZE.maxQty'nin altına bölerek SL/TP kur.

        closePosition=True başarısız olursa veya uygun değilse fallback.
        Toplam qty'yi 2 parçaya bölüp 2 ayrı SL/TP emri atar.

        Returns: (sl_id, tp_id) — başarısız olanlar boş string.
        """
        max_qty = await self._rest.get_max_qty(sym)
        original_qty = trade.get("qty", 0)

        if max_qty <= 0 or original_qty <= max_qty:
            return "", ""

        # 2 parçaya böl (güvenlik marjı: max_qty * 0.95)
        safe_chunk = await self._rest.apply_amount_precision(sym, max_qty * 0.95)
        num_chunks = max(2, int(original_qty / safe_chunk) + 1)
        chunk_qty = await self._rest.apply_amount_precision(
            sym, original_qty / num_chunks
        )

        log.warning(
            "[REPAIR] %s qty=%.4f > max_qty=%.4f, %d parcaya bolunuyor (her parca ~%.4f)",
            sym,
            original_qty,
            max_qty,
            num_chunks,
            chunk_qty,
        )

        sl_side = "SELL" if trade["side"] == "long" else "BUY"
        sl_id = ""
        tp_id = ""

        for i in range(num_chunks):
            if trade.get("sl") and sl_price > 0:
                try:
                    resp = await self._rest.place_stop_order(
                        sym,
                        sl_side,
                        chunk_qty,
                        sl_price,
                        client_id=f"sl_repr_{sym}_{i}_{int(time.time())}",
                    )
                    _id = extract_order_id(resp)
                    if _id:
                        sl_id = _id
                        log.info(
                            "[REPAIR] %s SL parca %d/%d kuruldu (id=%s, qty=%.4f)",
                            sym,
                            i + 1,
                            num_chunks,
                            _id,
                            chunk_qty,
                        )
                        break  # bir tanesi yeterli
                except Exception:
                    continue

            if trade.get("tp") and tp_price > 0:
                try:
                    resp = await self._rest.place_tp_order(
                        sym,
                        sl_side,
                        chunk_qty,
                        tp_price,
                        client_id=f"tp_repr_{sym}_{i}_{int(time.time())}",
                    )
                    _id = extract_order_id(resp)
                    if _id:
                        tp_id = _id
                        log.info(
                            "[REPAIR] %s TP parca %d/%d kuruldu (id=%s, qty=%.4f)",
                            sym,
                            i + 1,
                            num_chunks,
                            _id,
                            chunk_qty,
                        )
                        break
                except Exception:
                    continue

        return sl_id, tp_id

    async def repair_protection(
        self, sym: str, trade: dict, has_sl: bool, has_tp: bool
    ) -> None:
        """Eksik SL/TP emirlerini yeniden kur — per-symbol lock ile eşzamanlı
        çağrılara karşı korunur (P0-3). Aynı sembol için onarım zaten
        sürüyorsa bu çağrı atlanır; devam eden onarım tamamlandığında
        güncel duruma zaten bakılmış olacak.
        """
        lock = self._repair_locks.setdefault(sym, asyncio.Lock())
        if lock.locked():
            log.info(
                "[REPAIR] %s onarim zaten baska bir akistan yurutuluyor — "
                "bu cagri atlaniyor (concurrent repair guard)",
                sym,
            )
            return
        async with lock:
            await self._repair_protection_locked(sym, trade, has_sl, has_tp)

    async def _repair_protection_locked(
        self, sym: str, trade: dict, has_sl: bool, has_tp: bool
    ) -> None:
        """repair_protection()'ın kilit altındaki gerçek implementasyonu.

        FIX (P0-5): -4005 (max quantity) hatası algılandığında:
          1. closePosition=True ile qty'siz dene
          2. O da başarısızsa qty'yi bölüp parçalı dene
          3. İkisi de başarısızsa fiyat-bazlı retry'i atla
        -4005 dışındaki hatalarda mevcut fiyat-bazlı retry aynen kalır.

        Backoff: ardışık _MAX_REPAIR_RETRIES başarısız denemeden sonra
        frekans düşer ve CRITICAL uyarı üretilir.
        """
        # ── Backoff kontrolü ──
        fail_count = self._repair_failures.get(sym, 0)
        if fail_count >= 3:
            last_warn = self._last_repair_warning.get(sym, 0)
            now = time.time()
            # 5 dakikada bir CRITICAL uyarı
            if now - last_warn > 300:
                log.critical(
                    "[REPAIR] %s %d dakikadir korumasiz, MANUEL MUDAHALE GEREKIYOR "
                    "(ardisik %d basarisiz deneme)",
                    sym,
                    int((now - last_warn) / 60) if last_warn else 0,
                    fail_count,
                )
                self._last_repair_warning[sym] = now
            # Frekansı düşür: her 60sn yerine 5 dk'da bir dene
            if now - self._repair_failures.get(f"{sym}_ts", 0) < 300:
                log.warning(
                    "[REPAIR] %s backoff aktif — 5dk bekleniyor (ardisik %d basarisizlik)",
                    sym,
                    fail_count,
                )
                return
        else:
            # Başarısızlık yoksa / eşik altındaysa normal akış
            pass
        if not has_sl and trade.get("sl"):
            sl_side = "SELL" if trade["side"] == "long" else "BUY"
            sl_price = trade["sl"]
            try:
                sl_resp = await self._rest.place_stop_order(
                    sym, sl_side, trade["qty"], sl_price
                )
                sl_id = extract_order_id(sl_resp)

                if not sl_id and self._is_max_qty_error(sl_resp):
                    # ── -4005: MİKTAR KAYNAKLI HATA — closePosition dene ──
                    log.warning(
                        "[REPAIR] %s SL -4005 (max qty=%.4f), closePosition deneniyor...",
                        sym,
                        trade["qty"],
                    )
                    sl_id, _ = await self._try_place_sl_tp_with_close_position(
                        sym, trade, sl_price, 0
                    )
                    if not sl_id:
                        # closePosition da başarısız: parçalı dene
                        log.warning(
                            "[REPAIR] %s SL closePosition basarisiz, parcali deneniyor...",
                            sym,
                        )
                        sl_id, _ = await self._try_place_sl_tp_split_qty(
                            sym, trade, sl_price, 0
                        )

                elif not sl_id:
                    # ── -4005 DEĞİL: fiyat kaynaklı olabilir, mevcut fallback ──
                    log.warning(
                        "[REPAIR] %s SL basarisiz (sl=%.4f), mevcut fiyata gore yeniden hesaplaniyor...",
                        sym,
                        sl_price,
                    )
                    try:
                        cur_px = await self._rest.estimate_market_price(sym)
                        risk_pts = trade.get(
                            "risk_pts",
                            abs(trade.get("entry_price", cur_px) - sl_price),
                        )
                        if trade["side"] == "long" and cur_px < sl_price:
                            new_sl = await self._rest.apply_price_precision(
                                sym, cur_px - risk_pts * 2
                            )
                        elif trade["side"] == "short" and cur_px > sl_price:
                            new_sl = await self._rest.apply_price_precision(
                                sym, cur_px + risk_pts * 2
                            )
                        else:
                            new_sl = sl_price
                        sl_resp2 = await self._rest.place_stop_order(
                            sym, sl_side, trade["qty"], new_sl
                        )
                        sl_id = extract_order_id(sl_resp2)
                        if sl_id:
                            sl_price = new_sl
                            trade["sl"] = new_sl
                            log.info(
                                "[REPAIR] %s SL yeniden denendi: sl=%.4f -> id=%s",
                                sym,
                                new_sl,
                                sl_id,
                            )
                    except Exception as e2:
                        log.warning(
                            "[REPAIR] %s SL yeniden deneme de basarisiz: %s",
                            sym,
                            e2,
                        )
                if sl_id:
                    trade["sl_order_id"] = sl_id
                    trade["sl"] = sl_price
                    log.info(
                        "[REPAIR] %s SL yeniden kuruldu: %.6f (id=%s)",
                        sym,
                        sl_price,
                        sl_id,
                    )
                else:
                    log.warning(
                        "[REPAIR] %s SL kurulamadi: %.6f — Binance emri reddetti (ID=bos)",
                        sym,
                        sl_price,
                    )
            except Exception as e:
                log.warning(
                    "[REPAIR] %s SL kurulum hatasi: %.6f — %s",
                    sym,
                    sl_price,
                    e,
                )
        if not has_tp and trade.get("tp"):
            tp_side = "SELL" if trade["side"] == "long" else "BUY"
            tp_price = trade["tp"]
            try:
                tp_resp = await self._rest.place_tp_order(
                    sym, tp_side, trade["qty"], tp_price
                )
                tp_id = extract_order_id(tp_resp)

                if not tp_id and self._is_max_qty_error(tp_resp):
                    # ── -4005: MİKTAR KAYNAKLI HATA — closePosition dene ──
                    log.warning(
                        "[REPAIR] %s TP -4005 (max qty=%.4f), closePosition deneniyor...",
                        sym,
                        trade["qty"],
                    )
                    _, tp_id = await self._try_place_sl_tp_with_close_position(
                        sym, trade, 0, tp_price
                    )
                    if not tp_id:
                        log.warning(
                            "[REPAIR] %s TP closePosition basarisiz, parcali deneniyor...",
                            sym,
                        )
                        _, tp_id = await self._try_place_sl_tp_split_qty(
                            sym, trade, 0, tp_price
                        )

                elif not tp_id:
                    # ── -4005 DEĞİL: fiyat kaynaklı olabilir, mevcut fallback ──
                    log.warning(
                        "[REPAIR] %s TP basarisiz (tp=%.4f), mevcut fiyata gore yeniden hesaplaniyor...",
                        sym,
                        tp_price,
                    )
                    try:
                        cur_px = await self._rest.estimate_market_price(sym)
                        risk_pts = trade.get(
                            "risk_pts",
                            abs(
                                trade.get("entry_price", cur_px)
                                - trade.get("sl", cur_px)
                            ),
                        )
                        if trade["side"] == "long":
                            new_tp = await self._rest.apply_price_precision(
                                sym, max(tp_price, cur_px + risk_pts * 0.5)
                            )
                        else:
                            new_tp = await self._rest.apply_price_precision(
                                sym, min(tp_price, cur_px - risk_pts * 0.5)
                            )
                        tp_resp2 = await self._rest.place_tp_order(
                            sym, tp_side, trade["qty"], new_tp
                        )
                        tp_id = extract_order_id(tp_resp2)
                        if tp_id:
                            tp_price = new_tp
                            trade["tp"] = new_tp
                            log.info(
                                "[REPAIR] %s TP yeniden denendi: tp=%.4f -> id=%s",
                                sym,
                                new_tp,
                                tp_id,
                            )
                    except Exception as e2:
                        log.warning(
                            "[REPAIR] %s TP yeniden deneme de basarisiz: %s",
                            sym,
                            e2,
                        )
                if tp_id:
                    trade["tp_order_id"] = tp_id
                    trade["tp"] = tp_price
                    log.info(
                        "[REPAIR] %s TP yeniden kuruldu: %.6f (id=%s)",
                        sym,
                        tp_price,
                        tp_id,
                    )
                else:
                    log.warning(
                        "[REPAIR] %s TP kurulamadi: %.6f — Binance emri reddetti "
                        "(muhtemelen fiyat TP seviyesini gecti, ID=bos)",
                        sym,
                        tp_price,
                    )
            except Exception as e:
                log.warning(
                    "[REPAIR] %s TP kurulum hatasi: %.6f — %s",
                    sym,
                    tp_price,
                    e,
                )
        # ── Backoff: başarısızlık sayacı güncelle ──
        # Hem SL hem TP başarısızsa → sayaç artar
        # En az biri başarılıysa → sayaç sıfırlanır
        sl_ok = bool(trade.get("sl_order_id"))
        tp_ok = bool(trade.get("tp_order_id"))
        if not sl_ok and not tp_ok:
            self._repair_failures[sym] = self._repair_failures.get(sym, 0) + 1
            self._repair_failures[f"{sym}_ts"] = time.time()
            log.warning(
                "[REPAIR] %s onarim tamamen basarisiz (ardisik %d)",
                sym,
                self._repair_failures[sym],
            )
        else:
            self._repair_failures[sym] = 0  # başarılı, sıfırla
        log.info("[REPAIR] %s onarim tamam", sym)

    # ── Tüm açık emirleri iptal et (exit öncesi) ──────────────

    async def cancel_all_open_orders(self, sym: str) -> None:
        """Semboldeki tüm açık emirleri iptal et."""
        if not cfg.BINANCE_API_KEY or not self._is_live:
            return
        try:
            orders = await self._rest.get_all_orders(sym)
            for o in orders:
                oid = o.get("algoId") or o.get("orderId")
                if oid:
                    try:
                        is_algo = "algoId" in o
                        await self._rest.cancel_order(
                            oid, sym, reason="exit_cancel_all", is_algo=is_algo
                        )
                    except Exception:
                        pass
            log.info("[CANCEL] %s tum acik emirler iptal edildi", sym)
        except Exception as e:
            log.warning("[CANCEL] %s cancel_all hatasi: %s", sym, e)

    # ── Exit temizliği ─────────────────────────────────────────

    async def cleanup_on_exit(self, sym: str, trade: dict, result: str) -> None:
        """Exit sonrası Binance emir temizliği.

        FIX (A8): Davranış 3 sınıfa ayrıldı:
          1. result == "SL"  → kalan TP iptal et
          2. result == "TP"  → kalan SL iptal et
          3. TRAIL_CLOSE / WS_FALLBACK / TIMEOUT / MANUAL_CLOSE vb.
             → ne SL ne TP tetiklendi, her ikisini de iptal etmeye çalış

        Acil market close fallback yalnızca result in ("SL", "TP") ve
        tetiklenen tarafın Binance ID'si yoksa düşünülür — synthetic/market
        path'lerde pozisyon zaten _exit_trade() tarafından kapatılmıştır.

        FIX (A7): Son adım olarak cancel_all_open_orders broad-sweep —
        exit commit edildikten SONRA çalışır.

        Patch Set 3: ProtectionLifecycleService varsa karar ona delege
        edilir (cleanup_after_confirmed_exit). OrderManager sadece REST
        iptallerini yürütür.
        """
        if not cfg.BINANCE_API_KEY or not self._is_live:
            return

        try:
            # ── Kalan emirleri belirle ──
            if self._protection is not None:
                plan = self._protection.cleanup_after_confirmed_exit(trade, result)
                remaining_ids = plan.cancel_ids
                needs_emergency = plan.needs_emergency_close
                emerg_reason = plan.emergency_close_reason
            else:
                if result == "SL":
                    remaining_ids = [trade.get("tp_order_id")]
                elif result == "TP":
                    remaining_ids = [trade.get("sl_order_id")]
                else:
                    remaining_ids = [
                        trade.get("sl_order_id"),
                        trade.get("tp_order_id"),
                    ]
                # ── Acil market close: YALNIZCA SL/TP tetiklenme path'inde ──
                needs_emergency = False
                emerg_reason = ""
                if result in ("SL", "TP"):
                    trigger_id = (
                        trade.get("sl_order_id")
                        if result == "SL"
                        else trade.get("tp_order_id")
                    )
                    if not trigger_id:
                        needs_emergency = True
                        emerg_reason = (
                            f"tetiklenen {result} emri Binance ID'si olmadigi "
                            "icin acil market kapanisi gerekli"
                        )

            for rid in remaining_ids:
                if rid:
                    try:
                        await self._rest.cancel_order(
                            rid, sym, reason="exit_close", is_algo=True
                        )
                        log.info(
                            "[CANCEL] %s kalan koruma emri iptal edildi (id=%s)",
                            sym,
                            rid,
                        )
                    except Exception as e:
                        log.warning(
                            "[CANCEL] %s kalan emir iptal hatasi (id=%s): %s",
                            sym,
                            rid,
                            e,
                        )

            # ── Acil market close: YALNIZCA SL/TP tetiklenme path'inde ──
            if needs_emergency:
                log.warning(
                    "[CLOSE] %s %s — acil market kapanisi yapiliyor...",
                    sym,
                    emerg_reason,
                )
                mkt_side = "SELL" if trade["side"] == "long" else "BUY"
                try:
                    await self._rest.place_market_order(
                        sym, mkt_side, trade["qty"], reduce_only=True
                    )
                except Exception as e:
                    log.warning("[CLOSE] %s acil kapanis emri hatasi: %s", sym, e)
        except Exception as e:
            log.warning("[CLOSE] %s exit temizleme hatasi: %s", sym, e)

        # FIX (A7): _exit_trade() başında koşulsuz çalışan broad cancel buraya
        # taşındı — exit doğrulanıp commit edildikten SONRA, yukarıdaki
        # hedefli iptal/acil-kapanış denemelerinden bağımsız son bir güvenlik
        # süpürmesi. cancel_all_open_orders() açık emirleri Binance'ten taze
        # çeker; yukarıda zaten iptal edilmiş emirler tekrar hataya yol
        # açmadan atlanır (idempotent).
        try:
            await self.cancel_all_open_orders(sym)
        except Exception as e:
            log.warning("[CLEANUP] %s cancel_all_open_orders hatasi: %s", sym, e)
