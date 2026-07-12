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

Düzeltme (v2):
- minNotional hatası artık trade'i iptal etmiyor.
  qty < minNotional ise, minimum geçerli qty'ye yükseltilir,
  sonra buying power tavanıyla kontrol edilir.
  Tavan da yetersizse o zaman iptal edilir ve sebebi loglanır.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import config as cfg
from bot_infra import extract_order_id

if TYPE_CHECKING:
    from models import FVG

log = logging.getLogger("sniper.entry_manager")


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

    # ── 2.5 SL/TP hesaplama ──────────────────────────────────────

    @staticmethod
    def calculate_sl_tp(
        side: str,
        entry_price: float,
        risk_pts: float,
        fvg_buf: float,
        tp_rr: float,
        trigger_fvg: "FVG | None",
        london_high: float,
        london_low: float,
    ) -> tuple[float, float]:
        max_risk_dist = risk_pts * cfg.MAX_SL_DIST_MULT
        if side == "long":
            if trigger_fvg:
                fvg_height = trigger_fvg.top - trigger_fvg.bottom
                if fvg_height <= 0:
                    sl = entry_price - risk_pts * 2
                    log.warning(
                        "[SL_CALC] %s long FVG height=0 — fallback SL",
                        side,
                    )
                else:
                    adaptive_buf = max(
                        fvg_height * cfg.FVG_BUFFER_MIN_FACTOR,
                        max(risk_pts * 0.1, min(fvg_height * 0.25, risk_pts * fvg_buf)),
                    )
                    sl = trigger_fvg.bottom - adaptive_buf
            else:
                sl = entry_price - risk_pts * 2
            risk_dist = abs(sl - entry_price)
            if trigger_fvg and risk_dist > max_risk_dist:
                sl = entry_price - risk_pts * 2
                risk_dist = abs(sl - entry_price)
            if risk_dist <= 0:
                sl = entry_price - risk_pts * 2
                risk_dist = abs(sl - entry_price)
            tp = entry_price + risk_dist * tp_rr
        else:
            if trigger_fvg:
                fvg_height = trigger_fvg.top - trigger_fvg.bottom
                if fvg_height <= 0:
                    sl = entry_price + risk_pts * 2
                    log.warning(
                        "[SL_CALC] %s short FVG height=0 — fallback SL",
                        side,
                    )
                else:
                    adaptive_buf = max(
                        fvg_height * cfg.FVG_BUFFER_MIN_FACTOR,
                        max(risk_pts * 0.1, min(fvg_height * 0.25, risk_pts * fvg_buf)),
                    )
                    sl = trigger_fvg.top + adaptive_buf
            else:
                sl = entry_price + risk_pts * 2
            risk_dist = abs(sl - entry_price)
            if trigger_fvg and risk_dist > max_risk_dist:
                sl = entry_price + risk_pts * 2
                risk_dist = abs(sl - entry_price)
            if risk_dist <= 0:
                sl = entry_price + risk_pts * 2
                risk_dist = abs(sl - entry_price)
            tp = entry_price - risk_dist * tp_rr
        return sl, tp

    # ── 3. Canlı emir yerleştirme ────────────────────────────────

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
    ) -> EntryExecutionResult:
        """
        Binance üzerinde market + SL + TP emirlerini yerleştir.

        Değişiklik: minNotional altında kalınırsa trade iptal edilmez,
        qty minimum geçerli değere yükseltilir (bump). Buying power
        tavanını aşıyorsa o zaman iptal edilir.

        Args:
            balance: Hesap bakiyesi — minNotional bump sonrası tavan kontrolü için.
            leverage: Kaldıraç — buying power tavanı için.
        """
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

        # ── Miktar precision ──────────────────────────────────────
        rounded_qty = await self._rest.apply_amount_precision(sym, qty)
        valid_qty = await self._rest.validate_min_amount(sym, rounded_qty)
        if valid_qty <= 0:
            return EntryExecutionResult(
                success=False, error=f"qty={qty:.6f} minQty altinda"
            )

        # ── MIN_NOTIONAL kontrolü + otomatik bump ─────────────────
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

        # ── Market entry ──────────────────────────────────────────
        mkt_resp = await self._rest.place_market_order(sym, mkt_side, valid_qty)
        actual_qty, actual_price, quote_qty = self.parse_market_fill(mkt_resp)
        mkt_id = extract_order_id(mkt_resp)

        # Fill varsa ama orderId eksikse — Binance'te pozisyon acilmis olabilir
        if not mkt_id and actual_qty > 0 and actual_price > 0:
            try:
                positions = await self._rest.get_positions()
                for p in positions:
                    if p["symbol"] == sym:
                        pos_amt = abs(float(p.get("positionAmt", 0)))
                        if pos_amt > 0:
                            close_resp = await self._rest.place_market_order(
                                sym, opp_side, pos_amt, reduce_only=True
                            )
                            log.critical(
                                "[MARKET-RECONCILE] %s pos=%.4f acik, orderId yok — "
                                "acil kapatma gonderildi",
                                sym, pos_amt,
                            )
                            return EntryExecutionResult(
                                success=False, error=f"MARKET orderId bulunamadi — "
                                f"pos={pos_amt:.4f} acik kapatildi"
                            )
            except Exception as e:
                log.critical("[MARKET-RECONCILE] %s pos sorgu hatasi: %s", sym, e)
                return EntryExecutionResult(
                    success=False, error=f"MARKET RECONCILE BASARISIZ — {e}"
                )

        if not mkt_id or actual_qty <= 0 or actual_price <= 0:
            err_detail = str(mkt_resp) if mkt_resp else "empty_response"
            log.warning(
                "[MARKET] %s basarisiz resp=%s qty=%.8f",
                sym, err_detail, valid_qty
            )
            return EntryExecutionResult(
                success=False, error=f"MARKET BASARISIZ — {err_detail}"
            )

        log.info(
            "[ORDER] %s MARKET entry OK orderId=%s "
            "requested_qty=%.8f actual_qty=%.8f actual_price=%.6f quote_qty=%.2f",
            sym, mkt_id, valid_qty, actual_qty, actual_price, quote_qty,
        )

        # ── SL ve TP emirleri (actual_qty ile) ────────────────────
        order_qty = actual_qty if actual_qty > 0 else valid_qty
        rounded_sl = await self._rest.apply_price_precision(sym, sl)
        sl_resp = await self._rest.place_stop_order(sym, sl_side, order_qty, rounded_sl)
        sl_id = extract_order_id(sl_resp)
        if not sl_id:
            log.critical(
                "[ORDER] %s SL BASARISIZ! Acil pozisyon kapatiliyor. resp=%s",
                sym, sl_resp,
            )
            opp_side = "SELL" if mkt_side == "BUY" else "BUY"
            try:
                await self._rest.place_market_order(sym, opp_side, order_qty)
            except Exception as e:
                log.critical(
                    "[ORDER] %s acil pozisyon kapatma emri basarisiz: %s", sym, e
                )
            return EntryExecutionResult(
                success=False, error="SL BASARISIZ — acil pozisyon kapatildi"
            )

        log.info("[ORDER] %s SL OK at line=%s", sym, sl_id)

        # ── TP emri ───────────────────────────────────────────────
        rounded_tp = await self._rest.apply_price_precision(sym, tp)
        tp_resp = await self._rest.place_tp_order(sym, sl_side, order_qty, rounded_tp)
        tp_id = extract_order_id(tp_resp)
        if tp_id:
            log.info("[ORDER] %s TP OK algoId=%s", sym, tp_id)
        else:
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
                f"PRICE: {est_price:.2f} (filled @ {actual_price:.4f}) | "
                f"SL: {sl:.2f} | TP: {tp:.2f} | "
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
