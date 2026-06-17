"""
Volume Profile Module
=====================

Son N bar'dan Volume Profile (VP) hesaplar. Session bazlı (00:00 UTC sıfırlanır).

Kullanım Amacı
--------------
- Score adjuster — entry filter veya signal blocker değil.
- HVN/LVN yakınlığına göre FVG kalite skorunu düşürür/yükseltir.
- POC'u TP mıknatısı olarak kullanır.

Skor Kuralları
--------------
- HVN yakını (entry)       → score -0.10  (chop / kabul bölgesi, riskli)
- LVN yakını (entry)       → score +0.05  (boşluk / akış bölgesi, iyi)
- HVN yakını (mother bar)  → score -0.15  (sahte FVG riski)
- LVN yakını (mother bar)  → score +0.10  (güvenilir FVG)
- POC                      → TP mıknatısı (aktif kullanım)

Zaman Dilimi Bağımsızlığı
--------------------------
- required_bars parametresi sayesinde H1 (24 bar = 24 saat) veya
  15M (24 bar = 6 saat) gibi farklı timeframelerde çalışır.
- Session bazlı cache (00:00 UTC sıfırlanır) her sembol+timeframe
  kombinasyonu için ayrı tutulur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger("nexus.volume_profile")


# ─────────────────────────────────────────────────────────
# Veri Yapıları
# ─────────────────────────────────────────────────────────


@dataclass
class VPLevels:
    """Volume Profile seviyelerini tutar.

    Attributes:
        poc: Point of Control — en yüksek hacimli fiyat seviyesi.
        vah: Volume Area High — toplam hacmin %85'ini içeren üst sınır.
        val: Volume Area Low — toplam hacmin %15'ini içeren alt sınır.
        hvn: High Volume Nodes — ortalamanın HVN_THRESHOLD katı üzerindeki bölgeler.
        lvn: Low Volume Nodes — ortalamanın LVN_THRESHOLD katı altındaki bölgeler.
        session_start: Session başlangıç timestamp'i (UTC, saniye).
    """

    poc: float
    vah: float
    val: float
    hvn: list[float] = field(default_factory=list)
    lvn: list[float] = field(default_factory=list)
    session_start: float = 0.0


class ScoreAdjustable(Protocol):
    """Skor ayarlanabilir nesneler için protocol.

    adjust_score / adjust_score_mother_bar metodları bu protocol'ü
    kabul eder. Gerçek tip (ör. FVGQuality) döngüsel import'a yol
    açmamak için protocol olarak tanımlanmıştır.
    """

    score: float


# ─────────────────────────────────────────────────────────
# Volume Profile Hesaplayıcı
# ─────────────────────────────────────────────────────────


class VolumeProfile:
    """Volume Profile hesaplayıcı.

    HVN (High Volume Node) ve LVN (Low Volume Node) tespiti yapar.
    Session bazlı cache ile aynı gün içinde tekrar hesaplamayı önler.

    Args:
        bins: Fiyat aralığının bölüneceği dilim sayısı (varsayılan: 24).
        required_bars: VP hesaplamak için gereken minimum bar sayısı
                       (varsayılan: 24). H1'de 24 saat, 15M'de 6 saat.
    """

    # Sabitler
    HVN_THRESHOLD: float = 1.5
    LVN_THRESHOLD: float = 0.5
    PROXIMITY_PCT: float = 0.002
    _EPS: float = 1e-10  # zero-division koruması

    def __init__(self, bins: int = 24, required_bars: int = 24) -> None:
        """Initializes the VolumeProfile instance.

        Args:
            bins: Fiyat aralığının bölüneceği dilim sayısı.
            required_bars: VP hesaplamak için gereken minimum bar sayısı.
        """
        self.bins = bins
        self.required_bars = required_bars
        self._cache: dict[str, tuple[float, VPLevels]] = {}

    # ── Session Yardımcısı ─────────────────────────────

    @staticmethod
    def _session_start_ts() -> float:
        """Bugünün 00:00 UTC timestamp'ini döndürür.

        Returns:
            Unix timestamp (saniye) — bugünün başlangıcı.
        """
        now = datetime.now(UTC)
        session = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return session.timestamp()

    # ── VP Hesaplama ───────────────────────────────────

    def build(self, bars: list[Any], symbol: str = "") -> VPLevels:
        """Verilen barlardan Volume Profile hesaplar.

        Son required_bars adet bar kullanılır. SMC Uniform Volume
        Distribution: Her mumun hacmi Low-High aralığına eşit dağıtılır.
        Typical Price'ın "hayalet HVN" sorununu çözer — geniş aralıklı
        FVG mother barları otomatik LVN'e kayar, dar konsolidasyon
        mumları otomatik HVN'e kayar.

        Args:
            bars: Bar nesneleri listesi. Her bar .high, .low, .close,
                  .volume attribute'larına sahip olmalıdır.
            symbol: Sembol adı (opsiyonel). Verilirse cache'lenir.

        Returns:
            Hesaplanan VPLevels. Yeterli bar yoksa tüm değerler 0.0
            olan boş VPLevels döner.
        """
        if len(bars) < self.required_bars:
            logger.warning(
                "[VP] Yetersiz bar: %d < %d (symbol=%s)",
                len(bars),
                self.required_bars,
                symbol or "?",
            )
            return VPLevels(0.0, 0.0, 0.0)

        session_ts = self._session_start_ts()

        # Cache: aynı session'da tekrar hesaplama
        if symbol and symbol in self._cache:
            cached_ts, cached_vp = self._cache[symbol]
            if cached_ts == session_ts:
                logger.debug("[VP] Cache hit: %s (session=%.0f)", symbol, session_ts)
                return cached_vp

        data = bars[-self.required_bars :]

        # SMC Uniform Volume Distribution:
        # Her mumun hacmi Low-High aralığına eşit dağıtılır.
        # Typical Price'ın "hayalet HVN" sorununu çözer.
        # Geniş aralıklı FVG mother barları otomatik LVN'e kayar,
        # dar konsolidasyon mumları otomatik HVN'e kayar.
        low_array = np.array([b.low for b in data], dtype=float)
        high_array = np.array([b.high for b in data], dtype=float)
        volumes = np.array([b.volume for b in data], dtype=float)

        price_min = low_array.min()
        price_max = high_array.max()

        if price_max - price_min < self._EPS:
            logger.debug("[VP] Tüm fiyatlar eşit (%.6f), boş VP dönüyor.", price_min)
            return VPLevels(price_min, price_min, price_min)

        edges = np.linspace(price_min, price_max, self.bins + 1)
        profile = np.zeros(self.bins, dtype=float)

        for i in range(len(data)):
            vol = volumes[i]
            low_idx = int(np.digitize(low_array[i], edges)) - 1
            high_idx = int(np.digitize(high_array[i], edges)) - 1

            # Array sınır koruması
            low_idx = max(0, min(low_idx, self.bins - 1))
            high_idx = max(0, min(high_idx, self.bins - 1))

            if high_idx == low_idx:
                # Tek bin'e sıkışan mum (ör. doji)
                profile[low_idx] += vol
            else:
                bins_spanned = (high_idx - low_idx) + 1
                vol_per_bin = vol / bins_spanned
                for j in range(low_idx, high_idx + 1):
                    profile[j] += vol_per_bin

        # POC (Point of Control)
        poc_idx = int(np.argmax(profile))
        poc = float((edges[poc_idx] + edges[poc_idx + 1]) / 2.0)

        # VAH / VAL (Value Area)
        total_vol = profile.sum()
        if total_vol < self._EPS:
            logger.debug("[VP] Toplam hacim sıfır, boş VP dönüyor.")
            return VPLevels(poc, poc, poc)

        cum = np.cumsum(profile)
        upper = int(np.searchsorted(cum, total_vol * 0.85))
        lower = int(np.searchsorted(cum, total_vol * 0.15))
        vah = float(edges[min(upper, self.bins - 1)])
        val = float(edges[min(lower, self.bins - 1)])

        # HVN / LVN
        mean_vol = profile.mean()
        hvn = [
            float((edges[i] + edges[i + 1]) / 2.0) for i, v in enumerate(profile) if v > mean_vol * self.HVN_THRESHOLD
        ]
        lvn = [
            float((edges[i] + edges[i + 1]) / 2.0) for i, v in enumerate(profile) if v < mean_vol * self.LVN_THRESHOLD
        ]

        vp = VPLevels(
            poc=poc,
            vah=vah,
            val=val,
            hvn=hvn,
            lvn=lvn,
            session_start=session_ts,
        )

        if symbol:
            self._cache[symbol] = (session_ts, vp)
            logger.debug("[VP] Cache set: %s (session=%.0f)", symbol, session_ts)

        return vp

    # ── Skor Ayarlayıcılar ────────────────────────────

    def adjust_score(
        self,
        quality: ScoreAdjustable,
        price: float,
        vp: VPLevels,
        symbol: str = "",
    ) -> None:
        """Entry fiyatının HVN/LVN yakınlığına göre skoru ayarlar.

        HVN yakını → score -0.10 (chop bölgesi)
        LVN yakını → score +0.05 (akış bölgesi)

        Args:
            quality: ScoreAdjustable protocol'ünü karşılayan nesne
                     (ör. FVGQuality). .score attribute'u olmalıdır.
            price: Entry fiyatı.
            vp: Hesaplanmış VPLevels.
            symbol: Sembol adı (opsiyonel, log için).
        """
        for level in vp.hvn:
            if abs(price - level) / (abs(price) + self._EPS) < self.PROXIMITY_PCT:
                object.__setattr__(quality, "score", round(quality.score - 0.10, 3))
                logger.info(
                    "[VP] %s entry HVN yakını (%.6f) → score -0.10 → %.3f",
                    symbol or "?",
                    level,
                    quality.score,
                )
                break

        for level in vp.lvn:
            if abs(price - level) / (abs(price) + self._EPS) < self.PROXIMITY_PCT:
                object.__setattr__(quality, "score", round(quality.score + 0.05, 3))
                logger.info(
                    "[VP] %s entry LVN yakını (%.6f) → score +0.05 → %.3f",
                    symbol or "?",
                    level,
                    quality.score,
                )
                break

    def adjust_score_mother_bar(
        self,
        quality: ScoreAdjustable,
        mother_bar_mid: float,
        vp: VPLevels,
        symbol: str = "",
    ) -> None:
        """Mother bar merkezinin HVN/LVN yakınlığına göre FVG güvenilirlik skorunu ayarlar.

        LVN'de mother bar → score +0.10 (güvenilir FVG)
        HVN'de mother bar → score -0.15 (sahte FVG riski)

        Args:
            quality: ScoreAdjustable protocol'ünü karşılayan nesne.
            mother_bar_mid: Mother bar orta noktası (FVG oluşturan bar).
            vp: Hesaplanmış VPLevels (genellikle 15M timeframedan).
            symbol: Sembol adı (opsiyonel, log için).
        """
        for level in vp.lvn:
            if abs(mother_bar_mid - level) / (abs(mother_bar_mid) + self._EPS) < self.PROXIMITY_PCT:
                object.__setattr__(quality, "score", round(quality.score + 0.10, 3))
                logger.info(
                    "[VP] %s mother bar LVN'de (%.6f) → score +0.10 → %.3f",
                    symbol or "?",
                    level,
                    quality.score,
                )
                break

        for level in vp.hvn:
            if abs(mother_bar_mid - level) / (abs(mother_bar_mid) + self._EPS) < self.PROXIMITY_PCT:
                object.__setattr__(quality, "score", round(quality.score - 0.15, 3))
                logger.info(
                    "[VP] %s mother bar HVN'de (%.6f) → score -0.15 → %.3f",
                    symbol or "?",
                    level,
                    quality.score,
                )
                break

    # ── TP Mıknatısı (POC) ────────────────────────────

    def adjust_tp_for_poc(
        self,
        tp_price: float,
        entry_price: float,
        direction: str,
        vp: VPLevels,
        symbol: str = "",
    ) -> float:
        """TP'yi POC'a çeker (TP mıknatısı).

        TP ile entry arasında POC varsa, TP doğrudan POC seviyesine
        çekilir. Bu sayede yüksek hacim bölgesi TP olarak kullanılır.

        Args:
            tp_price: Mevcut TP fiyatı.
            entry_price: Giriş fiyatı.
            direction: İşlem yönü ("LONG" veya "SHORT").
            vp: Hesaplanmış VPLevels (genellikle H1 timeframedan).
            symbol: Sembol adı (opsiyonel, log için).

        Returns:
            Güncellenmiş TP fiyatı. POC mıknatısı yoksa aynen döner.
        """
        poc = vp.poc
        if abs(poc) < self._EPS:
            return tp_price

        direction_upper = direction.upper()

        if direction_upper == "LONG":
            if entry_price < poc < tp_price:
                logger.info(
                    "[VP] %s LONG POC mıknatısı: TP %.6f → %.6f (POC)",
                    symbol or "?",
                    tp_price,
                    poc,
                )
                return poc

        elif direction_upper == "SHORT":
            if tp_price < poc < entry_price:
                logger.info(
                    "[VP] %s SHORT POC mıknatısı: TP %.6f → %.6f (POC)",
                    symbol or "?",
                    tp_price,
                    poc,
                )
                return poc

        else:
            logger.warning(
                "[VP] %s Geçersiz yön: %s (LONG/SHORT bekleniyor)",
                symbol or "?",
                direction,
            )

        return tp_price
