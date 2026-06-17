"""
models.py
─────────
Nexus SMC Trading Bot — Temel veri yapıları (Foundation Layer).

Bar, FVG, CHoCH, SwingPoint, FVGQuality, AnalysisResult dataclass'ları.

Bağımlılık: YOK (bu modül hiçbir iç modülü import etmez).
Tüm diğer modüller bu modüle bağımlıdır → tek yönlü dependency graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final, Literal

logger = logging.getLogger("nexus.models")

# ─────────────────────────────────────────────────────────
# Timeframe-Adaptive Parametreler
# ─────────────────────────────────────────────────────────
# Timeframe → (BREAK_WINDOW, BODY_LOOKBACK, SFP_FOLLOWTHROUGH_BARS)
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
    """
    Timeframe'e göre adaptif parametreleri döner.

    Returns:
        tuple[int, int, int]: (break_window, body_lookback, sfp_followthrough_bars)
    """
    return _TF_PARAMS.get(timeframe.lower(), _TF_DEFAULT)


# ─────────────────────────────────────────────────────────
# CHoCH Konfigürasyonu (Global Sabitler)
# ─────────────────────────────────────────────────────────
CHoCH_SFP_FOLLOWTHROUGH: Final[int] = 2  # kaç bar boyunca level ötesinde kapanış gerekir

# ─────────────────────────────────────────────────────────
# FVG Sabitleri
# ─────────────────────────────────────────────────────────
DEFAULT_LOOKBACK: Final[int] = 100
MAX_FVG_AGE_BARS: Final[int] = 500
MIN_FVG_SIZE: Final[float] = 0.0
ATR_PERIOD: Final[int] = 14

# ─────────────────────────────────────────────────────────
# Dataclass'lar — Tip-Güvenli, Immutable-by-Convention
# ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Bar:
    """
    OHLCV mumu. `index` mutlak (absolute) bar pozisyonudur.

    Not: `frozen=True` → runtime'da accidental modification önler.
    WebSocket'ten gelen bar'lar için yeni instance oluşturulur.
    """

    index: int  # rolling buffer'daki mutlak konum — hiç değişmez
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True  # WebSocket: False ise mum henüz kapanmadı
    timestamp: int = 0  # Orijinal OHLCV timestamp (ms), export/log için

    @property
    def body(self) -> float:
        """Mum gövde büyüklüğü (mutlak değer)."""
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        """Üst fitil uzunluğu."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        """Alt fitil uzunluğu."""
        return min(self.open, self.close) - self.low

    @property
    def range(self) -> float:
        """Toplam fiyat aralığı (high - low)."""
        return self.high - self.low

    def __post_init__(self) -> None:
        """Validasyon: high >= low, open/close within [low, high]."""
        if self.high < self.low:
            raise ValueError(f"Bar[{self.index}]: high ({self.high}) < low ({self.low})")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"Bar[{self.index}]: open ({self.open}) out of [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"Bar[{self.index}]: close ({self.close}) out of [low, high]")


@dataclass(frozen=True)
class FVG:
    """
    Fair Value Gap (FVG) — 3-mum imbalance yapısı.

    `real_index`: FVG'nin oluştuğu "mother bar"ın mutlak bar indeksi.
    Dilim kaynaklı göreceli indeks YOKTUR → rolling buffer-safe.

    Invariant:
        - bullish: bottom < top (gap yukarı yönlü)
        - bearish: bottom < top (gap aşağı yönlü, top > bottom mantıksal)
    """

    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    real_index: int  # mutlak bar indeksi (Bar.index)
    timeframe: str = "5m"
    filled: bool = False
    invalidated: bool = False
    _next_check_abs: int = field(default=-1, repr=False, init=False)

    def __post_init__(self) -> None:
        """Validasyon + default initialization."""
        # FVG invariant: top > bottom (her iki yönde de mantıksal)
        if self.top <= self.bottom:
            raise ValueError(f"FVG[{self.real_index}]: top ({self.top}) <= bottom ({self.bottom})")
        # İlk tarama mother bar'dan 2 sonrasından başlar (FVG tanımı gereği).
        if self._next_check_abs < 0:
            object.__setattr__(self, "_next_check_abs", self.real_index + 2)

    @property
    def size(self) -> float:
        """FVG boyutu (fiyat aralığı)."""
        return self.top - self.bottom

    @property
    def midpoint(self) -> float:
        """FVG orta noktası (Consequent Encroachment seviyesi)."""
        return (self.top + self.bottom) / 2.0

    @property
    def is_active(self) -> bool:
        """Retest ve sinyal üretimi için kullanılabilir mi?"""
        return not self.invalidated and not self.filled

    def mark_filled(self, price: float) -> bool:
        """
        FVG'nin doldurulup doldurulmadığını kontrol eder ve günceller.

        Args:
            price: Kontrol edilecek fiyat seviyesi.

        Returns:
            bool: FVG bu fiyatla doldu mu?
        """
        if self.direction == "bullish":
            if price <= self.bottom:
                object.__setattr__(self, "filled", True)
                return True
        else:  # bearish
            if price >= self.top:
                object.__setattr__(self, "filled", True)
                return True
        return False


@dataclass(frozen=True)
class CHoCH:
    """
    Change of Character (CHoCH) — Yapısal trend kırılımı sinyali.

    İnvariantlar:
        - bar_index >= pivot_bar_index (kırılım, pivot'tan sonra gelir)
        - level: kırılan pivot'un mutlak fiyat seviyesi (close değil)
        - timestamp: kırılımı yapan mumun ms zaman damgası
    """

    direction: Literal["bullish", "bearish"]
    level: float  # Kırılan pivot'un mutlak fiyat seviyesi
    bar_index: int  # break_bar.index — kırılmanın gerçekleştiği bar
    pivot_bar_index: int  # Kırılan pivot'un oluştuğu bar (referans)
    timeframe: str = "5m"
    strength: float = 0.0  # CHoCH kalite gücü [0.0, 1.0] — penetration + SFP follow-through
    timestamp: int = 0  # Zaman senkronizasyonu için — SADECE CHoCH'ta var

    def __post_init__(self) -> None:
        """Validasyon: bar_index >= pivot_bar_index."""
        if self.bar_index < self.pivot_bar_index:
            raise ValueError(f"CHoCH[{self.bar_index}]: bar_index < pivot_bar_index " f"({self.pivot_bar_index})")

    def age_bars(self, current_index: int) -> int:
        """Mevcut bar indeksine göre CHoCH yaşını (bar cinsinden) döner."""
        return max(0, current_index - self.bar_index)


@dataclass(frozen=True)
class SwingPoint:
    """
    Tek bir swing high veya swing low pivot noktası.

    Kullanım: pivot.py modülü tarafından üretilir,
    choch.py ve fvg.py tarafından tüketilir.
    """

    kind: Literal["high", "low"]
    price: float
    bar_index: int  # Bar.index (mutlak)
    mitigated: bool = False  # Fiyat bu seviyeyi geçti mi?

    def mark_mitigated(self, price: float) -> bool:
        """
        Swing point'in fiyat tarafından aşıldığını işaretler.

        Args:
            price: Kontrol edilecek fiyat.

        Returns:
            bool: Mitigation gerçekleşti mi?
        """
        if self.kind == "high" and price > self.price:
            object.__setattr__(self, "mitigated", True)
            return True
        if self.kind == "low" and price < self.price:
            object.__setattr__(self, "mitigated", True)
            return True
        return False


@dataclass(frozen=True)
class FVGQuality:
    """
    FVG Kalite Skoru — bileşen skorları ve nihai ağırlıklandırılmış skor.

    Bileşenler:
        - displacement: mother bar momentumu (ATR-normalized)
        - fvg_size: FVG boyutu (ATR-relative)
        - sweep: likidite avı (ict sweep) varlığı
        - retest: FVG'ye temas zamanlaması
        - score: nihai ağırlıklandırılmış skor [0.0, 1.0]

    Not: scoring.py'deki compute_fvg_quality() tarafından üretilir.
    """

    displacement: float
    fvg_size: float
    sweep: float
    retest: float
    score: float

    def __post_init__(self) -> None:
        """Validasyon: tüm skorlar [0.0, 1.0] aralığında."""
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
        """Skor eşiği geçildi mi? (scoring.py'de threshold ile karşılaştırılır)."""
        return self.score > 0.0


# ─────────────────────────────────────────────────────────
# AnalysisResult — Analyzer.py Çıktısı (Opsiyonel, Burada Tanımlı)
# ─────────────────────────────────────────────────────────
@dataclass
class AnalysisResult:
    """
    Tek sembol için tam analiz sonucu.

    Bu dataclass analyzer.py tarafından doldurulur,
    scoring.py ve bot_state tarafından tüketilir.

    Alan önceliği: direction → choch → fvg → fvg_quality → entry/exit
    """

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
    vp_levels: object | None = None  # VPLevels (volume_profile.py) — TYPE_CHECKING ile import
    entry_zone: float | None = None
    entry_zone_type: Literal["proximal", "ce"] | None = None
    armed: bool = False
    stop_loss: float | None = None
    tp_level: float | None = None

    @property
    def expected_choch_direction(self) -> Literal["bullish", "bearish"] | None:
        """Merkezi yön dönüşümü — DRY prensibi."""
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
        """
        Sinyal validasyon zinciri (7 adımlı veto).
        Her adım başarısız olursa False döner.

        Args:
            threshold: Manuel skor eşiği (None → ADX moduna göre otomatik).
            adx: Manuel ADX değeri (None → self.adx_value kullanılır).

        Returns:
            bool: Sinyal işleme alınmalı mı?
        """
        # Placeholder implementation — analyzer.py'de tam implementasyon var.
        # Bu metot burada tanımlıdır ki scoring.py test'lerinde mock'lanabilsin.
        return self.direction is not None and self.fvg_quality is not None

    def summary(self) -> str:
        """Tek satır debug özeti — logging ve monitoring için."""
        choch_str = f"choch={self.choch.direction}@{self.choch.level:.2f}" if self.choch else "choch=None"
        fvg_str = f"fvg=[{self.fvg.bottom:.2f}-{self.fvg.top:.2f}]" if self.fvg else "fvg=None"
        score_str = f"score={self.fvg_quality.score:.3f}" if self.fvg_quality else "quality=None"
        return (
            f"{self.symbol} | {self.direction} | {choch_str} | {fvg_str} | "
            f"{score_str} | adx={self.adx_value:.1f} | armed={self.armed}"
        )


# MIGRATION: Yerel AnalysisResult sınıf tanımı korunur (backward compat),
# ancak modül namespace'inden kaldırılır. __getattr__ lazy import ile
# analyzer.py'nin AnalysisResult'ını sunar — circular import önlenir.
del AnalysisResult


def __getattr__(name: str):
    if name == "AnalysisResult":
        from analyzer import AnalysisResult

        return AnalysisResult
    raise AttributeError(f"module 'models' has no attribute {name!r}")
