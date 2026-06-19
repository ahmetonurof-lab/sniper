"""
models.py — Sniper Backtest Foundation Layer
Bar, FVG, CHoCH, SwingPoint, FVGQuality, AnalysisResult dataclass'lari.
Bagimlilik: YOK
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final, Literal

logger = logging.getLogger("sniper.models")

_TF_PARAMS: Final[dict[str, tuple[int, int, int]]] = {
    "1m": (8, 5, 1),
    "3m": (10, 8, 1),
    "5m": (12, 10, 1),
    "15m": (15, 10, 1),
    "30m": (18, 12, 2),
    "1h": (20, 14, 2),
    "2h": (22, 16, 2),
    "4h": (25, 20, 2),
    "1d": (30, 30, 3),
}
_TF_DEFAULT: Final[tuple[int, int, int]] = (15, 10, 2)


def tf_params(timeframe: str) -> tuple[int, int, int]:
    return _TF_PARAMS.get(timeframe.lower(), _TF_DEFAULT)


CHoCH_SFP_FOLLOWTHROUGH: Final[int] = 2

DEFAULT_LOOKBACK: Final[int] = 100
MAX_FVG_AGE_BARS: Final[int] = 500
MIN_FVG_SIZE: Final[float] = 0.0
ATR_PERIOD: Final[int] = 14


@dataclass(frozen=True)
class Bar:
    index: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True
    timestamp: int = 0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def range(self) -> float:
        return self.high - self.low

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"Bar[{self.index}]: high ({self.high}) < low ({self.low})")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"Bar[{self.index}]: open ({self.open}) out of [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"Bar[{self.index}]: close ({self.close}) out of [low, high]")


@dataclass(frozen=True)
class FVG:
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    real_index: int
    timeframe: str = "5m"
    filled: bool = False
    invalidated: bool = False
    _next_check_abs: int = field(default=-1, repr=False, init=False)

    def __post_init__(self) -> None:
        if self.top <= self.bottom:
            raise ValueError(f"FVG[{self.real_index}]: top ({self.top}) <= bottom ({self.bottom})")
        if self._next_check_abs < 0:
            object.__setattr__(self, "_next_check_abs", self.real_index + 2)

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def is_active(self) -> bool:
        return not self.invalidated and not self.filled

    def mark_filled(self, price: float) -> bool:
        if self.direction == "bullish":
            if price <= self.bottom:
                object.__setattr__(self, "filled", True)
                return True
        else:
            if price >= self.top:
                object.__setattr__(self, "filled", True)
                return True
        return False


@dataclass(frozen=True)
class CHoCH:
    direction: Literal["bullish", "bearish"]
    level: float
    bar_index: int
    pivot_bar_index: int
    timeframe: str = "5m"
    strength: float = 0.0
    timestamp: int = 0

    def __post_init__(self) -> None:
        if self.bar_index < self.pivot_bar_index:
            raise ValueError(f"CHoCH[{self.bar_index}]: bar_index < pivot_bar_index " f"({self.pivot_bar_index})")

    def age_bars(self, current_index: int) -> int:
        return max(0, current_index - self.bar_index)


@dataclass(frozen=True)
class SwingPoint:
    kind: Literal["high", "low"]
    price: float
    bar_index: int
    mitigated: bool = False

    def mark_mitigated(self, price: float) -> bool:
        if self.kind == "high" and price > self.price:
            object.__setattr__(self, "mitigated", True)
            return True
        if self.kind == "low" and price < self.price:
            object.__setattr__(self, "mitigated", True)
            return True
        return False


@dataclass(frozen=True)
class FVGQuality:
    displacement: float
    fvg_size: float
    sweep: float
    retest: float
    score: float

    def __post_init__(self) -> None:
        for name, val in [
            ("displacement", self.displacement),
            ("fvg_size", self.fvg_size),
            ("sweep", self.sweep),
            ("retest", self.retest),
            ("score", self.score),
        ]:
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"FVGQuality.{name} = {val} out of range [0.0, 1.0]")

    @property
    def is_valid(self) -> bool:
        return self.score > 0.0


@dataclass
class AnalysisResult:
    symbol: str
    direction: Literal["long", "short"] | None = None
    choch: CHoCH | None = None
    fvg: FVG | None = None
    fvg_quality: FVGQuality | None = None
    retest_ready: bool = False
    adx_value: float = 0.0
    ema100: float = 0.0
    ema200: float = 0.0
    close_d1: float = 0.0
    vp_levels: object | None = None
    entry_zone: float | None = None
    entry_zone_type: Literal["proximal", "ce"] | None = None
    armed: bool = False
    stop_loss: float | None = None
    tp_level: float | None = None

    @property
    def expected_choch_direction(self) -> Literal["bullish", "bearish"] | None:
        if self.direction == "long":
            return "bullish"
        if self.direction == "short":
            return "bearish"
        return None

    def is_valid_signal(
        self,
        _threshold: float | None = None,
        adx: float | None = None,
    ) -> bool:
        return self.direction is not None and self.fvg_quality is not None

    def summary(self) -> str:
        choch_str = f"choch={self.choch.direction}@{self.choch.level:.2f}" if self.choch else "choch=None"
        fvg_str = f"fvg=[{self.fvg.bottom:.2f}-{self.fvg.top:.2f}]" if self.fvg else "fvg=None"
        score_str = f"score={self.fvg_quality.score:.3f}" if self.fvg_quality else "quality=None"
        return (
            f"{self.symbol} | {self.direction} | {choch_str} | {fvg_str} | "
            f"{score_str} | adx={self.adx_value:.1f} | armed={self.armed}"
        )
