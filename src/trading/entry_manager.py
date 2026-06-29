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
from models import ActiveTrade

if TYPE_CHECKING:
    from models import FVG
    from session import SessionState

log = logging.getLogger("sniper.entry_manager")


@dataclass
class EntryExecutionResult:
    """Canlı emir yerleştirme sonucu."""

    success: bool
    qty: float = 0.0
    sl_order_id: str = ""
    tp_order_id: str = ""
    error: str = ""


SAFETY_MARGIN = 0.95  # buying power tavanında %5 emniyet payı


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
        if risk_dist <= 0:
            return 0.0
        qty = (balance * risk_pct) / risk_dist
        if entry_price > 0 and leverage > 0:
            max_qty = (balance * leverage * SAFETY_MARGIN) / entry_price
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
                adaptive_buf = max(
                    fvg_height * cfg.FVG_BUFFER_MIN_FACTOR,
                    min(fvg_height * 0.25, risk_pts * fvg_buf),
                )
                sl = trigger_fvg.bottom - adaptive_buf
            else:
                sl = entry_price - risk_pts * 2
            risk_dist = abs(sl - entry_price)
            if trigger_fvg and risk_dist > max_risk_dist:
                sl = entry_price - risk_pts * 2
                risk_dist = abs(sl - entry_price)
            tp = entry_price + risk_dist * tp_rr
        else:
            if trigger_fvg:
                fvg_height = trigger_fvg.top - trigger_fvg.bottom
                adaptive_buf = max(
                    fvg_height * cfg.FVG_BUFFER_MIN_FACTOR,
                    min(fvg_height * 0.25, risk_pts * fvg_buf),
                )
                sl = trigger_fvg.top + adaptive_buf
            else:
                sl = entry_price + risk_pts * 2
            risk_dist = abs(sl - entry_price)
            if trigger_fvg and risk_dist > max_risk_dist:
                sl = entry_price + risk_pts * 2
                risk_dist = abs(sl - entry_price)
            tp = entry_price - risk_dist * tp_rr
        return sl, tp

    # ── 3. Canlı emir yerleştirme ────────────────────────────────

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
            return EntryExecutionResult(success=True, qty=qty)

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
        mkt_id = extract_order_id(mkt_resp)
        if not mkt_id:
            return EntryExecutionResult(
                success=False, error="MARKET BASARISIZ — trade iptal"
            )

        log.info(
            "[ORDER] %s MARKET entry OK orderId=%s qty=%.8f",
            sym,
            mkt_id,
            valid_qty,
        )

        # ── SL emri ───────────────────────────────────────────────
        rounded_sl = await self._rest.apply_price_precision(sym, sl)
        sl_resp = await self._rest.place_stop_order(sym, sl_side, valid_qty, rounded_sl)
        sl_id = extract_order_id(sl_resp)
        if not sl_id:
            log.critical(
                "[ORDER] %s SL BASARISIZ! Acil pozisyon kapatiliyor. resp=%s",
                sym,
                sl_resp,
            )
            opp_side = "SELL" if mkt_side == "BUY" else "BUY"
            try:
                await self._rest.place_market_order(sym, opp_side, valid_qty)
            except Exception as e:
                log.critical(
                    "[ORDER] %s acil pozisyon kapatma emri basarisiz: %s", sym, e
                )
            return EntryExecutionResult(
                success=False, error="SL BASARISIZ — acil pozisyon kapatildi"
            )

        log.info("[ORDER] %s SL OK algoId=%s", sym, sl_id)

        # ── TP emri ───────────────────────────────────────────────
        rounded_tp = await self._rest.apply_price_precision(sym, tp)
        tp_resp = await self._rest.place_tp_order(sym, sl_side, valid_qty, rounded_tp)
        tp_id = extract_order_id(tp_resp)
        if tp_id:
            log.info("[ORDER] %s TP OK algoId=%s", sym, tp_id)
        else:
            log.warning("[ORDER] %s TP BASARISIZ! resp=%s", sym, tp_resp)

        return EntryExecutionResult(
            success=True,
            qty=valid_qty,
            sl_order_id=sl_id,
            tp_order_id=tp_id,
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
            max_qty = (balance * leverage * SAFETY_MARGIN) / price
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

    # ── LHR entry (Faz 7) ────────────────────────────────────────

    @staticmethod
    def execute_lhr_entry(
        sym: str,
        side: str,
        current,
        atr_val: float,
        sl: float,
        tp: float,
        ss: "SessionState",
        balance: float,
        risk_pct: float,
        leverage: int,
        zone_bottom: float,
        zone_top: float,
        active_trades: dict,
        pl_callback,
    ) -> bool:
        entry_price = current.close
        risk_dist = abs(sl - entry_price)

        valid, _ = EntryManager.validate_risk(risk_dist, atr_val)
        if not valid:
            return False

        qty = EntryManager.calculate_qty(balance, risk_pct, risk_dist, leverage)
        if qty <= 0:
            return False

        log.info(
            "🟨 LHR RETRADE | %s | zone: [%.4f-%.4f]",
            side.upper(),
            zone_bottom,
            zone_top,
        )

        from state_manager import clear_retrade_arm

        active_trades[sym] = ActiveTrade(
            symbol=sym,
            side=side,
            entry_price=entry_price,
            entry_bar_index=current.index,
            sl=sl,
            tp=tp,
            qty=qty,
            initial_sl=sl,
            initial_tp=tp,
            trailing_count=0,
            is_retrade=True,
            hybrid_mode="lhr",
        )
        ss.trades_today += 1
        ss.retrade_armed = False
        clear_retrade_arm(sym)
        pl_callback(
            sym,
            "lhr_entry",
            f"🟨 LHR ENTRY: {side.upper()} @ {entry_price:.2f} sl={sl:.2f} tp={tp:.2f}",
        )
        return True
