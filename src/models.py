"""
models.py — Sniper Backtest Foundation Layer
Bar, FVG, CHoCH, SwingPoint, FVGQuality, AnalysisResult dataclass'lari.
Bagimlilik: YOK
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final, Generic, Literal, TypeVar

logger = logging.getLogger("sniper.models")

# ── Generic Result type (P8.1) ───────────────────────────────────

T = TypeVar("T")


@dataclass(frozen=True)
class Result(Generic[T]):
    """Rust tarzi Result[T]: her operasyon ya basarili ya da hatasiyla doner.

    Kullanim:
        def islem() -> Result[int]:
            try:
                return Result.ok(42)
            except Exception as e:
                return Result.fail(str(e))

        r = islem()
        if r.is_ok:
            print(r.value)
        else:
            log.error(r.error)
    """

    success: bool
    value: T | None = None
    error: str = ""

    @staticmethod
    def ok(value: T) -> "Result[T]":
        return Result(success=True, value=value)

    @staticmethod
    def fail(error: str) -> "Result[T]":
        return Result(success=False, error=error)

    @property
    def is_ok(self) -> bool:
        return self.success

    @property
    def is_err(self) -> bool:
        return not self.success


# ── WS-FALLBACK exception (P8.5) ──────────────────────────────────


class WSFallbackError(Exception):
    """Binance reduceOnly FILLED geldi ama ID eslesmedi.

    Bu exception kritik bir durumu belirtir: Binance pozisyonu kapatti
    ama bot'un takip ettigi SL/TP ID'leri ile eslesmedi. Trade yine de
    kapatilir, ama bu exception yukari firlatilarak durumun sessiz
    kalmamasi saglanir.
    """

    def __init__(self, symbol: str, oid: str, expected_sl: str, expected_tp: str):
        self.symbol = symbol
        self.oid = oid
        self.expected_sl = expected_sl
        self.expected_tp = expected_tp
        super().__init__(
            f"[WS-FALLBACK] {symbol} reduceOnly FILLED geldi ama ID eslesmedi "
            f"(oid={oid}, beklenen_sl={expected_sl}, beklenen_tp={expected_tp})"
        )


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
            raise ValueError(
                f"Bar[{self.index}]: high ({self.high}) < low ({self.low})"
            )
        if not (self.low <= self.open <= self.high):
            raise ValueError(
                f"Bar[{self.index}]: open ({self.open}) out of [low, high]"
            )
        if not (self.low <= self.close <= self.high):
            raise ValueError(
                f"Bar[{self.index}]: close ({self.close}) out of [low, high]"
            )


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
            raise ValueError(
                f"FVG[{self.real_index}]: top ({self.top}) <= bottom ({self.bottom})"
            )
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
            raise ValueError(
                f"CHoCH[{self.bar_index}]: bar_index < pivot_bar_index "
                f"({self.pivot_bar_index})"
            )

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
    ) -> bool:
        return self.direction is not None and self.fvg_quality is not None

    def summary(self) -> str:
        choch_str = (
            f"choch={self.choch.direction}@{self.choch.level:.2f}"
            if self.choch
            else "choch=None"
        )
        fvg_str = (
            f"fvg=[{self.fvg.bottom:.2f}-{self.fvg.top:.2f}]"
            if self.fvg
            else "fvg=None"
        )
        score_str = (
            f"score={self.fvg_quality.score:.3f}"
            if self.fvg_quality
            else "quality=None"
        )
        return (
            f"{self.symbol} | {self.direction} | {choch_str} | {fvg_str} | "
            f"{score_str} | adx={self.adx_value:.1f} | armed={self.armed}"
        )


# ── ActiveTrade dataclass (Faz 1.1) ──────────────────────────────


@dataclass
class ActiveTrade:
    """Canlı trade durumu. Hem attribute hem dict erişimini destekler.

    Geriye dönük uyumlu: trade["side"] ve trade.side aynı değeri verir.
    """

    symbol: str = ""
    side: Literal["long", "short"] = "long"
    entry_price: float = 0.0
    entry_bar_index: int = 0
    sl: float = 0.0
    tp: float = 0.0
    qty: float = 0.0
    initial_sl: float = 0.0
    initial_tp: float = 0.0
    risk_pts: float = 0.0
    trailing_count: int = 0
    trail_steps: list = field(default_factory=list)
    is_recovered: bool = False
    hybrid_mode: str | None = None
    sl_order_id: str = ""
    tp_order_id: str = ""
    # Runtime-only (exit sırasında doldurulur):
    exit_price: float | None = None
    exit_bar: int | None = None
    exit_timestamp: int = 0
    result: str | None = None
    trigger_fvg: object | None = None
    fvg_top: float | None = None
    fvg_bottom: float | None = None
    fvg_direction: str | None = None
    fvg_bar_index: int = -1
    upnl: float | None = None
    status: str = ""

    # ── Dict uyumluluğu ───────────────────────────────────────

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __setitem__(self, key: str, value) -> None:
        setattr(self, key, value)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def keys(self):
        return self.__dataclass_fields__.keys()

    def __iter__(self):
        return iter(self.__dataclass_fields__)


# ── PendingLock context manager (P8.4) ────────────────────────────


class PendingLock:
    """PENDING kilidi için context manager.

    _try_entry() akışında API çağrısı öncesi PENDING statüsünde
    placeholder trade oluşturur. Erken dönüş (hata, skip) durumunda
    __exit__ garantisi ile temizler. Başarılı akışta commit() çağrısı
    yapılır ve PENDING korunur — sonra gerçek trade ile ezilir.

    Kullanim:
        with PendingLock(self.active_trades, sym, logger=log) as lock:
            ... API cagrisi ...
            if hata:
                return  # PENDING otomatik temizlenir
            lock.commit()  # basarili — PENDING korunur

        # context manager disinda:
        self.active_trades[sym] = ActiveTrade(...)  # PENDING ezilir
    """

    def __init__(self, active_trades: dict, sym: str, logger=None):
        self._active_trades = active_trades
        self._sym = sym
        self._log = logger
        self._committed = False

    def __enter__(self):
        self._active_trades[self._sym] = ActiveTrade(status="PENDING")
        self._committed = False
        return self

    def commit(self):
        """Context manager'a basarili tamamlandigini bildir.

        Cagrildiginda __exit__ PENDING state'i silmez.
        """
        self._committed = True

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        if self._committed:
            return False  # normal cikis, PENDING korunur
        trade = self._active_trades.get(self._sym)
        if trade is not None and getattr(trade, "status", "") == "PENDING":
            del self._active_trades[self._sym]
            if self._log:
                self._log.debug(
                    "[CLEANUP] %s PENDING state temizlendi (context manager)", self._sym
                )
        return False  # istisnalari bastirma
