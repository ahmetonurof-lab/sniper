"""
retrade_engine.py — Retrade sweep detection + RSM progression + trigger check + LHR fallback.

PaperTrader._check_retrade() içindeki şu blokları kapsar:
  1. Sweep tespiti (son 4 barda pivot kırılımı)
  2. Retrade RSM progression (IDLE → SWEEP_DETECTED → TRIGGER_READY)
  3. Trigger filtreleri (session + entry bar sıralaması)
  4. LHR fallback zone/SL/TP hesaplama (Faz 4.3)

PaperTrader'da kalanlar:
  - Guard kontrolleri (retrade_armed, trades_today, active_trades)
  - _pl() yazdırma
  - _try_entry() çağrısı
  - _try_lhr_entry() çağrısı
  - State güncellemeleri

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - Import yolları kırılmayacak
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal

import config as cfg
from models import Bar
from retrace_state import RetraceStateMachine
from session import SessionPhase, SessionState, detect_phase_from_timestamp

log = logging.getLogger("sniper.retrade_engine")


@dataclass
class RetradeSweepResult:
    """Retrade sweep taraması sonucu.

    Attributes:
        found: Son 4 barda sweep bulundu mu?
        sweep_bar_idx: Sweep'in gerçekleştiği bar index'i
        sweep_dir: Sweep yönü (retrade_side'a göre hesaplanır)
    """

    found: bool = False
    sweep_bar_idx: int = -1
    sweep_dir: Literal["bullish", "bearish"] | None = None


@dataclass
class RetradeDecision:
    """Retrade trigger kararı.

    Attributes:
        decision: SKIP (filtre reddetti), WAIT (FVG henüz yok), TRIGGER (entry yapılabilir)
        reason: SKIP sebebi (log için)
    """

    decision: Literal["SKIP", "WAIT", "TRIGGER"]
    reason: str = ""


@dataclass
class LHRFallbackResult:
    """LHR fallback zone/SL/TP hesaplama sonucu (Faz 4.3).

    Attributes:
        in_zone: Fiyat LHR zone içinde mi?
        side: "long" veya "short"
        sl: Hesaplanan stop-loss
        tp: Hesaplanan take-profit
        zone_bottom: LHR zone alt sınırı
        zone_top: LHR zone üst sınırı
    """

    in_zone: bool = False
    side: Literal["long", "short"] | None = None
    sl: float = 0.0
    tp: float = 0.0
    zone_bottom: float = 0.0
    zone_top: float = 0.0


class RetradeEngine:
    """Retrade sweep + RSM + trigger yönetimi.

    PaperTrader'dan DI ile alır:
      - rsm_retrade: sembole özel RetraceStateMachine (düşük min_fvg_size ile)
    """

    def __init__(self, rsm_retrade: RetraceStateMachine):
        self.rsm = rsm_retrade

    # ── 1. Sweep tespiti ─────────────────────────────────────

    @staticmethod
    def detect_sweep(
        bars_15m: list[Bar],
        current: Bar,
        retrade_side: Literal["long", "short"],
    ) -> RetradeSweepResult:
        """Son 4 barda pivot bazlı sweep tespiti.

        Orijinal _check_retrade() sweep tarama döngüsü ile birebir aynı:
          - short retrade: high > recent_high ve close < recent_high → bearish sweep
          - long retrade: low < recent_low ve close > recent_low → bullish sweep
        """
        scan_bar = current.index
        for check_idx in range(max(0, scan_bar - 4), scan_bar + 1):
            if check_idx < 0 or check_idx >= len(bars_15m):
                continue
            cb = bars_15m[check_idx]
            start_idx = max(0, check_idx - 5)
            if start_idx >= check_idx:
                continue
            recent_bars = bars_15m[start_idx:check_idx]
            if not recent_bars:
                continue

            if retrade_side == "short":
                recent_high = max(b.high for b in recent_bars)
                if cb.high > recent_high and cb.close < recent_high:
                    return RetradeSweepResult(
                        found=True,
                        sweep_bar_idx=check_idx,
                        sweep_dir="bearish",
                    )
            else:
                recent_low = min(b.low for b in recent_bars)
                if cb.low < recent_low and cb.close > recent_low:
                    return RetradeSweepResult(
                        found=True,
                        sweep_bar_idx=check_idx,
                        sweep_dir="bullish",
                    )

        return RetradeSweepResult()

    # ── 2. RSM progression ───────────────────────────────────

    def progress_rsm(
        self, bars_15m: list[Bar], sweep_bar_idx: int, sweep_dir: str
    ) -> None:
        """Retrade RSM state machine'ini ilerlet.

        Orijinal _check_retrade() RSM progression ile birebir aynı.
        """
        if self.rsm.state_name == "IDLE":
            self.rsm.on_sweep(
                direction=sweep_dir,
                level=0.0,
                bar_index=bars_15m[sweep_bar_idx].index,
            )

        if self.rsm.state_name == "SWEEP_DETECTED":
            sweep_bar = bars_15m[sweep_bar_idx]
            sweep_chunk = (
                bars_15m[
                    max(0, sweep_bar_idx - cfg.RETRADE_SWEEP_WINDOW) : sweep_bar_idx + 1
                ]
                if sweep_bar_idx >= cfg.RETRADE_SWEEP_WINDOW
                else bars_15m
            )
            self.rsm.on_sweep_confirmed(sweep_chunk, sweep_bar)

    # ── 3. Trigger check + filtreler ─────────────────────────

    def evaluate_trigger(
        self,
        current: Bar,
        sweep_bar_idx: int,
        retrade_entry_bar: int,
    ) -> RetradeDecision:
        """Trigger hazırsa session ve entry-bar filtrelerini uygula.

        Orijinal _check_retrade() FIX #6a ve FIX #6b ile birebir aynı.
        """
        if not self.rsm.can_trigger():
            return RetradeDecision(decision="WAIT")

        # FIX #6a: Session filtresi (sadece LONDON+NEWYORK)
        phase = detect_phase_from_timestamp(current.timestamp)
        if phase not in (SessionPhase.NEWYORK, SessionPhase.LONDON):
            log.info("[SKIP] retrade trigger — session %s, atlandi (rsm reset)", phase)
            self.rsm.reset()
            return RetradeDecision(decision="SKIP", reason="session_filter")

        # FIX #6b: Sweep, primary entry barından sonra oluşmuş olmalı
        if sweep_bar_idx <= (retrade_entry_bar or 0):
            log.info(
                "[RETRADE] sweep (bar=%d) primary entry barından (bar=%d) önce — atlandı",
                sweep_bar_idx,
                retrade_entry_bar or 0,
            )
            self.rsm.reset()
            return RetradeDecision(decision="SKIP", reason="sweep_before_entry")

        return RetradeDecision(decision="TRIGGER")

    # ── 4. LHR fallback zone/SL/TP hesaplama (Faz 4.3) ─────

    @staticmethod
    def try_lhr_fallback(
        retrade_side: Literal["long", "short"],
        current_close: float,
        atr_val: float,
        london_high: float,
        london_low: float,
        tp_rr: float,
    ) -> LHRFallbackResult:
        """LHR zone hesapla, fiyat zone içindeyse SL/TP belirle.

        Orijinal _check_retrade() içindeki long/short LHR blokları ile
        birebir aynı mantık — iki kopya yerine tek ortak metot.

        Args:
            retrade_side: "long" veya "short"
            current_close: Güncel bar kapanış fiyatı
            atr_val: ATR değeri
            london_high: Londra seansı yüksek seviyesi
            london_low: Londra seansı düşük seviyesi
            tp_rr: TP risk-ödül oranı (sym_cfg["TP_RR"])

        Returns:
            LHRFallbackResult — in_zone=True ise SL/TP/zone değerleri dolu.
        """
        if retrade_side == "short":
            if london_high <= 0:
                return LHRFallbackResult()
            zone_bottom = london_high * (1 - cfg.LHR_RETEST_PCT)
            zone_top = london_high
            if not (zone_bottom <= current_close <= zone_top):
                return LHRFallbackResult()
            lhr_sl = london_high + atr_val * cfg.LHR_RISK_ATR_MULT
            lhr_tp = (
                london_low
                if london_low < current_close
                else current_close - atr_val * cfg.LHR_RISK_ATR_MULT * tp_rr
            )
            return LHRFallbackResult(
                in_zone=True,
                side="short",
                sl=lhr_sl,
                tp=lhr_tp,
                zone_bottom=zone_bottom,
                zone_top=zone_top,
            )
        else:  # long
            if london_low >= float("inf"):
                return LHRFallbackResult()
            zone_bottom = london_low
            zone_top = london_low * (1 + cfg.LHR_RETEST_PCT)
            if not (zone_bottom <= current_close <= zone_top):
                return LHRFallbackResult()
            lhr_sl = london_low - atr_val * cfg.LHR_RISK_ATR_MULT
            lhr_tp = (
                london_high
                if london_high > current_close
                else current_close + atr_val * cfg.LHR_RISK_ATR_MULT * tp_rr
            )
            return LHRFallbackResult(
                in_zone=True,
                side="long",
                sl=lhr_sl,
                tp=lhr_tp,
                zone_bottom=zone_bottom,
                zone_top=zone_top,
            )

    # ── 5. Retrade arm (Faz 6.1) ──────────────────────────────

    @staticmethod
    def arm_retrade(
        sym: str,
        trade: dict,
        ss: SessionState,
        pl_callback: Callable[[str, str, str], None],
    ) -> bool:
        """Exit sonrası retrade kolunu kur.

        Guard kontrolleri:
          - recovered pozisyon → skip
          - zaten retrade → skip
          - trades_today != 0,1 → skip
          - zaten armed → skip

        Returns: True if retrade was armed, False otherwise.
        """
        from state_manager import save_retrade_arm

        if trade.get("is_recovered"):
            log.info(
                "[SKIP] %s retrade arm — recovered position, referans bar yok", sym
            )
            return False
        if trade.get("is_retrade", False):
            log.info("[SKIP] %s retrade arm — bu trade zaten retrade", sym)
            return False
        if ss.trades_today not in (0, 1):
            log.info(
                "[SKIP] %s retrade arm — trades_today=%d (beklenen=0 veya 1)",
                sym,
                ss.trades_today,
            )
            return False
        if ss.retrade_armed:
            log.info("[SKIP] %s retrade arm — zaten armed", sym)
            return False

        # Arm
        ss.retrade_side = "short" if trade["side"] == "long" else "long"
        ss.retrade_sweep_level = 0.0
        ss.retrade_entry_bar = trade.get("entry_bar_index", trade.get("entry_bar", 0))
        ss.retrade_armed = True
        ss.retrade_fvg_attempts = 0
        ss.retrade_mode = "fvg"
        save_retrade_arm(sym, ss.retrade_side, ss.retrade_entry_bar)
        pl_callback(
            sym,
            "rt_arm",
            f"\U0001f6a9 RETRADE ARMED | ters yon: {ss.retrade_side.upper()}",
        )
        return True
