"""
fvg.py — Fair Value Gap Motoru.
Bagimli: models.py (FVG, Bar, FVGQuality, SwingPoint)
"""

from __future__ import annotations

import logging
from typing import Final, Literal

from models import FVG, Bar

logger = logging.getLogger("sniper.fvg")

DEFAULT_LOOKBACK: Final[int] = 100
MAX_FVG_AGE_BARS: Final[int] = 500
MIN_FVG_SIZE: Final[float] = 1e-8
ATR_PERIOD: Final[int] = 14

_SYMBOL_COUNTERS: dict[str, int] = {}


def _wick_ratio_ok(b_curr: Bar, direction: str, max_ratio: float) -> bool:
    """Impulse mumun fitil oranini kontrol et. (wick / total_range) > max_ratio ise False."""
    total_range = b_curr.high - b_curr.low
    if total_range <= 0:
        return True
    if direction == "bullish":
        wick = b_curr.high - max(b_curr.open, b_curr.close)
    else:
        wick = min(b_curr.open, b_curr.close) - b_curr.low
    return (wick / total_range) <= max_ratio


def fvg_close_confirmed(
    direction: str,
    top: float,
    bottom: float,
    bar_index: int,
    all_bars: list[Bar],
) -> bool:
    """FVG icinde kapali 15m mumu var mi?
    Wick yetmez, govde kapanisi lazim. Far-side close gorurse False doner."""
    for b in all_bars:
        if b.index < bar_index + 2:
            continue
        if direction == "bullish":
            if b.close < bottom:
                return False
            if bottom <= b.close <= top:
                return True
        else:
            if b.close > top:
                return False
            if bottom <= b.close <= top:
                return True
    return False


def detect_fvgs(
    bars: list[Bar],
    lookback: int = DEFAULT_LOOKBACK,
    timeframe: str = "5m",
    min_fvg_size: float = MIN_FVG_SIZE,
    since_index: int | None = None,
    max_wick_ratio: float = 1.0,
) -> list[FVG]:
    segment = bars[-lookback:] if len(bars) > lookback else bars
    fvgs: list[FVG] = []

    for i in range(1, len(segment) - 1):
        b_prev = segment[i - 1]
        b_curr = segment[i]
        b_next = segment[i + 1]

        if not b_next.is_closed:
            continue

        if b_next.high <= b_curr.high and b_next.low >= b_curr.low:
            continue

        gap_bull = b_next.low - b_prev.high
        gap_bear = b_prev.low - b_next.high

        if gap_bull > 0:
            if not _wick_ratio_ok(b_curr, "bullish", max_wick_ratio):
                continue
            fvg = FVG(
                direction="bullish",
                top=b_next.low,
                bottom=b_prev.high,
                real_index=b_curr.index,
                timeframe=timeframe,
            )
            if fvg.size >= min_fvg_size:
                if since_index is None or fvg.real_index >= since_index:
                    fvgs.append(fvg)

        elif gap_bear > 0:
            if not _wick_ratio_ok(b_curr, "bearish", max_wick_ratio):
                continue
            fvg = FVG(
                direction="bearish",
                top=b_prev.low,
                bottom=b_next.high,
                real_index=b_curr.index,
                timeframe=timeframe,
            )
            if fvg.size >= min_fvg_size:
                if since_index is None or fvg.real_index >= since_index:
                    fvgs.append(fvg)

    return fvgs


def update_fvg_states(
    fvgs: list[FVG],
    bars: list[Bar],
) -> None:
    if not bars:
        return

    first_abs = bars[0].index
    last_abs = bars[-1].index

    for fvg in fvgs:
        if fvg.invalidated or fvg.real_index < first_abs:
            continue

        scan_from_abs = max(
            getattr(fvg, "_next_check_abs", fvg.real_index + 2), fvg.real_index + 2
        )

        for abs_i in range(scan_from_abs, last_abs + 1):
            list_pos = abs_i - first_abs
            if not (0 <= list_pos < len(bars)):
                continue
            b = bars[list_pos]
            if not b.is_closed:
                break

            if fvg.direction == "bullish":
                if b.close < fvg.bottom:
                    object.__setattr__(fvg, "invalidated", True)
                    object.__setattr__(fvg, "filled", False)
                    break
                elif fvg.bottom <= b.close <= fvg.top:
                    object.__setattr__(fvg, "filled", True)
                else:
                    object.__setattr__(fvg, "filled", False)
            else:
                if b.close > fvg.top:
                    object.__setattr__(fvg, "invalidated", True)
                    object.__setattr__(fvg, "filled", False)
                    break
                elif fvg.bottom <= b.close <= fvg.top:
                    object.__setattr__(fvg, "filled", True)
                else:
                    object.__setattr__(fvg, "filled", False)

        if not fvg.invalidated:
            object.__setattr__(fvg, "_next_check_abs", last_abs)


def find_latest_unfilled_fvg(
    fvgs: list[FVG],
    direction: Literal["bullish", "bearish"],
    min_fvg_size: float = MIN_FVG_SIZE,
) -> FVG | None:
    matches = [
        f
        for f in fvgs
        if f.direction == direction
        and not f.filled
        and not f.invalidated
        and f.size >= min_fvg_size
    ]
    if not matches:
        return None
    return max(matches, key=lambda f: f.real_index)


def is_retesting_fvg(
    fvg: FVG | None,
    current_bar: Bar,
    atr: float,
    atr_buffer_factor: float = 0.10,
) -> bool:
    if fvg is None or not fvg.is_active:
        return False

    body_high = max(current_bar.open, current_bar.close)
    body_low = min(current_bar.open, current_bar.close)
    buffer = max(atr * atr_buffer_factor, fvg.size * 0.10)

    if fvg.direction == "bullish":
        lower_bound = max(fvg.bottom - buffer, 0.0)
        wick_touches = (
            current_bar.low <= fvg.top + buffer and current_bar.low >= lower_bound
        )
        body_safe = body_low >= lower_bound
        return wick_touches and body_safe
    else:
        lower_bound = max(fvg.bottom - buffer, 0.0)
        wick_touches = (
            current_bar.high >= lower_bound and current_bar.high <= fvg.top + buffer
        )
        body_safe = body_high <= fvg.top + buffer
        return wick_touches and body_safe


def cleanup_fvgs(
    fvgs: list[FVG],
    current_abs: int,
    max_age: int = MAX_FVG_AGE_BARS,
) -> list[FVG]:
    before = len(fvgs)
    kept = [
        f
        for f in fvgs
        if not f.invalidated
        and not (f.filled and (current_abs - f.real_index) > max_age)
        and not (not f.filled and (current_abs - f.real_index) > max_age * 2)
    ]
    if before != len(kept):
        logger.info(
            "[FVG-CLEANUP] %d FVG temizlendi (%d -> %d).",
            before - len(kept),
            before,
            len(kept),
        )
    return kept


def refresh_fvg_list(
    fvgs: list[FVG],
    bars: list[Bar],
    lookback: int = DEFAULT_LOOKBACK,
    min_fvg_size: float = MIN_FVG_SIZE,
    max_age: int = MAX_FVG_AGE_BARS,
    timeframe: str = "5m",
    cleanup_every: int = 50,
    symbol: str = "default",
) -> list[FVG]:
    _SYMBOL_COUNTERS[symbol] = _SYMBOL_COUNTERS.get(symbol, 0) + 1
    call_n = _SYMBOL_COUNTERS[symbol]

    existing_indices = {f.real_index for f in fvgs}
    new_fvgs = [
        f
        for f in detect_fvgs(
            bars, lookback=lookback, timeframe=timeframe, min_fvg_size=min_fvg_size
        )
        if f.real_index not in existing_indices
    ]
    fvgs.extend(new_fvgs)
    update_fvg_states(fvgs, bars)

    if call_n % cleanup_every == 0 and bars:
        fvgs = cleanup_fvgs(fvgs, current_abs=bars[-1].index, max_age=max_age)

    return fvgs
