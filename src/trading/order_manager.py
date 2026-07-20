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

import logging
import time
from typing import TYPE_CHECKING

import config as cfg
from bot_infra import extract_order_id
from event_log import log_event
from models import UNRESTRICTED_STATUSES

if TYPE_CHECKING:
    from trading.protection_lifecycle import ProtectionLifecycleService

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

        new_sl = await self._rest.apply_price_precision(sym, new_sl)
        new_tp = await self._rest.apply_price_precision(sym, new_tp)

        sl_side = "SELL" if trade["side"] == "long" else "BUY"
        qty = trade.get("qty", trade.get("lot", 0))

        old_sl_id = trade.get("sl_order_id", "")
        old_tp_id = trade.get("tp_order_id", "")

        new_sl_id = ""
        new_tp_id = ""
        sl_ok = False
        tp_ok = False

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

        # ── 2. YENİ TP EMRİNİ AT ──
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
            log.warning("[TRAIL] %s TP place hatasi: %s -> eski TP korunuyor", sym, e)

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
                return False

        log.info(
            "[ORDER] %s trailing guncellendi sl=%.2f (id=%s) tp=%.2f (id=%s)",
            sym,
            trade.get("sl", 0.0),
            new_sl_id,
            trade.get("tp", 0.0),
            new_tp_id,
        )
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

    async def verify_protection(self, sym: str, trade: dict) -> tuple[bool, bool]:
        """Binance'teki açık emirleri sorgulayıp sl_order_id / tp_order_id'nin
        gerçekten hâlâ açık olup olmadığını döndürür: (sl_present, tp_present).

        ProtectionLifecycleService varsa karar ona delege edilir.
        REST sorgusu başarısız olursa fail-safe: ikisini de True varsayar
        (yani "dokunma", çağıran taraf yanlışlıkla cancel/exit tetiklemesin).
        """
        if self._protection is not None:
            open_ids = await self.get_open_order_ids(sym)
            if not open_ids:
                return True, True
            result = self._protection.verify(trade, open_ids)
            return result.sl_present, result.tp_present

        s_id = str(trade.get("sl_order_id", ""))
        t_id = str(trade.get("tp_order_id", ""))
        expects_sl = bool(trade.get("sl"))
        expects_tp = bool(trade.get("tp"))
        try:
            orders = await self._rest.get_all_orders(sym)
            open_ids = {str(o.get("algoId") or o.get("orderId") or "") for o in orders}
            sl_present = (not expects_sl) or (bool(s_id) and s_id in open_ids)
            tp_present = (not expects_tp) or (bool(t_id) and t_id in open_ids)
            return sl_present, tp_present
        except Exception as e:
            log.warning(
                "[VERIFY] %s acik emir sorgu hatasi: %s -> fail-safe (dokunma)",
                sym,
                e,
            )
            return True, True

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

    async def repair_protection(
        self, sym: str, trade: dict, has_sl: bool, has_tp: bool
    ) -> None:
        """Eksik SL/TP emirlerini yeniden kur.

        Orijinal _repair_protection() ile birebir aynı mantık.
        Sadece _register_user_data_callbacks() içindeki WS callback'ten çağrılır.
        """
        if not has_sl and trade.get("sl"):
            sl_side = "SELL" if trade["side"] == "long" else "BUY"
            sl_resp = await self._rest.place_stop_order(
                sym, sl_side, trade["qty"], trade["sl"]
            )
            trade["sl_order_id"] = extract_order_id(sl_resp)
            log.info(
                "[REPAIR] %s SL yeniden kuruldu: %.2f (id=%s)",
                sym,
                trade["sl"],
                trade["sl_order_id"],
            )
        if not has_tp and trade.get("tp"):
            tp_side = "SELL" if trade["side"] == "long" else "BUY"
            tp_resp = await self._rest.place_tp_order(
                sym, tp_side, trade["qty"], trade["tp"]
            )
            trade["tp_order_id"] = extract_order_id(tp_resp)
            log.info(
                "[REPAIR] %s TP yeniden kuruldu: %.2f (id=%s)",
                sym,
                trade["tp"],
                trade["tp_order_id"],
            )
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
