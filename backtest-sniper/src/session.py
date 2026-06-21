"""
session.py — CBDR + Session State Machine
AM/PM ayrımı kaldırıldı. Tek NEWYORK seansı: 13:00-22:00 UTC.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal


class SessionPhase(Enum):
    CBDR = "CBDR"
    LONDON = "LONDON"
    NEWYORK = "NEWYORK"
    CLOSED = "CLOSED"


class DailyBias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SessionState:
    def __init__(self):
        self.cbdr_body_high: float = 0.0
        self.cbdr_body_low: float = float("inf")
        self.cbdr_locked: bool = False
        self.cbdr_day: str = ""
        self.london_high: float = 0.0
        self.london_low: float = float("inf")
        self.daily_bias: DailyBias = DailyBias.NEUTRAL
        self.sweep_confirmed: bool = False
        self.sweep_direction: Literal["bullish", "bearish"] | None = None
        self.sweep_level: float | None = None
        self.trades_today: int = 0
        self.last_date: str = ""

        # Retrade state — pivot bazli LBS/SBS sweep sonrasi 2. entry icin.
        self.retrade_armed: bool = False
        self.retrade_side: Literal["long", "short"] | None = None
        self.retrade_sweep_level: float = 0.0
        self.retrade_entry_bar: int = 0

    def update(self, dt: datetime, open: float, high: float, low: float, close: float, atr: float = 0.0):
        sess = detect_phase(dt)
        h = dt.hour
        today = dt.strftime("%Y-%m-%d")

        cbdr_key = today if h >= 22 else (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        if cbdr_key != self.cbdr_day:
            self._reset_for_new_cbdr_cycle()
            self.cbdr_day = cbdr_key

        if today != self.last_date:
            self.last_date = today
            self.trades_today = 0

        if sess == SessionPhase.CBDR and not self.cbdr_locked:
            self._track_cbdr_body(open, close)

        if 2 <= h < 22 and not self.cbdr_locked and self.cbdr_body_high > 0:
            self.cbdr_locked = True

        if self.cbdr_locked and not self.sweep_confirmed:
            self._check_cbdr_sweep(high, low, close, atr)

        if sess == SessionPhase.LONDON:
            self._track_london(high, low)
        elif sess == SessionPhase.NEWYORK:
            self._track_ny(high, low)

    def _reset_for_new_cbdr_cycle(self):
        self.cbdr_body_high = 0.0
        self.cbdr_body_low = float("inf")
        self.cbdr_locked = False
        self.daily_bias = DailyBias.NEUTRAL
        self.sweep_confirmed = False
        self.sweep_direction = None
        self.sweep_level = None
        self.london_high = 0.0
        self.london_low = float("inf")
        self.retrade_armed = False
        self.retrade_side = None
        self.retrade_sweep_level = 0.0
        self.retrade_entry_bar = 0

    def _track_cbdr_body(self, open: float, close: float):
        body_high = max(open, close)
        body_low = min(open, close)
        if body_high > self.cbdr_body_high:
            self.cbdr_body_high = body_high
        if body_low < self.cbdr_body_low:
            self.cbdr_body_low = body_low

    def _track_london(self, high: float, low: float):
        if high > self.london_high:
            self.london_high = high
        if low < self.london_low:
            self.london_low = low

    def _track_ny(self, high: float, low: float):
        if self.london_high == 0:
            self.london_high = high
        elif high > self.london_high:
            self.london_high = high
        if self.london_low == float("inf"):
            self.london_low = low
        elif low < self.london_low:
            self.london_low = low

    def _check_cbdr_sweep(self, high: float, low: float, close: float, atr: float = 0.0):
        tolerance = atr * 0.5 if atr > 0 else 10.0

        if high > self.cbdr_body_high + tolerance:
            if close < self.cbdr_body_high:
                self.sweep_confirmed = True
                self.sweep_direction = "bullish"
                self.sweep_level = self.cbdr_body_high
                self.daily_bias = DailyBias.BULLISH
                return

        if low < self.cbdr_body_low - tolerance:
            if close > self.cbdr_body_low:
                self.sweep_confirmed = True
                self.sweep_direction = "bearish"
                self.sweep_level = self.cbdr_body_low
                self.daily_bias = DailyBias.BEARISH


def detect_phase(dt: datetime) -> SessionPhase:
    if isinstance(dt, int):
        return SessionPhase.CLOSED
    h = dt.hour
    if h >= 22 or h < 2:
        return SessionPhase.CBDR
    elif 2 <= h < 13:
        return SessionPhase.LONDON
    elif 13 <= h < 22:
        return SessionPhase.NEWYORK
    return SessionPhase.CLOSED


def detect_phase_from_timestamp(ts_ms: int) -> SessionPhase:
    if ts_ms <= 0:
        return SessionPhase.CLOSED
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return detect_phase(dt)
    except Exception:
        return SessionPhase.CLOSED
