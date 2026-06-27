"""
entry_manager.py — Entry validation + order placement.

PaperTrader._try_entry() içindeki 3 mekanik işlemi kapsar:
  1. Risk mesafesi validasyonu (min_risk_dist kontrolü)
  2. Pozisyon büyüklüğü hesaplama (qty = balance * risk / dist / leverage)
  3. Canlı emir yerleştirme (market + SL + TP Binance API çağrıları)

Kırmızı çizgiler:
  - Strateji mantığı (SL/TP hesaplama) PaperTrader'da kalır
  - _pl() formatına dokunulmaz (PaperTrader'da kalır)
  - Import yolları kırılmayacak
"""

from __future__ import annotations

import logging
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
    """Canlı emir yerleştirme sonucu.

    Attributes:
        success: Tüm emirler başarıyla yerleştirildi mi?
        qty: Binance tarafından onaylanan miktar (precision sonrası)
        sl_order_id: SL stop order ID (algoId)
        tp_order_id: TP take profit order ID (algoId)
        error: Hata mesajı (sadece success=False ise)
    """

    success: bool
    qty: float = 0.0
    sl_order_id: str = ""
    tp_order_id: str = ""
    error: str = ""


class EntryManager:
    """Entry validasyonu ve emir yerleştirme.

    PaperTrader'dan DI (dependency injection) ile alır:
      - rest_client: BinanceRESTClient (canlı emirler için)
      - is_live: bool — API key varsa ve bot canlı moddaysa True

    Test edilebilirlik: Tüm metodlar saf veya mock'lanabilir.
    """

    def __init__(self, rest_client, is_live: bool = False):
        self._rest = rest_client
        self._is_live = is_live

    # ── 1. Risk validasyonu ──────────────────────────────────

    @staticmethod
    def validate_risk(risk_dist: float, atr_val: float) -> tuple[bool, str]:
        """Risk mesafesi minimum eşiğin üzerinde mi?

        Orijinal _try_entry() "1. SENKRON VALİDASYONLAR" ile birebir aynı.
        """
        min_risk_dist = atr_val * cfg.MIN_RISK_DIST_ATR_MULT
        if risk_dist < min_risk_dist:
            return False, (
                f"risk_dist={risk_dist:.6f} < min={min_risk_dist:.6f} "
                f"(atr={atr_val:.6f})"
            )
        return True, ""

    # ── 2. Pozisyon büyüklüğü ────────────────────────────────

    @staticmethod
    def calculate_qty(
        balance: float,
        risk_pct: float,
        risk_dist: float,
        leverage: int,
        entry_price: float = 0.0,
    ) -> float:
        """Risk bazlı pozisyon büyüklüğü hesapla.

        Orijinal _try_entry() qty formülü ile birebir aynı:
          qty = (balance * risk_pct) / risk_dist / leverage

        1x kaldıraçta pozisyon notional'ı bakiyeyi aşmasın diye
        entry_price verilmişse max qty = balance / entry_price ile
        tavanlanır — böylece Binance -2019 hatası önlenir.
        """
        if risk_dist <= 0:
            return 0.0
        qty = (balance * risk_pct) / risk_dist / leverage
        if leverage == 1 and entry_price > 0:
            max_qty = balance / entry_price
            if qty > max_qty:
                log.info(
                    "[QTY] qty capped: risk-based=%.4f → balance-based=%.4f "
                    "(balance=%.2f, entry=%.2f, leverage=%d)",
                    qty,
                    max_qty,
                    balance,
                    entry_price,
                    leverage,
                )
                qty = max_qty
        return qty

    # ── 2.5 SL/TP hesaplama ──────────────────────────────────

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
        """SL/TP hesapla. Orijinal _try_entry() SL/TP mantığı birebir aynı.

        Args:
            side: "long" veya "short"
            entry_price: Giriş fiyatı
            risk_pts: ATR * SL_ATR_MULT
            fvg_buf: FVG buffer çarpanı
            tp_rr: TP risk/ödül oranı
            trigger_fvg: Tetikleyici FVG (None ise fallback)
            london_high: Londra seansı yüksek seviyesi
            london_low: Londra seansı düşük seviyesi

        Returns:
            (sl, tp) tuple
        """
        if side == "long":
            sl = (
                (trigger_fvg.bottom - (risk_pts * fvg_buf))
                if trigger_fvg
                else (entry_price - risk_pts * 2)
            )
            tp = (
                london_high
                if london_high > entry_price
                else entry_price + risk_pts * tp_rr
            )
        else:
            sl = (
                (trigger_fvg.top + (risk_pts * fvg_buf))
                if trigger_fvg
                else (entry_price + risk_pts * 2)
            )
            tp = (
                london_low
                if london_low < entry_price
                else entry_price - risk_pts * tp_rr
            )
        return sl, tp

    # ── 3. Canlı emir yerleştirme ────────────────────────────

    async def execute_live_entry(
        self, sym: str, side: str, qty: float, sl: float, tp: float
    ) -> EntryExecutionResult:
        """Binance üzerinde market + SL + TP emirlerini yerleştir.

        Orijinal _try_entry() içindeki "if cfg.BINANCE_API_KEY and live" bloğu
        ile birebir aynı mantık. Hata durumunda acil pozisyon kapatma dahil.

        Returns:
            EntryExecutionResult — başarılıysa success=True ve order ID'leri dolu.
        """
        if not self._is_live:
            return EntryExecutionResult(success=True, qty=qty)

        mkt_side = "BUY" if side == "long" else "SELL"
        sl_side = "SELL" if side == "long" else "BUY"

        # ── Miktar precision ──
        rounded_qty = await self._rest.apply_amount_precision(sym, qty)
        valid_qty = await self._rest.validate_min_amount(sym, rounded_qty)
        if valid_qty <= 0:
            return EntryExecutionResult(
                success=False, error=f"qty={qty:.6f} minQty altinda"
            )

        # ── Market entry ──
        mkt_resp = await self._rest.place_market_order(sym, mkt_side, valid_qty)
        mkt_id = extract_order_id(mkt_resp)
        if not mkt_id:
            return EntryExecutionResult(
                success=False, error="MARKET BASARISIZ — trade iptal"
            )

        log.info(
            "[ORDER] %s MARKET entry OK orderId=%s qty=%.8f", sym, mkt_id, valid_qty
        )

        # ── SL emri ──
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

        # ── TP emri ──
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

    # ── LHR entry (Faz 7) ────────────────────────────────────

    @staticmethod
    def execute_lhr_entry(
        sym: str,
        side: str,
        current,  # duck-typed: .close, .index (Bar)
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
        """LHR fallback entry: risk validasyonu + qty + trade kaydı + state güncelleme.

        Args:
            sym: Sembol adı
            side: "long" veya "short"
            current: Güncel bar (close ve index kullanılır)
            atr_val: ATR değeri
            sl, tp: Stop-loss ve take-profit seviyeleri
            ss: SessionState (trades_today, retrade_armed mutasyonu)
            balance: Hesap bakiyesi
            risk_pct: Risk yüzdesi (örn: 0.01)
            leverage: Kaldıraç
            zone_bottom, zone_top: LHR zone sınırları
            active_trades: Aktif trade'ler sözlüğü
            pl_callback: Display callback (sym, key, msg)

        Returns: True if trade was successfully created, False otherwise.
        """
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
