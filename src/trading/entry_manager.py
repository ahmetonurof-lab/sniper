"""
entry_manager.py — Entry validation + order placement.

PaperTrader._try_entry() içindeki 3 mekanik işlemi kapsar:
1. Risk mesafesi validasyonu (min_risk_dist kontrolü)
2. Pozisyon büyüklüğü hesaplama (qty = balance * risk / dist)
3. Canlı emir yerleştirme (market + SL + TP Binance API çağrıları)

Kırmızı çizgiler:
- Strateji mantığı (SL/TP hesaplama) PaperTrader'da kalır
- _pl() formatına dokunulmaz (PaperTrader'da kalır)
- Import yolları kırılmayacak

SL Live Uyumluluğu (2026-07-30):
- calculate_sl_tp: max_risk_dist override kaldırıldı, apply_min_sl_distance TP'den ÖNCE
- Decimal tick rounding (direction-aware floor/ceil)
- Actual fill fiyatıyla yeniden hesaplama + epsilon yön kontrolü
- -2021 emergency flow: retry yok, emergency close, CRITICAL alert
- protected state yalnızca SL response doğrulandıktan sonra
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from typing import TYPE_CHECKING

import config as cfg
from bot_infra import extract_order_id, _fmt_price
from paper_trade_logger import EventType, log_event as pt_log

if TYPE_CHECKING:
    from models import FVG

log = logging.getLogger("sniper.entry_manager")


class InvalidProtectionLevel(Exception):
    """SL/TP hesaplaması geçersiz yön veya mesafe üretti."""


@dataclass
class EntryExecutionResult:
    """Canli emir yerlestirme sonucu."""

    success: bool
    qty: float = 0.0
    sl_order_id: str = ""
    tp_order_id: str = ""
    error: str = ""
    entry_log_msg: str = ""
    actual_qty: float = 0.0
    actual_price: float = 0.0
    quote_qty: float = 0.0
    order_id: str = ""
    entry_price: float = 0.0


class EntryManager:
    def __init__(self, rest_client, is_live: bool = False):
        self._rest = rest_client
        self._is_live = is_live

    # ── 1. Risk validasyonu ──────────────────────────────────────

    @staticmethod
    def validate_risk(risk_dist: float, atr_val: float) -> tuple[bool, str]:
        min_risk_dist = atr_val * cfg.MIN_RISK_DIST_ATR_MULT
        if risk_dist < min_risk_dist:
            return False, (
                f"risk_dist={risk_dist:.6f} < min={min_risk_dist:.6f} "
                f"(atr={atr_val:.6f})"
            )
        return True, ""

    # ── 2. Pozisyon büyüklüğü ────────────────────────────────────

    @staticmethod
    def calculate_qty(
        balance: float,
        risk_pct: float,
        risk_dist: float,
        leverage: int,
        entry_price: float = 0.0,
    ) -> float:
        if risk_dist <= 0 or entry_price <= 0:
            return 0.0
        min_stop = entry_price * cfg.MIN_STOP_DIST_PCT
        risk_dist = max(risk_dist, min_stop)
        qty = (balance * risk_pct) / risk_dist
        if leverage > 0:
            max_margin = balance * cfg.MAX_MARGIN_PCT
            max_qty = (max_margin * leverage) / entry_price
            if qty > max_qty:
                qty = max_qty
        return qty

    # ── 2.5 SL/TP hesaplama — backtest parity ────────────────────

    @staticmethod
    def _fvg_height_valid(fvg: "FVG") -> bool:
        """FVG validity: height>0, no NaN/inf."""
        if fvg is None:
            return False
        h = fvg.top - fvg.bottom
        if h <= 0:
            return False
        if math.isnan(h) or math.isinf(h):
            return False
        return True

    @staticmethod
    def _assert_valid_protection_direction(
        side: str, entry_price: float, sl: float, tp: float
    ):
        if side == "long":
            if not (sl < entry_price and tp > entry_price):
                raise InvalidProtectionLevel(
                    f"long SL={sl} < entry={entry_price} < TP={tp} ihlali"
                )
        else:
            if not (sl > entry_price and tp < entry_price):
                raise InvalidProtectionLevel(
                    f"short SL={sl} > entry={entry_price} > TP={tp} ihlali"
                )

    @staticmethod
    def floor_to_tick(price: float, tick_size: Decimal) -> float:
        d = Decimal(str(price))
        return float(d.quantize(tick_size, rounding=ROUND_FLOOR))

    @staticmethod
    def ceil_to_tick(price: float, tick_size: Decimal) -> float:
        d = Decimal(str(price))
        return float(d.quantize(tick_size, rounding=ROUND_CEILING))

    @staticmethod
    def round_sl_tp(
        side: str, sl: float, tp: float, tick_size: Decimal
    ) -> tuple[float, float]:
        if side == "long":
            rsl = EntryManager.floor_to_tick(sl, tick_size)
            rtp = EntryManager.ceil_to_tick(tp, tick_size)
        else:
            rsl = EntryManager.ceil_to_tick(sl, tick_size)
            rtp = EntryManager.floor_to_tick(tp, tick_size)
        return rsl, rtp

    @staticmethod
    def calculate_sl_tp(
        side: str,
        entry_price: float,
        risk_pts: float,
        fvg_buf: float,
        tp_rr: float,
        trigger_fvg: "FVG | None",
    ) -> tuple[float, float]:
        """
        Backtest-parity SL/TP hesaplama (analyzer_v5 ile birebir).

        Sıra:
          1. FVG tabanlı veya fallback SL
          2. apply_min_sl_distance (MIN_SL_DISTANCE_PCT)
          3. risk_dist = abs(sl - entry_price)
          4. TP = entry ± risk_dist × tp_rr
          5. Yön kontrolü (InvalidProtectionLevel)
        """
        if risk_pts <= 0:
            raise InvalidProtectionLevel(f"risk_pts={risk_pts} <= 0")

        if side == "long":
            if EntryManager._fvg_height_valid(trigger_fvg):
                fh = trigger_fvg.top - trigger_fvg.bottom
                adaptive_buf = max(
                    fh * cfg.FVG_BUFFER_MIN_FACTOR,
                    max(risk_pts * 0.1, min(fh * 0.25, risk_pts * fvg_buf)),
                )
                raw_sl = trigger_fvg.bottom - adaptive_buf
            else:
                raw_sl = entry_price - risk_pts * 2

            sl = EntryManager.apply_min_sl_distance(entry_price, raw_sl, side)
            risk_dist = entry_price - sl
            if risk_dist <= 0:
                raise InvalidProtectionLevel(
                    f"long risk_dist={risk_dist} <= 0 (entry={entry_price} sl={sl})"
                )
            tp = entry_price + risk_dist * tp_rr
        else:
            if EntryManager._fvg_height_valid(trigger_fvg):
                fh = trigger_fvg.top - trigger_fvg.bottom
                adaptive_buf = max(
                    fh * cfg.FVG_BUFFER_MIN_FACTOR,
                    max(risk_pts * 0.1, min(fh * 0.25, risk_pts * fvg_buf)),
                )
                raw_sl = trigger_fvg.top + adaptive_buf
            else:
                raw_sl = entry_price + risk_pts * 2

            sl = EntryManager.apply_min_sl_distance(entry_price, raw_sl, side)
            risk_dist = sl - entry_price
            if risk_dist <= 0:
                raise InvalidProtectionLevel(
                    f"short risk_dist={risk_dist} <= 0 (entry={entry_price} sl={sl})"
                )
            tp = entry_price - risk_dist * tp_rr

        EntryManager._assert_valid_protection_direction(side, entry_price, sl, tp)
        return sl, tp

    # ── 3. Canlı emir yerleştirme ────────────────────────────────

    @staticmethod
    def apply_min_sl_distance(entry_price: float, sl: float, side: str) -> float:
        min_dist = entry_price * cfg.MIN_SL_DISTANCE_PCT
        if side == "long":
            min_sl_price = entry_price - min_dist
            return min(sl, min_sl_price)
        else:
            min_sl_price = entry_price + min_dist
            return max(sl, min_sl_price)

    @staticmethod
    def parse_market_fill(response: dict) -> tuple[float, float, float]:
        if not response or not isinstance(response, dict):
            return (0.0, 0.0, 0.0)
        executed_qty = float(response.get("executedQty", 0))
        if executed_qty <= 0:
            return (0.0, 0.0, 0.0)
        avg_price = float(response.get("avgPrice", 0))
        if avg_price <= 0:
            avg_price = float(response.get("averagePrice", 0))
        if avg_price <= 0:
            cum_quote = float(response.get("cummulativeQuoteQty", 0))
            if cum_quote <= 0:
                cum_quote = float(response.get("cumQuote", 0))
            if cum_quote <= 0:
                cum_quote = float(response.get("quoteQty", 0))
            if cum_quote > 0 and executed_qty > 0:
                avg_price = cum_quote / executed_qty
        quote_qty = float(response.get("cummulativeQuoteQty", 0))
        if quote_qty <= 0:
            quote_qty = float(response.get("cumQuote", 0))
        if quote_qty <= 0:
            quote_qty = float(response.get("quoteQty", 0))
        if quote_qty <= 0 and avg_price > 0 and executed_qty > 0:
            quote_qty = avg_price * executed_qty
        return (executed_qty, avg_price, quote_qty)

    @staticmethod
    def validate_protection_with_actual_fill(
        side: str,
        actual_fill: float,
        sl: float,
        tp: float,
        tick_size: Decimal,
        epsilon_ticks: int = 2,
    ) -> tuple[bool, str]:
        epsilon = float(tick_size) * epsilon_ticks
        if side == "long":
            if not (sl < actual_fill - epsilon):
                return False, f"SL={sl} >= actual_fill={actual_fill} - eps={epsilon}"
            if not (tp > actual_fill + epsilon):
                return False, f"TP={tp} <= actual_fill={actual_fill} + eps={epsilon}"
        else:
            if not (sl > actual_fill + epsilon):
                return False, f"SL={sl} <= actual_fill={actual_fill} + eps={epsilon}"
            if not (tp < actual_fill - epsilon):
                return False, f"TP={tp} >= actual_fill={actual_fill} - eps={epsilon}"
        return True, ""

    async def _emergency_close(
        self, sym: str, side: str, qty: float, reason: str
    ) -> EntryExecutionResult:
        opp_side = "SELL" if side.upper() == "BUY" else "BUY"
        side_label = "long" if side.upper() == "BUY" else "short"
        log.critical("[EMERGENCY] %s %s — acil kapatma baslatiliyor", sym, reason)
        pt_log(
            EventType.EMERGENCY_CLOSE_STARTED,
            sym,
            side_label,
            error={"code": 0, "message": reason, "retry_count": 0},
            reason=reason,
        )
        try:
            await self._rest.place_market_order(
                sym,
                opp_side,
                qty,
                reduce_only=True,
                client_order_id=f"emergency-{sym.lower()}-{int(time.time()*1000)}",
            )
            log.critical("[EMERGENCY] %s acil kapatma gonderildi", sym)
            pt_log(
                EventType.EMERGENCY_CLOSE_COMPLETED,
                sym,
                side_label,
                result="completed",
                reason="emergency_close_sent",
            )
        except Exception as e:
            log.critical("[EMERGENCY] %s acil kapatma BASARISIZ: %s", sym, e)
            pt_log(
                EventType.EMERGENCY_CLOSE_FAILED,
                sym,
                side_label,
                error={"code": -1, "message": str(e)[:200], "retry_count": 0},
                reason="emergency_close_failed",
            )
            return EntryExecutionResult(
                success=False,
                error=f"EMERGENCY CLOSE BASARISIZ — {e}",
            )
        return EntryExecutionResult(
            success=True,
            qty=qty,
            entry_log_msg=f"EMERGENCY CLOSE — {reason}",
        )

    async def execute_live_entry(
        self,
        sym: str,
        side: str,
        qty: float,
        sl: float,
        tp: float,
        entry_price: float | None = None,
        balance: float = 0.0,
        leverage: int = 1,
        risk_pts: float = 0.0,
        fvg_buf: float = 0.0,
        tp_rr: float = 2.0,
        trigger_fvg: "FVG | None" = None,
        trade_id: str = "",
    ) -> EntryExecutionResult:
        if not self._is_live:
            return EntryExecutionResult(
                success=True,
                qty=qty,
                entry_log_msg=(
                    f"\U0001f7e8 ENTRY: {side.upper()} | "
                    f"PRICE: {entry_price or 0:.2f} | "
                    f"SL: {sl:.2f} | TP: {tp:.2f} | "
                    f"QTY: {qty:.4f}"
                ),
            )

        mkt_side = "BUY" if side == "long" else "SELL"
        sl_side = "SELL" if side == "long" else "BUY"

        rounded_qty = await self._rest.apply_amount_precision(sym, qty)
        valid_qty = await self._rest.validate_min_amount(sym, rounded_qty)
        if valid_qty <= 0:
            return EntryExecutionResult(
                success=False, error=f"qty={qty:.6f} minQty altinda"
            )

        est_price = entry_price or await self._rest.estimate_market_price(sym)
        valid_qty = await self._bump_to_min_notional(
            sym, valid_qty, est_price, balance, leverage
        )
        if valid_qty <= 0:
            return EntryExecutionResult(
                success=False,
                error=(
                    f"qty={qty:.6f} minNotional altinda ve "
                    f"buying power yetersiz — trade iptal"
                ),
            )

        max_qty = await self._rest.get_max_qty(sym)
        if max_qty > 0 and valid_qty > max_qty:
            log.warning(
                "[MAX_QTY] %s qty=%.8f > LOT_SIZE.maxQty=%.8f — "
                "pozisyon boyutu clamp'leniyor",
                sym,
                valid_qty,
                max_qty,
            )
            valid_qty = await self._rest.apply_amount_precision(sym, max_qty)
            valid_qty = await self._rest.validate_min_amount(sym, valid_qty)
            if valid_qty <= 0:
                return EntryExecutionResult(
                    success=False,
                    error=(
                        f"max_qty={max_qty:.6f} clamp sonrasi minQty "
                        f"altinda kaldi — trade iptal"
                    ),
                )

        mkt_resp = await self._rest.place_market_order(
            sym,
            mkt_side,
            valid_qty,
            client_order_id=f"entry-{sym.lower()}-{int(time.time()*1000)}",
        )
        actual_qty, actual_price, quote_qty = self.parse_market_fill(mkt_resp)
        mkt_id = extract_order_id(mkt_resp)

        if not mkt_id and actual_qty > 0 and actual_price > 0:
            try:
                positions = await self._rest.get_positions()
                for p in positions:
                    if p["symbol"] == sym:
                        pos_amt = abs(float(p.get("positionAmt", 0)))
                        if pos_amt > 0:
                            return await self._emergency_close(
                                sym,
                                mkt_side,
                                pos_amt,
                                "MARKET orderId yok ama pozisyon acik — reconcile",
                            )
            except Exception as e:
                log.critical("[MARKET-RECONCILE] %s pos sorgu hatasi: %s", sym, e)
                return EntryExecutionResult(
                    success=False, error=f"MARKET RECONCILE BASARISIZ — {e}"
                )

        if not mkt_id or actual_qty <= 0 or actual_price <= 0:
            if mkt_id and actual_qty <= 0:
                log.info("[MARKET] %s orderId=%s fill bekleniyor...", sym, mkt_id)
                await asyncio.sleep(1.5)
                try:
                    positions = await self._rest.get_positions()
                    for p in positions:
                        if p["symbol"] == sym:
                            pos_amt = abs(float(p.get("positionAmt", 0)))
                            entry_px = float(p.get("entryPrice", 0))
                            if pos_amt > 0 and entry_px > 0:
                                actual_qty = pos_amt
                                actual_price = entry_px
                                quote_qty = actual_qty * actual_price
                                log.info(
                                    "[MARKET] %s gecikmeli fill tespit: qty=%.4f @ %.4f",
                                    sym,
                                    actual_qty,
                                    actual_price,
                                )
                                break
                except Exception as e:
                    log.warning("[MARKET] %s pozisyon sorgu hatasi: %s", sym, e)

            if actual_qty <= 0 or actual_price <= 0:
                err_detail = str(mkt_resp) if mkt_resp else "empty_response"
                log.warning(
                    "[MARKET] %s basarisiz resp=%s qty=%.8f", sym, err_detail, valid_qty
                )
                return EntryExecutionResult(
                    success=False, error=f"MARKET BASARISIZ — {err_detail}"
                )

        log.info(
            "[ORDER] %s MARKET entry OK orderId=%s "
            "requested_qty=%.8f actual_qty=%.8f actual_price=%.6f quote_qty=%.2f",
            sym,
            mkt_id,
            valid_qty,
            actual_qty,
            actual_price,
            quote_qty,
        )

        pt_log(
            EventType.ENTRY_FILLED,
            sym,
            side,
            trade_id=trade_id,
            entry={
                "signal_price": entry_price or 0.0,
                "actual_fill_price": actual_price,
                "requested_qty": valid_qty,
                "actual_qty": actual_qty,
            },
            result="accepted",
            reason="market_fill_ok",
        )

        # ── SL/TP'yi actual fill price ile yeniden hesapla ──
        order_qty = actual_qty if actual_qty > 0 else valid_qty
        protected = False
        if actual_price > 0 and risk_pts > 0:
            try:
                sl, tp = EntryManager.calculate_sl_tp(
                    side=side,
                    entry_price=actual_price,
                    risk_pts=risk_pts,
                    fvg_buf=fvg_buf,
                    tp_rr=tp_rr,
                    trigger_fvg=trigger_fvg,
                )
            except InvalidProtectionLevel as e:
                log.critical(
                    "[SL_TP_CALC] %s gecersiz SL/TP: %s — acil kapatma",
                    sym,
                    e,
                )
                return await self._emergency_close(
                    sym, mkt_side, order_qty, f"SL/TP CALC FAIL — {e}"
                )

            # ── Tick rounding (Decimal, direction-aware) ──
            tick_size = await self._rest.get_tick_size(sym)
            tick_dec = Decimal(str(tick_size))
            raw_sl, raw_tp = sl, tp
            rsl, rtp = EntryManager.round_sl_tp(side, sl, tp, tick_dec)
            sl, tp = rsl, rtp
            eps = float(tick_dec) * 2
            rounding_label = (
                "long_sl_floor_tp_ceil" if side == "long" else "short_sl_ceil_tp_floor"
            )

            # ── Yön kontrolü (actual fill fiyatiyla) ──
            valid_dir, dir_msg = EntryManager.validate_protection_with_actual_fill(
                side, actual_price, sl, tp, tick_dec, epsilon_ticks=2
            )
            if not valid_dir:
                log.critical(
                    "[SL_TP_VALIDATION] %s actual_fill=%.6f dogrulama BASARISIZ: %s — acil kapatma",
                    sym,
                    actual_price,
                    dir_msg,
                )
                return await self._emergency_close(
                    sym, mkt_side, order_qty, f"SL/TP direction fail — {dir_msg}"
                )

            risk_dist = abs(sl - actual_price)
            fvg_present = trigger_fvg is not None and EntryManager._fvg_height_valid(
                trigger_fvg
            )
            fvg_data = None
            if fvg_present and trigger_fvg:
                fvg_data = {
                    "present": True,
                    "top": trigger_fvg.top,
                    "bottom": trigger_fvg.bottom,
                    "height": trigger_fvg.top - trigger_fvg.bottom,
                    "bar_index": trigger_fvg.bar_index,
                    "buffer": 0.0,
                    "fallback_used": False,
                    "max_risk_cap_used": False,
                }

            pt_log(
                EventType.INITIAL_SL_CALCULATED,
                sym,
                side,
                trade_id=trade_id,
                protection={
                    "raw_sl": round(raw_sl, 8),
                    "raw_tp": round(raw_tp, 8),
                    "normalized_sl": sl,
                    "normalized_tp": tp,
                    "final_sl": sl,
                    "final_tp": tp,
                    "risk_distance": round(risk_dist, 8),
                    "tp_rr": tp_rr,
                    "tick_size": tick_size,
                    "epsilon": eps,
                    "rounding": rounding_label,
                    "sl_order_id": None,
                    "tp_order_id": None,
                },
                fvg=fvg_data,
                result="accepted",
                reason="protection_ready",
            )

            pt_log(
                EventType.PROTECTION_NORMALIZED,
                sym,
                side,
                trade_id=trade_id,
                protection={
                    "raw_sl": round(raw_sl, 8),
                    "raw_tp": round(raw_tp, 8),
                    "normalized_sl": sl,
                    "normalized_tp": tp,
                    "tick_size": tick_size,
                    "epsilon": eps,
                    "rounding": rounding_label,
                },
                result="accepted",
                reason="tick_rounding_ok",
            )

            pt_log(
                EventType.PROTECTION_VALIDATED,
                sym,
                side,
                trade_id=trade_id,
                validation={
                    "sl_direction_valid": True,
                    "tp_direction_valid": True,
                    "tick_valid": True,
                    "actual_qty_used": True,
                    "placeable": True,
                },
                entry={
                    "signal_price": entry_price or 0.0,
                    "actual_fill_price": actual_price,
                },
                result="accepted",
                reason="direction_validation_ok",
            )

            log.info(
                "[SL_TP_RECALC] %s sl/tp actual_price=%.6f ile yeniden hesaplandi: sl=%.6f tp=%.6f",
                sym,
                actual_price,
                sl,
                tp,
            )

        # ── SL emri (actual_qty ile) ────────────────────
        rounded_sl = await self._rest.apply_price_precision(sym, sl)
        sl_resp = await self._rest.place_stop_order(sym, sl_side, order_qty, rounded_sl)
        log.debug("[ORDER] %s SL place_stop_order raw resp: %s", sym, sl_resp)

        # -2021 veya herhangi bir hata: retry YOK, emergency close
        err_code = sl_resp.get("code", 0) if isinstance(sl_resp, dict) else 0
        sl_id = extract_order_id(sl_resp)

        if not sl_id or err_code == -2021:
            log.critical(
                "[ORDER] %s SL BASARISIZ code=%s! Acil kapatma. resp=%s",
                sym,
                err_code,
                sl_resp,
            )
            pt_log(
                EventType.SL_REJECTED,
                sym,
                side,
                trade_id=trade_id,
                error={
                    "code": err_code,
                    "message": str(sl_resp.get("msg", "")),
                    "retry_count": 0,
                },
                protected_state_before=False,
                reason=f"SL code={err_code}",
            )
            return await self._emergency_close(
                sym, mkt_side, order_qty, f"SL FAIL code={err_code}"
            )

        protected = True
        pt_log(
            EventType.SL_PLACED,
            sym,
            side,
            trade_id=trade_id,
            protection={
                "sl_order_id": sl_id,
                "final_sl": sl,
            },
            protected_state_before=False,
            protected_state_after=True,
            result="accepted",
            reason="sl_placed_ok",
        )
        log.info("[ORDER] %s SL OK id=%s (protected=%s)", sym, sl_id, protected)

        # ── TP emri ───────────────────────────────────────────────
        rounded_tp = await self._rest.apply_price_precision(sym, tp)
        tp_resp = await self._rest.place_tp_order(sym, sl_side, order_qty, rounded_tp)
        log.debug("[ORDER] %s TP place_tp_order raw resp: %s", sym, tp_resp)
        tp_id = extract_order_id(tp_resp)
        if tp_id:
            pt_log(
                EventType.TP_PLACED,
                sym,
                side,
                trade_id=trade_id,
                protection={
                    "tp_order_id": tp_id,
                    "final_tp": tp,
                },
                protected_state_before=True,
                protected_state_after=True,
                result="accepted",
                reason="tp_placed_ok",
            )
            log.info("[ORDER] %s TP OK algoId=%s", sym, tp_id)
        else:
            pt_log(
                EventType.TP_REJECTED,
                sym,
                side,
                trade_id=trade_id,
                error={
                    "code": -1,
                    "message": str(tp_resp),
                    "retry_count": 0,
                },
                protected_state_before=True,
                protected_state_after=True,
                reason="tp_placement_failed",
            )
            log.warning("[ORDER] %s TP BASARISIZ! resp=%s", sym, tp_resp)

        return EntryExecutionResult(
            success=True,
            qty=actual_qty,
            actual_qty=actual_qty,
            actual_price=actual_price,
            quote_qty=quote_qty,
            order_id=mkt_id,
            entry_price=actual_price,
            sl_order_id=sl_id,
            tp_order_id=tp_id,
            entry_log_msg=(
                f"\U0001f7e8 ENTRY: {side.upper()} | "
                f"PRICE: {_fmt_price(est_price)} (filled @ {_fmt_price(actual_price)}) | "
                f"SL: {_fmt_price(sl)} | TP: {_fmt_price(tp)} | "
                f"QTY: {valid_qty:.4f} (filled: {actual_qty:.4f})"
            ),
        )

    # ── minNotional bump yardımcısı (YENİ) ───────────────────────

    async def _bump_to_min_notional(
        self,
        sym: str,
        qty: float,
        price: float,
        balance: float,
        leverage: int,
    ) -> float:
        """
        qty * price < minNotional ise qty'yi minimum geçerli değere yükselt.

        Adımlar:
          1. Notional kontrolü — zaten yeterliyse dokunma.
          2. Gerekli minimum qty'yi hesapla: ceil(minNotional / price / step) * step
          3. Buying power tavanıyla karşılaştır.
          4. Tavan yeterliyse bump'lı qty'yi döndür, değilse 0.0.
        """
        if price <= 0:
            return 0.0

        notional = qty * price
        min_notional = await self._rest.get_min_notional(sym)

        if notional >= min_notional:
            return qty  # zaten geçerli, dokunma

        # Minimum geçerli qty hesapla
        step = await self._rest.get_step_size(sym)
        min_qty_n = min_notional / price  # gereken ham miktar
        # step'e yukarı yuvarla
        bumped = math.ceil(min_qty_n / step) * step
        bumped = round(bumped, 8)

        # Buying power tavanı
        if balance > 0 and leverage > 0 and price > 0:
            max_qty = (balance * cfg.MAX_MARGIN_PCT * leverage) / price
            if bumped > max_qty:
                log.warning(
                    "[MINNOTIONAL] %s bump=%.8f > buying_power=%.8f "
                    "(balance=%.2f lev=%d) — trade iptal",
                    sym,
                    bumped,
                    max_qty,
                    balance,
                    leverage,
                )
                return 0.0

        log.info(
            "[MINNOTIONAL] %s qty %.8f → %.8f bump (notional %.2f → %.2f USDT)",
            sym,
            qty,
            bumped,
            notional,
            bumped * price,
        )
        return bumped
