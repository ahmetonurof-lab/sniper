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


FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_TOLERANCE = 0.005  # ±0.5% — backtest ile aynı
MATCHED_FIB = {  # discount (bullish) → 0.236, premium (bearish) → 0.786
    "bullish": 0.236,
    "bearish": 0.786,
}


def _compute_fibo_level(
    fvg_midpoint: float, swing_high: float, swing_low: float
) -> float | None:
    """FVG midpoint'inin swing içindeki fibo seviyesini hesapla.

    Returns: En yakın fibo level (0.236, 0.382, 0.5, 0.618, 0.786) veya None.
    """
    rng = swing_high - swing_low
    if rng <= 0 or fvg_midpoint == 0:
        return None
    normalized = (fvg_midpoint - swing_low) / rng
    best = None
    best_dist = None
    for lvl in FIB_LEVELS:
        dist = abs(normalized - lvl)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = lvl
    return best


def _is_matched_fib(fvg_direction: str, fib_level: float | None) -> bool:
    """FVG yönü ile fibo seviyesi eşleşiyor mu?

    Matched: bullish+0.236, bearish+0.786 (backtest PF 6.99 vs 1.75).
    fib_level=None ise pass-through (filtre uygulanmaz).
    """
    if fib_level is None:
        return True
    expected = MATCHED_FIB.get(fvg_direction)
    if expected is None:
        return True
    return abs(fib_level - expected) < FIB_TOLERANCE


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
        self,
        bars_15m: list[Bar],
        sweep_bar: Bar,
        atr_val: float = 0.0,
        symbol: str = "",
    ):
        """Sweep onaylandiginda FVG taramasi + govde-ici kapanis onayi.

        min_fvg_size artik coin bazli: FVG_SIZE_MAP[symbol] veya FVG_MIN_SIZE_ATR_MULT fallback."""
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

        # ── Coin bazli dinamik FVG eşiği ──
        import config as _cfg

        min_mult = _cfg.FVG_SIZE_MAP.get(symbol, _cfg.FVG_MIN_SIZE_ATR_MULT)
        min_fvg_size = max(atr_val * min_mult, 1e-8)

        htf_fvgs = scan_htf_fvgs(
            bars_15m,
            lookback=100,
            min_fvg_size=min_fvg_size,
            max_wick_ratio=self._max_wick_ratio,
        )
        if not htf_fvgs:
            logger.info("[FVG-DEBUG] %s no FVG found in last 100 bars", self.direction)
            return  # sweep hala gecerli, bir sonraki bar'i bekle — RESET YOK

        # P1-15/fibo: swing high/low hesapla (FVG adaylarının fibo seviyesi için)
        _seg = bars_15m[-100:] if len(bars_15m) > 100 else bars_15m
        swing_high = max(b.high for b in _seg) if _seg else 0
        swing_low = min(b.low for b in _seg) if _seg else 0

        for fvg in reversed(htf_fvgs):
            # ── Debug: her FVG adayini logla ──
            fvg_first = max(0, fvg.bar_index - 1)
            fvg_third = fvg.bar_index + 1
            fvg_mid = (fvg.top + fvg.bottom) / 2
            fib_level = _compute_fibo_level(fvg_mid, swing_high, swing_low)
            _fvg_debug = (
                f"[FVG-DEBUG] candidate |"
                f" dir={fvg.direction} |"
                f" bars=[{fvg_first},{fvg.bar_index},{fvg_third}] |"
                f" FVG=[{fvg.bottom:.4f}-{fvg.top:.4f}] |"
                f" fib={fib_level} |"
                f" sweep_bar_idx={last.index} |"
                f" sweep_dir={self.direction}"
            )
            if fvg.direction != self.direction:
                logger.info("%s | reject=wrong_direction", _fvg_debug)
                continue

            # P1-15/fibo: matched pair filtresi (discount+0.236, premium+0.786)
            if not _is_matched_fib(fvg.direction, fib_level):
                expected = MATCHED_FIB.get(fvg.direction, "?")
                logger.info(
                    "%s | reject=unmatched_fib (fib=%s, expected=%s)",
                    _fvg_debug,
                    fib_level,
                    expected,
                )
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
