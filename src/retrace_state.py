"""
retrace_state.py — HTF FVG Wick Rejection State Machine.
Sadece FVG kullanilir (OB yok). ADX filtresi kaldirildi.
Sweep + FVG wick rejection = aninda TRIGGER_READY.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Literal

from fvg import detect_fvgs
from models import Bar

logger = logging.getLogger("nexus.retrace_state")


class RetraceState(Enum):
    IDLE = auto()
    SWEEP_DETECTED = auto()
    TRIGGER_READY = auto()


class HTFFVG:
    """HTF FVG key level."""

    def __init__(self, top: float, bottom: float, direction: str, bar_index: int):
        self.top = top
        self.bottom = bottom
        self.direction = direction
        self.bar_index = bar_index

    def __repr__(self):
        return f"FVG([{self.bottom:.2f}-{self.top:.2f}] dir={self.direction} bar={self.bar_index})"


def scan_htf_fvgs(
    bars_15m: list[Bar], lookback: int = 100, min_fvg_size: float = 10.0
) -> list[HTFFVG]:
    """Son 15m bar'ler icinde FVG'leri tara. min_fvg_size coin'e gore dinamik."""
    segment = bars_15m[-lookback:] if len(bars_15m) > lookback else bars_15m
    if len(segment) < 5:
        return []

    fvgs = detect_fvgs(
        segment, lookback=len(segment), timeframe="15m", min_fvg_size=min_fvg_size
    )
    levels = [HTFFVG(f.top, f.bottom, f.direction, f.real_index) for f in fvgs]
    levels.sort(key=lambda x: x.bar_index)
    return levels[-10:] if len(levels) > 10 else levels


class RetraceStateMachine:
    def __init__(self, min_fvg_size: float = 10.0):
        self.state: RetraceState = RetraceState.IDLE
        self.direction: Literal["bullish", "bearish"] | None = None
        self.sweep_level: float | None = None
        self.trigger_fvg: HTFFVG | None = None
        self._min_fvg_size = min_fvg_size
        self._pending_sweep_id: str | None = None

    @property
    def state_name(self) -> str:
        return self.state.name

    def can_trigger(self) -> bool:
        return self.state == RetraceState.TRIGGER_READY

    def _mark_sweep_used(self):
        if self._pending_sweep_id is not None:
            try:
                from state_manager import mark_sweep_used

                mark_sweep_used(self._pending_sweep_id)
            except Exception:
                pass
            self._pending_sweep_id = None

    def reset(self):
        self.state = RetraceState.IDLE
        self.direction = None
        self.sweep_level = None
        self.trigger_fvg = None
        self._pending_sweep_id = None

    def on_sweep(
        self,
        direction: Literal["bullish", "bearish"],
        level: float,
        bar_index: int | None = None,
    ):
        if self.state != RetraceState.IDLE:
            return

        # ── Sweep tekilleştirme: aynı sweep bar'ı restart sonrası tekrar tetiklenmesin ──
        if bar_index is not None:
            try:
                from state_manager import is_sweep_used

                sweep_id = f"{direction}_{bar_index}"
                if is_sweep_used(sweep_id):
                    logger.info(
                        f"[RST] SWEEP SKIP | sweep_id={sweep_id} zaten bugün kullanıldı"
                    )
                    return
            except Exception as e:
                logger.warning(f"[RST] sweep state kontrol hatası (geçiliyor): {e}")
        # ── Sweep tekilleştirme sonu ──

        self.state = RetraceState.SWEEP_DETECTED
        self.direction = direction
        self.sweep_level = level
        self._pending_sweep_id = (
            f"{direction}_{bar_index}" if bar_index is not None else None
        )
        logger.info(f"[RST] SWEEP_DETECTED | dir={direction} level={level:.2f}")

    def on_sweep_confirmed(self, bars_15m: list[Bar], sweep_bar: Bar):
        """Sweep onaylandiginda aninda FVG taramasi + wick rejection."""
        if self.state != RetraceState.SWEEP_DETECTED:
            return

        htf_fvgs = scan_htf_fvgs(
            bars_15m, lookback=100, min_fvg_size=self._min_fvg_size
        )
        if not htf_fvgs:
            self.reset()
            return

        last = sweep_bar
        min(last.open, last.close)
        max(last.open, last.close)

        for fvg in reversed(htf_fvgs):
            if fvg.direction != self.direction:
                continue
            if fvg.bar_index >= last.index:
                continue

            if self.direction == "bullish":
                wick_touched = last.low <= fvg.top
                body_broke_down = last.close < fvg.bottom
                if wick_touched and not body_broke_down:
                    self.state = RetraceState.TRIGGER_READY
                    self.trigger_fvg = fvg
                    self._mark_sweep_used()
                    return
            else:
                wick_touched = last.high >= fvg.bottom
                body_broke_up = last.close > fvg.top
                if wick_touched and not body_broke_up:
                    self.state = RetraceState.TRIGGER_READY
                    self.trigger_fvg = fvg
                    self._mark_sweep_used()
                    return

        self.reset()
