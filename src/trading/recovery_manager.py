"""
recovery_manager.py — Binance pozisyon kurtarma + ghost temizliği.

PaperTrader._recover_positions() ve _reconcile_ghost_positions()
metodlarını kapsar. Sadece run() başlangıcında çağrılır.

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - extract_order_id, cfg import'ları korunur
  - _pl() formatı birebir aynı (pl_callback üzerinden)

Patch Set 3: _known_protection_ids() ve should_skip_reconcile()
ProtectionLifecycleService'e delege edilir (varsa).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import config as cfg
from bot_binance import MaxQtyUnavailableError
from bot_infra import _fmt_price, extract_order_id
from event_log import log_event
from models import (
    INCIDENT_POSITION_OPEN_BUT_STATE_MISSING,
    ActiveTrade,
    STATUS_ACTIVE,
    UNRESTRICTED_STATUSES,
)

if TYPE_CHECKING:
    from trading.protection_lifecycle import ProtectionLifecycleService

log = logging.getLogger("sniper.recovery_manager")


class RecoveryManager:
    """Binance pozisyon kurtarma + ghost temizliği.

    PaperTrader'dan DI ile alır:
      - rest_client: BinanceRESTClient
      - symbols: list[str] — takip edilen semboller
      - cfgs: dict[sym, dict] — sembol konfigürasyonları
      - states: dict[sym, SessionState] — session durumları
      - active_trades: dict[sym, ActiveTrade] — aktif trade'ler
      - pl_callback: callable(sym, key, msg) — _pl() delegesi
      - order_manager: OrderManager — had_immediately_trigger() son 1 saatte
        -2021 reject kaydi olup olmadigini sorgulamak icin kullanilir
      - atr_state: dict[sym, float] — sembol bazlı gerçek Wilder's ATR
      - protection_service: ProtectionLifecycleService | None —
        policy kararlari icin (None ise eski inline logic korunur)
    """

    def __init__(
        self,
        rest_client,
        symbols: list[str],
        cfgs: dict,
        states: dict,
        active_trades: dict,
        pl_callback,
        order_manager=None,
        atr_state: dict | None = None,
        protection_service: "ProtectionLifecycleService | None" = None,
    ):
        self._rest = rest_client
        self._symbols = symbols
        self._cfgs = cfgs
        self._states = states
        self._active_trades = active_trades
        self._pl = pl_callback
        self._order_manager = order_manager
        self._atr_state = atr_state or {}
        self._protection = protection_service
        self._ghost_fail_count = 0

    # ── Pozisyon kurtarma ──────────────────────────────────────

    async def _dedupe_protection_orders(
        self, sym: str, sl_orders: list[dict], tp_orders: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """Borsada ayni pozisyon icin birikmis fazla SL/TP emirlerini iptal
        eder, en yenisini dondurur (DOGEUSDT cift emir kazasi fix'i).
        recover_positions'da protection_orders doldurulmadan ONCE calisir —
        "hangi cifti tutacagiz" karari stale ID'lerle karismasin."""

        def _newest(orders: list[dict]) -> dict:
            def _oid(o: dict) -> int:
                try:
                    return int(extract_order_id(o) or 0)
                except (TypeError, ValueError):
                    return 0

            return max(orders, key=_oid)

        async def _cancel_except(orders: list[dict], keep: dict) -> None:
            keep_id = str(extract_order_id(keep) or "")
            for o in orders:
                oid = str(extract_order_id(o) or "")
                if oid and oid != keep_id:
                    try:
                        await self._rest.cancel_order(oid, sym)
                        log.info("[RECOVER] %s fazla koruma emri iptal: %s", sym, oid)
                    except Exception as e:
                        log.warning(
                            "[RECOVER] %s koruma emri iptal hatasi %s: %s", sym, oid, e
                        )

        sl_keep = sl_orders
        tp_keep = tp_orders
        if len(sl_orders) > 1:
            sl_keep = [_newest(sl_orders)]
            await _cancel_except(sl_orders, sl_keep[0])
        if len(tp_orders) > 1:
            tp_keep = [_newest(tp_orders)]
            await _cancel_except(tp_orders, tp_keep[0])
        return sl_keep, tp_keep

    async def recover_positions(self, quiet: bool = False) -> None:
        """Binance'deki açık pozisyonları tara, SL/TP varsa envantere al,
        yoksa yeni koruma emri kur.

        Args:
            quiet: True ise _pl() konsol mesaji atlanir (periyodik cagri).
        """
        if not cfg.BINANCE_API_KEY:
            return
        try:
            positions = await self._rest.get_positions()
            if not positions:
                if not quiet:
                    self._pl("SYSTEM", "recover", "\u2705 API'de acik pozisyon yok")
                return

            if not quiet:
                self._pl(
                    "SYSTEM",
                    "recover",
                    f"\U0001f504 {len(positions)} pozisyon bulundu, envantere aliniyor...",
                )
            for pos in positions:
                sym = pos["symbol"]
                if sym not in self._symbols:
                    continue
                amt = float(pos.get("positionAmt", 0))
                direction = "long" if amt > 0 else "short"
                entry = float(pos.get("entryPrice", 0))

                # FIX (tick_size): recover edilen trade'ler _try_entry'deki gibi
                # gerçek tick_size ile kurulmalı. models.ActiveTrade default'u
                # (0.10) recovery'de hiç set edilmezse kullanılır ve trailing
                # normalize (ROUND_CEILING) her iyileşmeyi yutar — 170 recovered
                # trade'de trailing tamamen kilitlendi (bkz. ALGO/RENDER).
                tick_size = 0.10
                try:
                    tick_size = await self._rest.get_tick_size(sym)
                except Exception:
                    log.warning("[RECOVER] %s tick_size alinamadi (0.10 fallback)", sym)

                open_orders = await self._rest.get_all_orders(sym)
                sl_orders = [
                    o
                    for o in open_orders
                    if self._rest.get_order_type(o)
                    in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                    and (
                        o.get("reduceOnly") in (True, "true", "True")
                        or o.get("closePosition") in (True, "true", "True")
                    )
                ]
                tp_orders = [
                    o
                    for o in open_orders
                    if self._rest.get_order_type(o)
                    in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                    and (
                        o.get("reduceOnly") in (True, "true", "True")
                        or o.get("closePosition") in (True, "true", "True")
                    )
                ]

                existing = self._active_trades.get(sym)
                if sl_orders and tp_orders:
                    # Fix C: dedupe ONCE protection_orders'a dokunulur —
                    # fazla (cift) SL/TP emirleri en yeni kalacak sekilde
                    # iptal edilir, sonra state'e yazilir.
                    sl_orders, tp_orders = await self._dedupe_protection_orders(
                        sym, sl_orders, tp_orders
                    )
                    sl_price = self._rest.get_order_price(sl_orders[0])
                    tp_price = self._rest.get_order_price(tp_orders[0])
                    risk_pts = abs(entry - sl_price)
                    sl_id = extract_order_id(sl_orders[0])
                    tp_id = extract_order_id(tp_orders[0])
                    # Fix B: protection_orders gercek borsa tipi ile doldurulur.
                    # Daha once sadece flat sl_order_id/tp_order_id yaziliyordu;
                    # restore sonrasi trail replace_protection eski emri
                    # protection_orders'tan bulamayip iptal edemiyordu.
                    protection_orders = {
                        "sl": {
                            "order_id": str(sl_id),
                            "stop_price": float(sl_price),
                            "type": self._rest.get_order_type(sl_orders[0]),
                        },
                        "tp": {
                            "order_id": str(tp_id),
                            "stop_price": float(tp_price),
                            "type": self._rest.get_order_type(tp_orders[0]),
                        },
                    }
                    if existing:
                        existing["sl"] = sl_price
                        existing["tp"] = tp_price
                        existing["sl_order_id"] = sl_id
                        existing["tp_order_id"] = tp_id
                        existing["protection_orders"] = protection_orders
                        existing["risk_pts"] = risk_pts
                        existing["tick_size"] = tick_size
                        # A1-01 Fix: Sync runtime.protection for existing trade
                        if self._order_manager:
                            self._order_manager._sync_runtime_protection(
                                existing, "sl_current", sl_id, sl_price
                            )
                            self._order_manager._sync_runtime_protection(
                                existing, "tp_current", tp_id, tp_price
                            )
                    else:
                        new_trade = ActiveTrade(
                            symbol=sym,
                            entry_bar_index=0,
                            entry_price=entry,
                            entry_timestamp=int(time.time() * 1000),
                            sl=sl_price,
                            tp=tp_price,
                            qty=abs(amt),
                            side=direction,
                            status=STATUS_ACTIVE,
                            trigger_fvg=None,
                            initial_sl=sl_price,
                            initial_tp=tp_price,
                            trailing_count=0,
                            trail_count=0,
                            risk_pts=risk_pts,
                            is_recovered=True,
                            trail_mode="fvg",
                            tick_size=tick_size,
                            sl_order_id=sl_id,
                            tp_order_id=tp_id,
                            protection_orders=protection_orders,
                        )
                        self._active_trades[sym] = new_trade
                        # A1-01 Fix: Sync runtime.protection for new trade
                        if self._order_manager:
                            self._order_manager._sync_runtime_protection(
                                new_trade, "sl_current", sl_id, sl_price
                            )
                            self._order_manager._sync_runtime_protection(
                                new_trade, "tp_current", tp_id, tp_price
                            )
                    if not quiet:
                        self._pl(
                            sym,
                            "recover",
                            f"🔒 {direction.upper()} @ {_fmt_price(entry)} | SL={_fmt_price(sl_price)} TP={_fmt_price(tp_price)} | yeni trade engellendi",
                        )
                else:
                    # Hedefli guard: local trade exit akisinda ise koruma kurma —
                    # exit lifecycle pozisyonu zaten yonetiyor. -2021 reject kaydi
                    # varsa pozisyon dolmus olabilir (WS FILLED gecikmeli).
                    if (
                        existing is not None
                        and existing.get("status") not in UNRESTRICTED_STATUSES
                    ):
                        if not quiet:
                            self._pl(
                                sym,
                                "recover",
                                f"local status={existing.get('status')} — koruma kurulumu atlandi (exit akisi yonetiyor)",
                            )
                        continue
                    if (
                        self._order_manager is not None
                        and hasattr(self._order_manager, "had_immediately_trigger")
                        and self._order_manager.had_immediately_trigger(sym)
                    ):
                        if not quiet:
                            self._pl(
                                sym,
                                "recover",
                                "-2021 reject kaydi var — koruma kurulumu atlandi (WS FILLED bekleniyor)",
                            )
                        continue
                    if not quiet:
                        self._pl(
                            sym,
                            "recover",
                            f"[{INCIDENT_POSITION_OPEN_BUT_STATE_MISSING}] ⚠️ {direction.upper()} @ {_fmt_price(entry)} | SL/TP bulunamadi (pozisyon korumasiz)",
                        )
                    # Gercek ATR varsa kullan. Yoksa DEFAULT_ATR_FALLBACK_PCT (0.01%)
                    # KULLANMA: SL/TP giris fiyatina yapisir, Binance "immediately
                    # trigger" hatasiyla reddeder ve pozisyon sessizce korumasiz kalir.
                    # Bunun yerine ayri, gercekci bir acil durum mesafesi kullan.
                    real_atr = self._atr_state.get(sym, 0.0)
                    if real_atr > 0:
                        atr_est = real_atr
                        risk_pts = atr_est * self._cfgs[sym]["SL_ATR_MULT"]
                    else:
                        risk_pts = entry * cfg.RECOVERY_SL_FALLBACK_PCT
                    if direction == "long":
                        sl = entry - risk_pts * 2
                        tp = entry + risk_pts * self._cfgs[sym]["TP_RR"]
                    else:
                        sl = entry + risk_pts * 2
                        tp = entry - risk_pts * self._cfgs[sym]["TP_RR"]

                    def _is_max_qty_error(resp: dict) -> bool:
                        return resp.get("_error_code") == "-4005"

                    async def _try_close_position_sl_tp(
                        s: str, d: str, sl_px: float, tp_px: float
                    ) -> tuple[str, str]:
                        """closePosition=True ile SL/TP dene."""
                        _sl_side = "SELL" if d == "long" else "BUY"
                        _sl_id = ""
                        _tp_id = ""
                        if sl_px > 0:
                            r = await self._rest.place_stop_order(
                                s, _sl_side, 0, sl_px, close_position=True
                            )
                            _sl_id = extract_order_id(r)
                        if tp_px > 0:
                            r = await self._rest.place_tp_order(
                                s, _sl_side, 0, tp_px, close_position=True
                            )
                            _tp_id = extract_order_id(r)
                        return _sl_id, _tp_id

                    async def _try_split_qty_sl_tp(
                        s: str, d: str, sl_px: float, tp_px: float, total_qty: float
                    ) -> tuple[str, str]:
                        """Miktarı bölerek SL/TP dene."""
                        try:
                            max_qty = await self._rest.get_max_qty(s)
                        except MaxQtyUnavailableError:
                            log.warning(
                                "[RECOVER] %s max_qty alinamadi (fiyat yok) — parcali SL/TP atlanir",
                                s,
                            )
                            return "", ""
                        if max_qty <= 0 or total_qty <= max_qty:
                            return "", ""
                        safe_chunk = await self._rest.apply_amount_precision(
                            s, max_qty * 0.95
                        )
                        num_chunks = max(2, int(total_qty / safe_chunk) + 1)
                        chunk_qty = await self._rest.apply_amount_precision(
                            s, total_qty / num_chunks
                        )
                        _sl_side = "SELL" if d == "long" else "BUY"
                        _sl_id = ""
                        _tp_id = ""
                        for i in range(num_chunks):
                            if sl_px > 0 and not _sl_id:
                                r = await self._rest.place_stop_order(
                                    s, _sl_side, chunk_qty, sl_px
                                )
                                _sl_id = extract_order_id(r)
                            if tp_px > 0 and not _tp_id:
                                r = await self._rest.place_tp_order(
                                    s, _sl_side, chunk_qty, tp_px
                                )
                                _tp_id = extract_order_id(r)
                            if _sl_id and _tp_id:
                                break
                        return _sl_id, _tp_id

                    sl_id = ""
                    tp_id = ""
                    if cfg.BINANCE_API_KEY:
                        try:
                            sl_side = "SELL" if direction == "long" else "BUY"
                            rounded_sl = await self._rest.apply_price_precision(sym, sl)
                            rounded_tp = await self._rest.apply_price_precision(sym, tp)

                            sl_resp = await self._rest.place_stop_order(
                                sym, sl_side, abs(amt), rounded_sl
                            )
                            sl_id = extract_order_id(sl_resp)

                            # ── SL -4005 kontrolü ──
                            if not sl_id and _is_max_qty_error(sl_resp):
                                log.warning(
                                    "[RECOVER] %s SL -4005 (max qty=%.4f), closePosition deneniyor...",
                                    sym,
                                    abs(amt),
                                )
                                sl_id, tp_id = await _try_close_position_sl_tp(
                                    sym, direction, rounded_sl, 0
                                )
                                if not sl_id:
                                    log.warning(
                                        "[RECOVER] %s SL closePosition basarisiz, parcali deneniyor...",
                                        sym,
                                    )
                                    sl_id, _ = await _try_split_qty_sl_tp(
                                        sym, direction, rounded_sl, 0, abs(amt)
                                    )

                            elif not sl_id:
                                # Fiyat kaynaklı: mevcut fiyata gore yeni SL dene
                                log.warning(
                                    "[RECOVER] %s SL basarisiz (sl=%.4f), mevcut fiyata gore yeniden hesaplaniyor...",
                                    sym,
                                    sl,
                                )
                                try:
                                    cur_px = await self._rest.estimate_market_price(sym)
                                    if direction == "long" and cur_px < sl:
                                        new_sl = await self._rest.apply_price_precision(
                                            sym, cur_px * 0.97
                                        )
                                    elif direction == "short" and cur_px > sl:
                                        new_sl = await self._rest.apply_price_precision(
                                            sym, cur_px * 1.03
                                        )
                                    else:
                                        new_sl = rounded_sl
                                    sl_resp2 = await self._rest.place_stop_order(
                                        sym, sl_side, abs(amt), new_sl
                                    )
                                    sl_id2 = extract_order_id(sl_resp2)
                                    if sl_id2:
                                        sl_id = sl_id2
                                        sl = new_sl
                                        log.info(
                                            "[RECOVER] %s SL yeniden denendi: sl=%.4f -> id=%s",
                                            sym,
                                            new_sl,
                                            sl_id,
                                        )
                                except Exception as e2:
                                    log.warning(
                                        "[RECOVER] %s SL yeniden deneme de basarisiz: %s",
                                        sym,
                                        e2,
                                    )

                            # ── TP -4005 kontrolü ──
                            if not tp_id:
                                tp_resp = await self._rest.place_tp_order(
                                    sym, sl_side, abs(amt), rounded_tp
                                )
                                tp_id = extract_order_id(tp_resp)

                                if not tp_id and _is_max_qty_error(tp_resp):
                                    log.warning(
                                        "[RECOVER] %s TP -4005 (max qty=%.4f), closePosition deneniyor...",
                                        sym,
                                        abs(amt),
                                    )
                                    _, tp_id = await _try_close_position_sl_tp(
                                        sym, direction, 0, rounded_tp
                                    )
                                    if not tp_id:
                                        log.warning(
                                            "[RECOVER] %s TP closePosition basarisiz, parcali deneniyor...",
                                            sym,
                                        )
                                        _, tp_id = await _try_split_qty_sl_tp(
                                            sym, direction, 0, rounded_tp, abs(amt)
                                        )

                                elif not tp_id:
                                    # Fiyat kaynaklı: mevcut fiyata gore yeni TP dene
                                    log.warning(
                                        "[RECOVER] %s TP basarisiz (tp=%.4f), mevcut fiyata gore yeniden hesaplaniyor...",
                                        sym,
                                        tp,
                                    )
                                    try:
                                        cur_px = await self._rest.estimate_market_price(
                                            sym
                                        )
                                        if direction == "long":
                                            new_tp = (
                                                await self._rest.apply_price_precision(
                                                    sym,
                                                    max(rounded_tp, cur_px * 1.01),
                                                )
                                            )
                                        else:
                                            new_tp = (
                                                await self._rest.apply_price_precision(
                                                    sym,
                                                    min(rounded_tp, cur_px * 0.99),
                                                )
                                            )
                                        tp_resp2 = await self._rest.place_tp_order(
                                            sym, sl_side, abs(amt), new_tp
                                        )
                                        tp_id2 = extract_order_id(tp_resp2)
                                        if tp_id2:
                                            tp_id = tp_id2
                                            tp = new_tp
                                            log.info(
                                                "[RECOVER] %s TP yeniden denendi: tp=%.4f -> id=%s",
                                                sym,
                                                new_tp,
                                                tp_id,
                                            )
                                    except Exception as e2:
                                        log.warning(
                                            "[RECOVER] %s TP yeniden deneme de basarisiz: %s",
                                            sym,
                                            e2,
                                        )

                            log.info(
                                "[RECOVER] %s icin Binance uzerinde SL/TP emirleri olusturuldu (sl_id=%s, tp_id=%s)",
                                sym,
                                sl_id,
                                tp_id,
                            )
                        except Exception as e:
                            log.warning(
                                "[RECOVER] %s icin Binance koruma emri yerlestirme hatasi: %s",
                                sym,
                                e,
                            )

                    if not sl_id:
                        # SL hicbir sekilde kurulamadi. Pozisyonu "korumali" gibi
                        # envantere alip yoluna devam ETME — acil market kapanisi yap.
                        log.critical(
                            "[RECOVER] %s SL hicbir sekilde kurulamadi -- pozisyon "
                            "korumasiz kalmasin diye ACIL MARKET KAPANISI yapiliyor (qty=%.6f)",
                            sym,
                            abs(amt),
                        )
                        self._pl(
                            sym,
                            "recover_emergency_close",
                            f"🚨 {direction.upper()} @ {_fmt_price(entry)} | SL kurulamadi -> ACIL KAPANIS tetiklendi",
                        )
                        close_result = None
                        close_error = None
                        try:
                            close_side = "SELL" if direction == "long" else "BUY"
                            # P0-5: CB bypass'li acil kapanis
                            close_result = await self._rest.place_market_order_priority(
                                sym,
                                close_side,
                                abs(amt),
                                reduce_only=True,
                                client_order_id=f"recover-{sym.lower()}-{int(time.time() * 1000)}",
                            )
                        except Exception as e:
                            close_error = str(e)

                        if not close_result or not close_result.get("orderId"):
                            # A4-04 Fix: verify position status before marking as failed
                            try:
                                positions = await self._rest.get_positions()
                                pos = next(
                                    (p for p in positions if p["symbol"] == sym), None
                                )
                                if pos and float(pos.get("positionAmt", 0)) == 0:
                                    log.info(
                                        "[RECOVER] %s market close basarili (positionAmt=0)",
                                        sym,
                                    )
                                    close_result = {
                                        "_status": "EXECUTION_CONFIRMED",
                                        "orderId": "verified",
                                    }
                                else:
                                    log.warning(
                                        "[RECOVER] %s market close basarisiz, closePosition deneniyor...",
                                        sym,
                                    )
                                    try:
                                        # force close zaten _emergency_post kullanir (CB bypass)
                                        forced = (
                                            await self._rest.place_force_close_order(
                                                sym, close_side, direction
                                            )
                                        )
                                        if forced:
                                            log.info(
                                                "[RECOVER] %s closePosition kabul edildi (CB bypass)",
                                                sym,
                                            )
                                            close_result = {"closePosition": True}
                                    except Exception as e2:
                                        close_error = (
                                            f"{close_error or ''} + closePosition: {e2}"
                                        )
                            except Exception as e:
                                log.warning(
                                    "[RECOVER] %s market close dogrulama hatasi: %s",
                                    sym,
                                    e,
                                )

                        if close_result:
                            if tp_id:
                                try:
                                    await self._rest.cancel_order(
                                        tp_id,
                                        sym,
                                        reason="recover_emergency_close",
                                        is_algo=True,
                                    )
                                except Exception as e:
                                    log.error(
                                        "[RECOVER] %s TP iptal hatasi (id=%s): %s — "
                                        "retry denecek, koruma emri aktif olabilir",
                                        sym,
                                        tp_id,
                                        e,
                                    )
                                    for _attempt in range(1):
                                        try:
                                            await asyncio.sleep(0.5)
                                            await self._rest.cancel_order(
                                                tp_id,
                                                sym,
                                                reason="recover_emergency_close_retry",
                                                is_algo=True,
                                            )
                                            log.info(
                                                "[RECOVER] %s TP retry iptal OK (id=%s)",
                                                sym,
                                                tp_id,
                                            )
                                            break
                                        except Exception as e2:
                                            log.critical(
                                                "[RECOVER] %s TP iptal retry de BASARISIZ "
                                                "(id=%s): %s — MANUEL MUDAHALE GEREKLI",
                                                sym,
                                                tp_id,
                                                e2,
                                            )
                                            if not quiet:
                                                self._pl(
                                                    sym,
                                                    "recovery_tp_cancel_failed",
                                                    f"TP cancel retry failed: {e2}",
                                                )
                                    continue

                        # place_market_order basarisiz: ya exception atti ya da
                        # {} dondu (minQty/minNotional/POST hatasi -- exception
                        # ATMAZ). Ikisinde de pozisyon Binance'de hala acik
                        # olabilir. "kapandi" varsayip continue ETME --
                        # pozisyonu (korumasiz da olsa) active_trades'e alip
                        # state'te birak ki ghost/orphan taramasi ve bir
                        # sonraki recover_positions() dongusu bunu tekrar
                        # yakalayabilsin.
                        reason = close_error or "place_market_order bos dict ({}) dondu"
                        log.critical(
                            "[RECOVER] %s ACIL KAPANIS BASARISIZ -- MANUEL MUDAHALE GEREKLI: %s",
                            sym,
                            reason,
                        )
                        if not quiet:
                            self._pl(
                                sym,
                                "recover_emergency_close_failed",
                                f"\U0001f6a8\U0001f6a8 {sym}: ACIL KAPANIS BASARISIZ -- HEMEN MANUEL KONTROL ET: {reason}",
                            )
                        if existing:
                            existing["sl"] = sl
                            existing["tp"] = tp
                            existing["sl_order_id"] = ""
                            existing["tp_order_id"] = tp_id
                            existing["tick_size"] = tick_size
                            # A1-01 Fix: Sync runtime.protection for existing trade
                            if self._order_manager:
                                self._order_manager._sync_runtime_protection(
                                    existing, "sl_current", "", sl
                                )
                                self._order_manager._sync_runtime_protection(
                                    existing, "tp_current", tp_id, tp
                                )
                        else:
                            new_trade = ActiveTrade(
                                symbol=sym,
                                entry_bar_index=0,
                                entry_price=entry,
                                entry_timestamp=int(time.time() * 1000),
                                sl=sl,
                                tp=tp,
                                qty=abs(amt),
                                side=direction,
                                status=STATUS_ACTIVE,
                                trigger_fvg=None,
                                initial_sl=sl,
                                initial_tp=tp,
                                trailing_count=0,
                                trail_count=0,
                                risk_pts=risk_pts,
                                is_recovered=True,
                                trail_mode="fvg",
                                tick_size=tick_size,
                                sl_order_id="",
                                tp_order_id=tp_id,
                            )
                            self._active_trades[sym] = new_trade
                            # A1-01 Fix: Sync runtime.protection for new trade
                            if self._order_manager:
                                self._order_manager._sync_runtime_protection(
                                    new_trade, "sl_current", "", sl
                                )
                                self._order_manager._sync_runtime_protection(
                                    new_trade, "tp_current", tp_id, tp
                                )
                        continue

                    if existing:
                        existing["sl_order_id"] = sl_id
                        existing["tp_order_id"] = tp_id
                        existing["tick_size"] = tick_size
                        if self._order_manager:
                            self._order_manager._sync_runtime_protection(
                                existing, "sl_current", sl_id, sl
                            )
                            self._order_manager._sync_runtime_protection(
                                existing, "tp_current", tp_id, tp
                            )
                    else:
                        new_trade = ActiveTrade(
                            symbol=sym,
                            entry_bar_index=0,
                            entry_price=entry,
                            entry_timestamp=int(time.time() * 1000),
                            sl=sl,
                            tp=tp,
                            qty=abs(amt),
                            side=direction,
                            status=STATUS_ACTIVE,
                            trigger_fvg=None,
                            initial_sl=sl,
                            initial_tp=tp,
                            trailing_count=0,
                            trail_count=0,
                            risk_pts=risk_pts,
                            is_recovered=True,
                            trail_mode="fvg",
                            tick_size=tick_size,
                            sl_order_id=sl_id,
                            tp_order_id=tp_id,
                        )
                        self._active_trades[sym] = new_trade
                        if self._order_manager:
                            self._order_manager._sync_runtime_protection(
                                new_trade, "sl_current", sl_id, sl
                            )
                            self._order_manager._sync_runtime_protection(
                                new_trade, "tp_current", tp_id, tp
                            )
                    protection_note = "" if tp_id else " (TP kurulamadi, sadece SL var)"
                    if not quiet:
                        self._pl(
                            sym,
                            "recover",
                            f"🔒 {direction.upper()} @ {_fmt_price(entry)} | SL={_fmt_price(sl)} (id={sl_id}) TP={_fmt_price(tp)} (id={tp_id}){protection_note} kuruldu",
                        )
        except Exception as e:
            if not quiet:
                self._pl("SYSTEM", "recover", f"\u274c Pozisyon kurtarma hatasi: {e}")

    # ── Ghost pozisyon temizliği ───────────────────────────────

    async def reconcile_ghost_positions(self) -> None:
        """trade_state.json'da open=true görünüp Binance'de kapalı
        olan pozisyonları temizle."""
        if not cfg.BINANCE_API_KEY:
            return
        from state_manager import dump_state, mark_trade_closed

        try:
            state = dump_state()
            self._ghost_fail_count = 0
        except Exception as e:
            self._ghost_fail_count += 1
            log.error(
                "[GHOST] state okunamadi, ghost temizligi bu turda atlandi: %s", e
            )
            if self._ghost_fail_count >= 5:
                log_event("ghost_check_persistently_failing", "SYSTEM", error=str(e))
                self._ghost_fail_count = 0
            return

        for sym, s in list(state.items()):
            if sym.startswith("_"):
                continue
            if not s.get("open"):
                continue
            if sym in self._active_trades:
                continue

            log.info(
                "[GHOST] %s state'de open=true ama active_trades'te yok — Binance sorgulaniyor...",
                sym,
            )
            try:
                positions = await self._rest.get_positions()
                pos = next((p for p in positions if p["symbol"] == sym), None)
                if pos and float(pos.get("positionAmt", 0)) != 0:
                    amt = float(pos["positionAmt"])
                    entry = float(pos.get("entryPrice", 0))
                    direction = "long" if amt > 0 else "short"
                    log.info(
                        "[GHOST] %s pozisyon ACIK (amt=%s, entry=%.2f) — SL/TP kontrol ediliyor",
                        sym,
                        amt,
                        entry,
                    )
                    # _recover_positions atlamis olabilir, mevcut emirleri kontrol et
                    open_orders = await self._rest.get_all_orders(sym)
                    has_sl = any(
                        self._rest.get_order_type(o)
                        in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                        for o in open_orders
                    )
                    has_tp = any(
                        self._rest.get_order_type(o)
                        in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                        for o in open_orders
                    )
                    if not has_sl or not has_tp:
                        log_event(
                            "ghost_missing_sltp",
                            sym,
                            side=direction,
                            entry=entry,
                            has_sl=has_sl,
                            has_tp=has_tp,
                        )
                        log.warning(
                            "[GHOST] %s SL/TP eksik (sl=%s tp=%s) — trade hatasi olabilir",
                            sym,
                            has_sl,
                            has_tp,
                        )
                        self._pl(
                            sym,
                            "ghost_missing_sltp",
                            f"⚠️ GHOST: {direction.upper()} @ {_fmt_price(entry)} | SL={has_sl} TP={has_tp} eksik",
                        )
                    else:
                        log_event("ghost_ok", sym, side=direction, entry=entry)
                        self._pl(
                            sym,
                            "ghost_ok",
                            f"🔒 GHOST: {direction.upper()} @ {_fmt_price(entry)} | SL/TP mevcut",
                        )
                else:
                    mark_trade_closed(sym)
                    self._states[sym].trades_today = 0
                    log_event("ghost_cleaned", sym)
                    log.info(
                        "[GHOST] %s pozisyon kapali, state temizlendi — trades_today sifirlandi",
                        sym,
                    )
                    self._pl(
                        sym,
                        "ghost_cleaned",
                        f"\U0001f4a4 GHOST: {sym} state temizlendi, trades_today=0",
                    )
            except Exception as e:
                log.warning("[GHOST] %s sorgu hatasi: %s", sym, e)

    def _known_protection_ids(self) -> set[str]:
        """Aktif trade'lerin sahip olabileceği tüm SL/TP order ID
        kaynaklarını toplar: current, prev, history, (varsa) pending.
        Geçiş halindeki (henüz cancel edilmemiş eski / henüz confirm
        edilmemiş yeni) ID'lerin orphan sanılmaması içindir (A5).

        Patch Set 3: ProtectionLifecycleService varsa karar ona delege
        edilir. Yoksa eski inline logic korunur.
        """
        if self._protection is not None:
            all_ids: set[str] = set()
            for t in self._active_trades.values():
                all_ids |= self._protection.known_ids(t)
            return all_ids

        known_ids: set[str] = set()
        for t in self._active_trades.values():
            for k in (
                "sl_order_id",
                "tp_order_id",
                "sl_order_id_prev",
                "tp_order_id_prev",
                "pending_sl_order_id",
                "pending_tp_order_id",
            ):
                oid = t.get(k)
                if oid:
                    known_ids.add(str(oid))
            for k in ("sl_order_id_history", "tp_order_id_history"):
                for oid in t.get(k) or []:
                    if oid:
                        known_ids.add(str(oid))
            for kind in ("sl", "tp"):
                ref = (t.get("protection_orders") or {}).get(kind)
                if isinstance(ref, dict) and ref.get("order_id"):
                    known_ids.add(str(ref["order_id"]))
        return known_ids

    async def reconcile_orphan_orders(self) -> None:
        """Binance'teki acik tum emirleri tara, bot'un bildigi
        trade'lere ait olmayanlari iptal et (crash sonrasi birikme onlenir).

        Patch Set 3: Transition guard ProtectionLifecycleService'e
        delege edilir (varsa)."""
        if not cfg.BINANCE_API_KEY:
            return

        for sym in self._symbols:
            trade = self._active_trades.get(sym)
            if trade is not None:
                if self._protection is not None:
                    if self._protection.should_skip_reconcile(trade):
                        log.info(
                            "[ORPHAN] %s status=%s — orphan sweep bu sembolde atlaniyor",
                            sym,
                            trade.get("status"),
                        )
                        continue
                elif trade.get("status") not in UNRESTRICTED_STATUSES:
                    log.info(
                        "[ORPHAN] %s status=%s — orphan sweep bu sembolde atlaniyor",
                        sym,
                        trade.get("status"),
                    )
                    continue
            known_ids = self._known_protection_ids()
            try:
                orders = await self._rest.get_all_orders(sym)
            except Exception:
                continue
            for o in orders:
                oid = str(o.get("orderId") or o.get("algoId") or "")
                if not oid or oid in known_ids:
                    continue
                is_algo = "algoId" in o
                cancel_id = o.get("algoId") or o.get("orderId")
                otype = self._rest.get_order_type(o)
                try:
                    await self._rest.cancel_order(
                        cancel_id, sym, reason="orphan_sweep", is_algo=is_algo
                    )
                    log_event("orphan_cleaned", sym, order_id=oid, order_type=otype)
                    log.info(
                        "[ORPHAN] %s emir iptal edildi (id=%s, type=%s)",
                        sym,
                        oid,
                        otype,
                    )
                except Exception as e:
                    log.warning("[ORPHAN] %s emir iptal hatasi: %s", sym, e)

    async def periodic_check_loop(self):
        """Her ~60sn'de recover_positions(quiet=True) + orphan sweep calistir.
        PaperTrader.run() tarafindan background task olarak baslatilir.

        FIX (P1-4): Orphan sweep de periyodik olarak calistirilir —
        _on_1m_close'daki sayac portfolio flat iken ilerlemedigi icin
        orada calismaz. Periyodik loop bunu karsilar.
        """
        while True:
            try:
                if cfg.BINANCE_API_KEY:
                    await self.recover_positions(quiet=True)
                    # FIX (P1-4): Periyodik orphan sweep — portfolio flat
                    # iken _on_1m_close tetiklenmez, sayac durur.
                    await self.reconcile_orphan_orders()
                    # FIX (P1-4): Periyodik ghost pozisyon temizligi —
                    # restart'lar arasinda olusan ghost pozisyonlar da
                    # fark edilsin (idempotent: temizlenen state open=false
                    # olur, sonraki turda elenir).
                    await self.reconcile_ghost_positions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("[POS-CHECK] periyodik kontrol hatasi (devam): %s", e)
            await asyncio.sleep(60)
