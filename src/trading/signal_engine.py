"""
signal_engine.py — CBDR → Sweep → FVG → Trigger decision.

PaperTrader._on_15m_close() içindeki iki mantıksal bloğu kapsar:
  Blok 8  — RSM state progression (IDLE → SWEEP_DETECTED → TRIGGER_READY)
  Blok 10 — Trigger check + bias/session filtreleri

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - _pl() formatına dokunulmaz (PaperTrader'da kalır)
  - Import yolları kırılmayacak
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from models import Bar
from retrace_state import RetraceStateMachine, HTFFVG
from session import DailyBias, SessionPhase, SessionState, detect_phase_from_timestamp

log = logging.getLogger("sniper.signal_engine")


@dataclass
class EvalResult:
    """SignalEngine.evaluate() dönüş değeri.

    Attributes:
        decision: SKIP (filtre reddetti), WAIT (FVG henüz hazır değil), TRIGGER (entry yapılabilir)
        direction: TRIGGER kararında sweep yönü
        trigger_fvg: TRIGGER kararında wick rejection yapılan FVG
        reason: SKIP kararının sebebi (log için)
    """

    decision: Literal["SKIP", "WAIT", "TRIGGER"]
    direction: Literal["bullish", "bearish"] | None = None
    trigger_fvg: HTFFVG | None = None
    reason: str = ""


class SignalEngine:
    """CBDR/Sweep/FVG sinyal akışını yönetir.

    PaperTrader'dan DI (dependency injection) ile alır:
      - rsm: sembole özel RetraceStateMachine (primary)
      - cfg: sembole özel config dict (opsiyonel, ileride magic number'lar için)

    PaperTrader._on_15m_close() akışı:
      1. progress_rsm(bars_15m, current, ss)  → RSM state ilerlet
      2. _pl() ile FVG durumunu yazdır            → PaperTrader'da
      3. evaluate_trigger(current, ss)            → filtreler + karar
      4. EvalResult.decision == TRIGGER ise _try_entry() → PaperTrader'da
    """

    def __init__(self, rsm: RetraceStateMachine):
        self.rsm = rsm

    # ── Blok 8: RSM state progression ──────────────────────────

    def progress_rsm(self, bars_15m: list[Bar], current: Bar, ss: SessionState) -> None:
        """RSM state machine'i ilerlet: IDLE → SWEEP_DETECTED → TRIGGER_READY.

        Orijinal _on_15m_close() Blok 8 ile birebir aynı mantık.
        """
        if self.rsm.state_name == "IDLE":
            self.rsm.on_sweep(
                direction=ss.sweep_direction or "bullish",
                level=ss.sweep_level or 0.0,
                bar_index=current.index,
            )

        if self.rsm.state_name == "SWEEP_DETECTED":
            self.rsm.on_sweep_confirmed(bars_15m, current)

        ss.fvg_ready = self.rsm.state_name == "TRIGGER_READY"

    # ── Blok 10: Trigger check + filtreler ─────────────────────

    def evaluate_trigger(self, current: Bar, ss: SessionState) -> EvalResult:
        """Trigger hazırsa bias/session filtrelerini uygula, karar ver.

        Orijinal _on_15m_close() Blok 10 ile birebir aynı mantık.
        Filtreler:
          1. Bias uyuşmazlığı → SKIP + rsm.reset()
          2. NEUTRAL bias → SKIP + rsm.reset()
          3. Session LONDON/NEWYORK değilse → SKIP + rsm.reset()
        """
        if not self.rsm.can_trigger():
            return EvalResult(decision="WAIT")

        # Candle close guard — sadece kapali mum ile entry
        if not current.is_closed:
            log.info("[SKIP] trigger — bar not closed, atlandi (rsm reset)")
            self.rsm.reset()
            return EvalResult(decision="SKIP", reason="bar_not_closed")

        # Bias filter (analyzer.py ile aynı)
        if self.rsm.direction == "bullish" and ss.daily_bias == DailyBias.BEARISH:
            log.info("[SKIP] bullish trigger — bias BEARISH, atlandi (rsm reset)")
            self.rsm.reset()
            return EvalResult(decision="SKIP", reason="bias_bearish")

        if self.rsm.direction == "bearish" and ss.daily_bias == DailyBias.BULLISH:
            log.info("[SKIP] bearish trigger — bias BULLISH, atlandi (rsm reset)")
            self.rsm.reset()
            return EvalResult(decision="SKIP", reason="bias_bullish")

        if ss.daily_bias == DailyBias.NEUTRAL:
            log.info("[SKIP] trigger — bias NEUTRAL, atlandi (rsm reset)")
            self.rsm.reset()
            return EvalResult(decision="SKIP", reason="bias_neutral")

        # Session filter (analyzer.py: NEWYORK + LONDON)
        phase = detect_phase_from_timestamp(current.timestamp)
        if phase not in (SessionPhase.NEWYORK, SessionPhase.LONDON):
            log.info("[SKIP] trigger — session %s, atlandi (rsm reset)", phase)
            self.rsm.reset()
            return EvalResult(decision="SKIP", reason="session_filter")

        return EvalResult(
            decision="TRIGGER",
            direction=self.rsm.direction,
            trigger_fvg=self.rsm.trigger_fvg,
        )
