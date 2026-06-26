"""
session.py — CBDR + Session State Machine
AM/PM ayrımı kaldırıldı. Tek NEWYORK seansı: 13:00-22:00 UTC.

Faz 4.2: SessionState → 3 sınıfa ayrıldı:
  - CBDRState: body tracking + bias + sweep (8 alan)
  - RangeTracker: asia/london/NY range + range_type (6 alan)
  - TradeDayState: trade counting + retrade armed state (9 alan)

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
                self.sweep_direction = "bullish"
                self.sweep_level = self.body_high
                self.daily_bias = DailyBias.BULLISH
                return

        if low < self.body_low - tolerance:
            if close > self.body_low:
                self.sweep_confirmed = True
                self.sweep_direction = "bearish"
                self.sweep_level = self.body_low
                self.daily_bias = DailyBias.BEARISH
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

    def evaluate_range_type(self, cbdr: CBDRState) -> None:
        """08:00 UTC'de CBDR ve Asia range'lerine bakarak range_type belirle.

        CBDR dar (< CBDR_DEAD_THRESHOLD_PCT) ve Asia da dar ise → DEAD.
        CBDR dar ama Asia yeterli genişlikte → ASIA range kullan.
        Aksi halde range_type "CBDR" kalır.
        """
        cbdr_range_pct = (
            ((cbdr.body_high - cbdr.body_low) / cbdr.body_low * 100)
            if cbdr.body_low > 0
            else 0
        )
        if cbdr_range_pct < cfg.CBDR_DEAD_THRESHOLD_PCT:
            if self.asia_high > 0:
                asia_range_pct = (
                    ((self.asia_high - self.asia_low) / self.asia_low * 100)
                    if self.asia_low > 0
                    else 0
                )
                if asia_range_pct < cfg.ASIA_DEAD_THRESHOLD_PCT:
                    self.range_type = "DEAD"
                    cbdr.locked = False  # DEAD: CBDR kilidi geri al
                else:
                    cbdr.body_high = self.asia_high
                    cbdr.body_low = self.asia_low
                    self.range_type = "ASIA"
        # dar değilse range_type zaten "CBDR", dokunma

    def reset(self) -> None:
        """Yeni CBDR döngüsünde range'leri sıfırla."""
        self.asia_high = 0.0
        self.asia_low = float("inf")
        self.london_high = 0.0
        self.london_low = float("inf")
        self.range_type = "CBDR"
        self.asia_checked = False


class TradeDayState:
    """Günlük trade sayısı + retrade armed state.

    9 alan — tek sorumluluk: trade counting ve retrade yönetimi.
    """

    __slots__ = (
        "trades_today",
        "retrade_armed",
        "retrade_side",
        "retrade_sweep_level",
        "retrade_entry_bar",
        "retrade_fvg_attempts",
        "retrade_mode",
        "pending_retrade_arm",
    )

    def __init__(self):
        self.trades_today: int = 0
        # Retrade state — pivot bazlı LBS/SBS sweep sonrası 2. entry için.
        self.retrade_armed: bool = False
        self.retrade_side: Literal["long", "short"] | None = None
        self.retrade_sweep_level: float = 0.0
        self.retrade_entry_bar: int = 0
        self.retrade_fvg_attempts: int = 0
        self.retrade_mode: str = "fvg"
        # FIX #2: WS confirm bekleyen retrade arm (LIVE mod)
        self.pending_retrade_arm: bool = False

    def increment_trade(self) -> None:
        """Trade sayısını 1 artır."""
        self.trades_today += 1

    def arm_retrade(self, side: str, entry_bar: int) -> None:
        """Retrade kolunu aktifleştir."""
        self.retrade_armed = True
        self.retrade_side = side
        self.retrade_entry_bar = entry_bar
        self.retrade_fvg_attempts = 0
        self.retrade_mode = "fvg"
        self.retrade_sweep_level = 0.0

    def disarm_retrade(self) -> None:
        """Retrade kolunu devre dışı bırak."""
        self.retrade_armed = False
        self.pending_retrade_arm = False
        self.retrade_side = None
        self.retrade_sweep_level = 0.0
        self.retrade_entry_bar = 0
        self.retrade_fvg_attempts = 0
        self.retrade_mode = "fvg"

    def reset_for_new_cycle(self) -> None:
        """Yeni CBDR döngüsünde trade/retrade state'ini sıfırla.

        trades_today burada sıfırlanmalı: CBDR döngüsü 22:00'de başlar,
        gece yarısı değil. last_date/today bloğu 22:00-00:00 arasında
        eski günün sayısını taşıyarak retrade'i engelliyordu.
        """
        self.trades_today = 0
        self.disarm_retrade()


# ═══════════════════════════════════════════════════════════════
# SessionState — geriye dönük uyumlu adapter (Faz 4.2)
# ═══════════════════════════════════════════════════════════════


class SessionState:
    """CBDR + Session + Trade state'lerini tek çatıda toplayan adapter.

    Faz 4.2: İçeride CBDRState, RangeTracker, TradeDayState kullanır.
    Tüm eski attribute'ları ve metot imzalarını korur —
    bot.py, signal_engine.py, retrade_engine.py'de değişiklik gerekmez.
    """

    def __init__(self):
        self._cbdr = CBDRState()
        self._range = RangeTracker()
        self._trade = TradeDayState()

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

    # ── TradeDayState delegation (9 attribute) ──────────────

    @property
    def trades_today(self) -> int:
        return self._trade.trades_today

    @trades_today.setter
    def trades_today(self, v: int) -> None:
        self._trade.trades_today = v

    @property
    def retrade_armed(self) -> bool:
        return self._trade.retrade_armed

    @retrade_armed.setter
    def retrade_armed(self, v: bool) -> None:
        self._trade.retrade_armed = v

    @property
    def retrade_side(self):
        return self._trade.retrade_side

    @retrade_side.setter
    def retrade_side(self, v) -> None:
        self._trade.retrade_side = v

    @property
    def retrade_sweep_level(self) -> float:
        return self._trade.retrade_sweep_level

    @retrade_sweep_level.setter
    def retrade_sweep_level(self, v: float) -> None:
        self._trade.retrade_sweep_level = v

    @property
    def retrade_entry_bar(self) -> int:
        return self._trade.retrade_entry_bar

    @retrade_entry_bar.setter
    def retrade_entry_bar(self, v: int) -> None:
        self._trade.retrade_entry_bar = v

    @property
    def retrade_fvg_attempts(self) -> int:
        return self._trade.retrade_fvg_attempts

    @retrade_fvg_attempts.setter
    def retrade_fvg_attempts(self, v: int) -> None:
        self._trade.retrade_fvg_attempts = v

    @property
    def retrade_mode(self) -> str:
        return self._trade.retrade_mode

    @retrade_mode.setter
    def retrade_mode(self, v: str) -> None:
        self._trade.retrade_mode = v

    @property
    def pending_retrade_arm(self) -> bool:
        return self._trade.pending_retrade_arm

    @pending_retrade_arm.setter
    def pending_retrade_arm(self, v: bool) -> None:
        self._trade.pending_retrade_arm = v

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
        """Tüm alt state'leri güncelle. Orijinal mantık birebir aynı."""
        sess = detect_phase(dt)
        h = dt.hour
        today = dt.strftime("%Y-%m-%d")
        cbdr = self._cbdr
        rng = self._range

        cbdr_key = today if h >= 22 else (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        if cbdr_key != cbdr.day:
            self._reset_for_new_cbdr_cycle()
            cbdr.day = cbdr_key

        if sess == SessionPhase.CBDR and not cbdr.locked:
            cbdr.track_body(open, close)

        if 2 <= h < 22 and not cbdr.locked and cbdr.body_high > 0:
            cbdr.lock()

        if cbdr.locked and not cbdr.sweep_confirmed:
            cbdr.check_sweep(high, low, close, atr)

        if h >= 8 and not rng.asia_checked and cbdr.locked:
            rng.asia_checked = True
            rng.evaluate_range_type(cbdr)

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
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        return detect_phase(dt)
    except Exception:
        return SessionPhase.CLOSED
