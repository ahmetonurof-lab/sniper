"""
session.py — CBDR + Session State Machine
AM/PM ayrımı kaldırıldı. Tek NEWYORK seansı: 13:00-22:00 UTC.

Faz 4.2: SessionState → 3 sınıfa ayrıldı:
  - CBDRState: body tracking + bias + sweep (8 alan)
  - RangeTracker: asia/london/NY range + range_type (6 alan)
  - TradeDayState: trade counting (1 alan)

Geriye dönük uyumluluk: SessionState tüm eski API'yi korur,
içeride 3 sınıfa delegate eder.
"""

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal

import config as cfg


class SessionPhase(Enum):
    CBDR = "CBDR"
    LONDON = "LONDON"
    NEWYORK = "NEWYORK"
    CLOSED = "CLOSED"


class DailyBias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


# ═══════════════════════════════════════════════════════════════
# Faz 4.2 — 3 odaklı sınıf
# ═══════════════════════════════════════════════════════════════


class CBDRState:
    """Sadece CBDR body + bias + sweep. Immutable'a yakın.

    8 alan — tek sorumluluk: günlük CBDR yapısını izlemek.
    """

    __slots__ = (
        "body_high",
        "body_low",
        "locked",
        "day",
        "daily_bias",
        "sweep_confirmed",
        "sweep_direction",
        "sweep_level",
    )

    def __init__(self):
        self.body_high: float = 0.0
        self.body_low: float = float("inf")
        self.locked: bool = False
        self.day: str = ""
        self.daily_bias: DailyBias = DailyBias.NEUTRAL
        self.sweep_confirmed: bool = False
        self.sweep_direction: Literal["bullish", "bearish"] | None = None
        self.sweep_level: float | None = None

    def track_body(self, open: float, close: float) -> None:
        """CBDR body aralığını genişlet (open/close gövde range)."""
        body_high = open if open > close else close
        body_low = close if open > close else open
        if body_high > self.body_high:
            self.body_high = body_high
        if body_low < self.body_low:
            self.body_low = body_low

    def lock(self) -> None:
        """CBDR body'yi kilitle (artık genişlemez)."""
        self.locked = True

    def check_sweep(
        self, high: float, low: float, close: float, atr: float = 0.0
    ) -> None:
        """CBDR body kırılımı + kapanış yönüne göre sweep/bias belirle."""
        tolerance = (
            atr * cfg.CBDR_SWEEP_ATR_TOLERANCE_MULT
            if atr > 0
            else cfg.CBDR_SWEEP_DEFAULT_TOLERANCE
        )

        if high > self.body_high + tolerance:
            if close < self.body_high:
                self.sweep_confirmed = True
                self.sweep_direction = "bearish"
                self.sweep_level = self.body_high
                self.daily_bias = DailyBias.BEARISH
                return

        if low < self.body_low - tolerance:
            if close > self.body_low:
                self.sweep_confirmed = True
                self.sweep_direction = "bullish"
                self.sweep_level = self.body_low
                self.daily_bias = DailyBias.BULLISH
                return

    def reset_for_new_cycle(self) -> None:
        """Yeni CBDR döngüsü başlangıcında tüm alanları sıfırla."""
        self.body_high = 0.0
        self.body_low = float("inf")
        self.locked = False
        self.daily_bias = DailyBias.NEUTRAL
        self.sweep_confirmed = False
        self.sweep_direction = None
        self.sweep_level = None


class RangeTracker:
    """Session range'leri: Asia, London, NY high/low. Range tipi (CBDR/ASIA/DEAD).

    6 alan — tek sorumluluk: session aralıklarını izlemek.
    """

    __slots__ = (
        "asia_high",
        "asia_low",
        "london_high",
        "london_low",
        "range_type",
        "asia_checked",
    )

    def __init__(self):
        self.asia_high: float = 0.0
        self.asia_low: float = float("inf")
        self.london_high: float = 0.0
        self.london_low: float = float("inf")
        self.range_type: str = "CBDR"
        self.asia_checked: bool = False

    def track_asia(self, high: float, low: float) -> None:
        """Asya seansı (02:00-08:00 UTC) high/low izle."""
        if high > self.asia_high:
            self.asia_high = high
        if low < self.asia_low:
            self.asia_low = low

    def track_london(self, high: float, low: float) -> None:
        """Londra seansı (02:00-13:00 UTC) high/low izle.
        Aynı zamanda asia range'ini de genişletir (overlap).
        """
        if high > self.london_high:
            self.london_high = high
        if low < self.london_low:
            self.london_low = low
        if high > self.asia_high:
            self.asia_high = high
        if low < self.asia_low:
            self.asia_low = low

    def track_ny(self, high: float, low: float) -> None:
        """New York seansı (13:00-22:00 UTC) high/low izle.
        London range'ini genişletebilir.
        """
        if self.london_high == 0:
            self.london_high = high
        elif high > self.london_high:
            self.london_high = high
        if self.london_low == float("inf"):
            self.london_low = low
        elif low < self.london_low:
            self.london_low = low

    def reset(self) -> None:
        """Yeni CBDR döngüsünde range'leri sıfırla."""
        self.asia_high = 0.0
        self.asia_low = float("inf")
        self.london_high = 0.0
        self.london_low = float("inf")
        self.range_type = "CBDR"
        self.asia_checked = False


class TradeDayState:
    """Günlük trade sayısı.

    Tek sorumluluk: trade counting.
    """

    __slots__ = ("trades_today",)

    def __init__(self):
        self.trades_today: int = 0

    def increment_trade(self) -> None:
        """Trade sayısını 1 artır."""
        self.trades_today += 1

    def reset_for_new_cycle(self) -> None:
        """Yeni CBDR döngüsünde trade state'ini sıfırla.

        trades_today burada sıfırlanmalı: CBDR döngüsü 22:00'de başlar,
        gece yarısı değil.
        """
        self.trades_today = 0


# ═══════════════════════════════════════════════════════════════
# SessionState — geriye dönük uyumlu adapter (Faz 4.2)
# ═══════════════════════════════════════════════════════════════


class SessionState:
    """CBDR + Session + Trade state'lerini tek çatıda toplayan adapter.

    Faz 4.2: İçeride CBDRState, RangeTracker, TradeDayState kullanır.
    Tüm eski attribute'ları ve metot imzalarını korur —
    bot.py, signal_engine.py'de değişiklik gerekmez.
    """

    def __init__(self, start_hour: int = 22, end_hour: int = 2):
        self._cbdr = CBDRState()
        self._range = RangeTracker()
        self._trade = TradeDayState()
        self.fvg_ready: bool = False
        self.cbdr_start: int = start_hour
        self.cbdr_end: int = end_hour

    # ── CBDRState delegation (8 attribute) ──────────────────

    @property
    def cbdr_body_high(self) -> float:
        return self._cbdr.body_high

    @cbdr_body_high.setter
    def cbdr_body_high(self, v: float) -> None:
        self._cbdr.body_high = v

    @property
    def cbdr_body_low(self) -> float:
        return self._cbdr.body_low

    @cbdr_body_low.setter
    def cbdr_body_low(self, v: float) -> None:
        self._cbdr.body_low = v

    @property
    def cbdr_locked(self) -> bool:
        return self._cbdr.locked

    @cbdr_locked.setter
    def cbdr_locked(self, v: bool) -> None:
        self._cbdr.locked = v

    @property
    def cbdr_day(self) -> str:
        return self._cbdr.day

    @cbdr_day.setter
    def cbdr_day(self, v: str) -> None:
        self._cbdr.day = v

    @property
    def daily_bias(self) -> DailyBias:
        return self._cbdr.daily_bias

    @daily_bias.setter
    def daily_bias(self, v: DailyBias) -> None:
        self._cbdr.daily_bias = v

    @property
    def sweep_confirmed(self) -> bool:
        return self._cbdr.sweep_confirmed

    @sweep_confirmed.setter
    def sweep_confirmed(self, v: bool) -> None:
        self._cbdr.sweep_confirmed = v

    @property
    def sweep_direction(self):
        return self._cbdr.sweep_direction

    @sweep_direction.setter
    def sweep_direction(self, v) -> None:
        self._cbdr.sweep_direction = v

    @property
    def sweep_level(self):
        return self._cbdr.sweep_level

    @sweep_level.setter
    def sweep_level(self, v) -> None:
        self._cbdr.sweep_level = v

    # ── RangeTracker delegation (6 attribute) ───────────────

    @property
    def asia_high(self) -> float:
        return self._range.asia_high

    @asia_high.setter
    def asia_high(self, v: float) -> None:
        self._range.asia_high = v

    @property
    def asia_low(self) -> float:
        return self._range.asia_low

    @asia_low.setter
    def asia_low(self, v: float) -> None:
        self._range.asia_low = v

    @property
    def london_high(self) -> float:
        return self._range.london_high

    @london_high.setter
    def london_high(self, v: float) -> None:
        self._range.london_high = v

    @property
    def london_low(self) -> float:
        return self._range.london_low

    @london_low.setter
    def london_low(self, v: float) -> None:
        self._range.london_low = v

    @property
    def range_type(self) -> str:
        return self._range.range_type

    @range_type.setter
    def range_type(self, v: str) -> None:
        self._range.range_type = v

    @property
    def asia_checked(self) -> bool:
        return self._range.asia_checked

    @asia_checked.setter
    def asia_checked(self, v: bool) -> None:
        self._range.asia_checked = v

    # ── TradeDayState delegation (1 attribute) ──────────────

    @property
    def trades_today(self) -> int:
        return self._trade.trades_today

    @trades_today.setter
    def trades_today(self, v: int) -> None:
        self._trade.trades_today = v

    # ── Public methods (orijinal API birebir korunur) ───────

    def update(
        self,
        dt: datetime,
        open: float,
        high: float,
        low: float,
        close: float,
        atr: float = 0.0,
    ) -> None:
        """Tüm alt state'leri güncelle. cbdr_start/cbdr_end ile dinamik pencere."""
        sess = detect_phase(dt, {"start": self.cbdr_start, "end": self.cbdr_end})
        h = dt.hour
        today = dt.strftime("%Y-%m-%d")
        cbdr = self._cbdr
        rng = self._range
        sh = self.cbdr_start
        eh = self.cbdr_end
        spans_midnight = sh > eh

        # CBDR day_key: spans_midnight ise dünün bugününe düşen saatler
        if spans_midnight:
            cbdr_key = (
                today if h >= sh else (dt - timedelta(days=1)).strftime("%Y-%m-%d")
            )
        else:
            cbdr_key = (
                today if h >= sh else (dt - timedelta(days=1)).strftime("%Y-%m-%d")
            )
        if cbdr_key != cbdr.day:
            self._reset_for_new_cbdr_cycle()
            cbdr.day = cbdr_key

        # CBDR body tracking: pencere ici saatlerde
        in_window = (h >= sh or h < eh) if spans_midnight else (sh <= h < eh)
        if in_window and not cbdr.locked:
            cbdr.track_body(open, close)

        # CBDR lock: pencere disina cikinca
        out_of_window = (eh <= h < sh) if spans_midnight else (h >= eh or h < sh)
        if out_of_window and not cbdr.locked and cbdr.body_high > 0:
            cbdr.lock()

        if cbdr.locked and not cbdr.sweep_confirmed:
            cbdr.check_sweep(high, low, close, atr)

        if 2 <= h < 8:
            rng.track_asia(high, low)

        if sess == SessionPhase.LONDON:
            rng.track_london(high, low)
        elif sess == SessionPhase.NEWYORK:
            rng.track_ny(high, low)

    def _reset_for_new_cbdr_cycle(self) -> None:
        """Yeni CBDR döngüsü başlangıcında tüm alt state'leri sıfırla."""
        self._cbdr.reset_for_new_cycle()
        self._range.reset()
        self._trade.reset_for_new_cycle()

    def _track_cbdr_body(
        self, open: float, high: float, low: float, close: float
    ) -> None:
        """Eski API uyumluluğu için — CBDRState'e delegate."""
        self._cbdr.track_body(open, close)

    def _track_asia(self, high: float, low: float) -> None:
        """Eski API uyumluluğu için — RangeTracker'a delegate."""
        self._range.track_asia(high, low)

    def _track_london(self, high: float, low: float) -> None:
        """Eski API uyumluluğu için — RangeTracker'a delegate."""
        self._range.track_london(high, low)

    def _track_ny(self, high: float, low: float) -> None:
        """Eski API uyumluluğu için — RangeTracker'a delegate."""
        self._range.track_ny(high, low)

    def _check_cbdr_sweep(
        self, high: float, low: float, close: float, atr: float = 0.0
    ) -> None:
        """Eski API uyumluluğu için — CBDRState'e delegate."""
        self._cbdr.check_sweep(high, low, close, atr)


# ═══════════════════════════════════════════════════════════════
# Helper functions (değişmedi)
# ═══════════════════════════════════════════════════════════════


def detect_phase(dt: datetime, session_hours: dict | None = None) -> SessionPhase:
    """
    Saat bazinda piyasa seansi tespiti.
    session_hours: {'start': int, 'end': int} — CBDR penceresi.
                   None = default (22-2, mevcut hardcoded davranis).
    """
    if isinstance(dt, int):
        return SessionPhase.CLOSED
    if session_hours is None:
        session_hours = {"start": 22, "end": 2}
    h = dt.hour
    sh = session_hours["start"]
    eh = session_hours["end"]
    spans = sh > eh
    # CBDR window kontrolu
    in_cbdr = (h >= sh or h < eh) if spans else (sh <= h < eh)
    if in_cbdr:
        return SessionPhase.CBDR
    # Aktif seanslar (hardcoded, piyasa saatleri degismez)
    if 2 <= h < 13:
        return SessionPhase.LONDON
    elif 13 <= h < 22:
        return SessionPhase.NEWYORK
    return SessionPhase.CLOSED


def detect_phase_from_timestamp(
    ts_ms: int, session_hours: dict | None = None
) -> SessionPhase:
    if ts_ms <= 0:
        return SessionPhase.CLOSED
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        return detect_phase(dt, session_hours)
    except Exception:
        return SessionPhase.CLOSED
