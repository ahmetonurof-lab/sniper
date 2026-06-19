"""
pivot.py — Swing High/Low tespiti ve durum yonetimi.
Bagimli: models.py (Bar, SwingPoint) — TEK YONLU, dongusuz.
"""

from __future__ import annotations

import logging
from typing import Final, Literal

from models import Bar, SwingPoint

logger = logging.getLogger("sniper.pivot")

DEFAULT_LEFT: Final[int] = 3
DEFAULT_RIGHT: Final[int] = 3
MAX_PIVOT_AGE_BARS: Final[int] = 500


def find_swing_highs(
    bars: list[Bar],
    left: int = DEFAULT_LEFT,
    right: int = DEFAULT_RIGHT,
) -> list[SwingPoint]:
    if len(bars) < left + right + 1:
        return []

    result: list[SwingPoint] = []

    for i in range(left, len(bars) - right):
        bar = bars[i]

        if not bar.is_closed:
            continue

        candidate_high = bar.high

        left_ok = all(bars[i - j].high <= candidate_high for j in range(1, left + 1))
        if not left_ok:
            continue

        right_ok = all(bars[i + j].high <= candidate_high for j in range(1, right + 1))
        if not right_ok:
            continue

        result.append(SwingPoint(kind="high", price=candidate_high, bar_index=bar.index))

    return result


def find_swing_lows(
    bars: list[Bar],
    left: int = DEFAULT_LEFT,
    right: int = DEFAULT_RIGHT,
) -> list[SwingPoint]:
    if len(bars) < left + right + 1:
        return []

    result: list[SwingPoint] = []

    for i in range(left, len(bars) - right):
        bar = bars[i]

        if not bar.is_closed:
            continue

        candidate_low = bar.low

        left_ok = all(bars[i - j].low >= candidate_low for j in range(1, left + 1))
        if not left_ok:
            continue

        right_ok = all(bars[i + j].low >= candidate_low for j in range(1, right + 1))
        if not right_ok:
            continue

        result.append(SwingPoint(kind="low", price=candidate_low, bar_index=bar.index))

    return result


class SwingStateManager:
    def __init__(self) -> None:
        self._highs: list[SwingPoint] = []
        self._lows: list[SwingPoint] = []

    def ingest(self, bars: list[Bar], left: int = DEFAULT_LEFT, right: int = DEFAULT_RIGHT) -> None:
        existing_high_idx = {p.bar_index for p in self._highs}
        existing_low_idx = {p.bar_index for p in self._lows}

        for sp in find_swing_highs(bars, left, right):
            if sp.bar_index not in existing_high_idx:
                self._highs.append(sp)
                existing_high_idx.add(sp.bar_index)

        for sp in find_swing_lows(bars, left, right):
            if sp.bar_index not in existing_low_idx:
                self._lows.append(sp)
                existing_low_idx.add(sp.bar_index)

        self._highs.sort(key=lambda p: p.bar_index)
        self._lows.sort(key=lambda p: p.bar_index)

        logger.debug(
            "[PIVOT] ingest: +%d highs, +%d lows -> total highs=%d, lows=%d",
            len([p for p in self._highs if p.bar_index >= bars[0].index]),
            len([p for p in self._lows if p.bar_index >= bars[0].index]),
            len(self._highs),
            len(self._lows),
        )

    def mark_mitigated(self, kind: Literal["high", "low"], bar_index: int) -> bool:
        pool = self._highs if kind == "high" else self._lows
        for p in pool:
            if p.bar_index == bar_index and not p.mitigated:
                object.__setattr__(p, "mitigated", True)
                logger.debug("[PIVOT] Mitigated: %s @ bar_index=%d", kind, bar_index)
                return True
        return False

    def active_highs(self) -> list[SwingPoint]:
        return [p for p in self._highs if not p.mitigated]

    def active_lows(self) -> list[SwingPoint]:
        return [p for p in self._lows if not p.mitigated]

    def get_latest_active(self, kind: Literal["high", "low"]) -> SwingPoint | None:
        pool = self._highs if kind == "high" else self._lows
        for p in reversed(pool):
            if not p.mitigated:
                return p
        return None

    def cleanup(self, max_age: int = MAX_PIVOT_AGE_BARS, current_abs: int | None = None) -> None:
        if not self._highs and not self._lows:
            return

        if current_abs is None:
            all_indices = [p.bar_index for p in self._highs + self._lows]
            current_abs = max(all_indices) if all_indices else 0

        before_h, before_l = len(self._highs), len(self._lows)

        self._highs = [p for p in self._highs if (current_abs - p.bar_index) <= max_age]
        self._lows = [p for p in self._lows if (current_abs - p.bar_index) <= max_age]

        removed_h = before_h - len(self._highs)
        removed_l = before_l - len(self._lows)

        if removed_h > 0 or removed_l > 0:
            logger.debug(
                "[PIVOT] cleanup: -%d highs, -%d lows (max_age=%d, current_abs=%d)",
                removed_h,
                removed_l,
                max_age,
                current_abs,
            )

    def reset(self) -> None:
        self._highs.clear()
        self._lows.clear()
        logger.info("[PIVOT] State reset: tum pivot hafizasi temizlendi")

    @property
    def total_active(self) -> int:
        return sum(1 for p in self._highs + self._lows if not p.mitigated)

    @property
    def total_stored(self) -> int:
        return len(self._highs) + len(self._lows)
