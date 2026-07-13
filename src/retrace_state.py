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
    bars_15m: list[Bar],
    lookback: int = 100,
    min_fvg_size: float = 10.0,
    max_wick_ratio: float = 1.0,
) -> list[HTFFVG]:
    """Son 15m bar'ler icinde FVG'leri tara. min_fvg_size coin'e gore dinamik."""
    segment = bars_15m[-lookback:] if len(bars_15m) > lookback else bars_15m
    if len(segment) < 5:
        return []

    fvgs = detect_fvgs(
        segment,
        lookback=len(segment),
        timeframe="15m",
        min_fvg_size=min_fvg_size,
        max_wick_ratio=max_wick_ratio,
    )
    levels = [HTFFVG(f.top, f.bottom, f.direction, f.real_index) for f in fvgs]
    levels.sort(key=lambda x: x.bar_index)
    return levels[-10:] if len(levels) > 10 else levels


class RetraceStateMachine:
    def __init__(self, max_wick_ratio: float = 1.0):
        self.state: RetraceState = RetraceState.IDLE
        self.direction: Literal["bullish", "bearish"] | None = None
        self.sweep_level: float | None = None
        self.trigger_fvg: HTFFVG | None = None
        self._max_wick_ratio = max_wick_ratio
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

    def on_sweep_confirmed(
        self, bars_15m: list[Bar], sweep_bar: Bar, atr_val: float = 0.0
    ):
        """Sweep onaylandiginda FVG taramasi + govde-ici kapanis onayi.

        min_fvg_size artik ATR-bazlı dinamik: atr_val * FVG_MIN_SIZE_ATR_MULT
        """
        if self.state != RetraceState.SWEEP_DETECTED:
            return

        last = sweep_bar

        # ── Sweep invalidation: likidite okumasi ters yonde kirilirsa TAM reset ──
        if self.sweep_level is not None:
            if self.direction == "bullish" and last.close < self.sweep_level:
                logger.info(
                    f"[RST] SWEEP INVALID | close={last.close:.2f} < sweep={self.sweep_level:.2f} -> IDLE"
                )
                self.reset()
                return
            if self.direction == "bearish" and last.close > self.sweep_level:
                logger.info(
                    f"[RST] SWEEP INVALID | close={last.close:.2f} > sweep={self.sweep_level:.2f} -> IDLE"
                )
                self.reset()
                return

        # ── ATR-bazlı dinamik FVG eşiği ──
        import config as _cfg

        min_fvg_size = max(atr_val * _cfg.FVG_MIN_SIZE_ATR_MULT, 1e-8)

        htf_fvgs = scan_htf_fvgs(
            bars_15m,
            lookback=100,
            min_fvg_size=min_fvg_size,
            max_wick_ratio=self._max_wick_ratio,
        )
        if not htf_fvgs:
            logger.info("[FVG-DEBUG] %s no FVG found in last 100 bars", self.direction)
            return  # sweep hala gecerli, bir sonraki bar'i bekle — RESET YOK

        for fvg in reversed(htf_fvgs):
            # ── Debug: her FVG adayini logla ──
            fvg_first = max(0, fvg.bar_index - 1)
            fvg_third = fvg.bar_index + 1
            _fvg_debug = (
                f"[FVG-DEBUG] candidate |"
                f" dir={fvg.direction} |"
                f" bars=[{fvg_first},{fvg.bar_index},{fvg_third}] |"
                f" FVG=[{fvg.bottom:.4f}-{fvg.top:.4f}] |"
                f" sweep_bar_idx={last.index} |"
                f" sweep_dir={self.direction}"
            )
            if fvg.direction != self.direction:
                logger.info("%s | reject=wrong_direction", _fvg_debug)
                continue
            if fvg.bar_index >= last.index:
                logger.info(
                    "%s | reject=FVG_after_sweep (bar_idx=%d >= sweep=%d)",
                    _fvg_debug,
                    fvg.bar_index,
                    last.index,
                )
                continue

            if self.direction == "bullish":
                wick_touched = last.low <= fvg.top
                body_broke_down = last.close < fvg.bottom
            else:
                wick_touched = last.high >= fvg.bottom
                body_broke_down = last.close > fvg.top

            if not wick_touched:
                logger.info("%s | reject=wick_not_touched", _fvg_debug)
                continue
            if body_broke_down:
                logger.info("%s | reject=body_broke_fvg", _fvg_debug)
                continue

            # NOTE: fvg_close_confirmed gecici olarak devre disi — backtest karsilastirmasi icin
            # if not fvg_close_confirmed(
            #     fvg.direction, fvg.top, fvg.bottom, fvg.bar_index, bars_15m
            # ):
            #     logger.info("%s | reject=no_close_inside_fvg", _fvg_debug)
            #     continue

            logger.info("%s | ACCEPT=trigger_ready", _fvg_debug)
            self.state = RetraceState.TRIGGER_READY
            self.trigger_fvg = fvg
            self._mark_sweep_used()
            return

        return  # bu bar'da hicbir FVG tetiklenmedi — SWEEP_DETECTED'de kal, reset YOK
