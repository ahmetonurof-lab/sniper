"""
pivot.py
────────
Nexus SMC Trading Bot — Swing High/Low tespiti ve durum yönetimi.

Rollü:
  • Fraktal tabanlı pivot tespiti (left/right onaylı)
  • Kalıcı pivot hafızası (SwingStateManager)
  • Rolling buffer-safe mutlak indeksleme (Bar.index)

Bağımlılık: models.py (Bar, SwingPoint) — TEK YÖNLÜ, döngüsüz.
"""

from __future__ import annotations

import logging
from typing import Final, Literal

from models import Bar, SwingPoint

logger = logging.getLogger("nexus.pivot")

# ─────────────────────────────────────────────────────────
# Sabitler & Konfigürasyon
# ─────────────────────────────────────────────────────────
DEFAULT_LEFT: Final[int] = 3
DEFAULT_RIGHT: Final[int] = 3
MAX_PIVOT_AGE_BARS: Final[int] = 500  # cleanup() için varsayılan yaş limiti


# ─────────────────────────────────────────────────────────
# Pivot Tespit Fonksiyonları — Pure, Stateless
# ─────────────────────────────────────────────────────────
def find_swing_highs(
    bars: list[Bar],
    left: int = DEFAULT_LEFT,
    right: int = DEFAULT_RIGHT,
) -> list[SwingPoint]:
    """
    Fraktal tabanlı Swing High (tepe) noktalarını tespit eder.

    Kriterler:
      • Merkez mumun high'i, sol taraftaki `left` mumun high'inden >= olmalı
      • Merkez mumun high'i, sağ taraftaki `right` mumun high'inden >= olmalı
      • Mum `is_closed=True` olmalı (kapanmamış mum pivot olamaz)
      • Inclusive comparison (<=) → double-top, equal-tick yakalanır

    Args:
        bars: Bar listesi (mutlak indeksli, Bar.index kullanılır)
        left: Sol tarafta kaç mum onay gerekir (varsayılan: 3)
        right: Sağ tarafta kaç mum onay gerekir (varsayılan: 3)

    Returns:
        list[SwingPoint]: Tespit edilen swing high'ler (kronolojik sıralı)

    Complexity: O(n * (left + right)) — küçük left/right için lineer
    """
    if len(bars) < left + right + 1:
        return []

    result: list[SwingPoint] = []

    # Pivot adayı olabilecek aralık: [left, len(bars) - right)
    for i in range(left, len(bars) - right):
        bar = bars[i]

        # Kapanmamış mum pivot olamaz (WebSocket stream'lerde kritik)
        if not bar.is_closed:
            continue

        candidate_high = bar.high

        # Sol onay: soldaki `left` mumun high'i <= candidate
        left_ok = all(bars[i - j].high <= candidate_high for j in range(1, left + 1))
        if not left_ok:
            continue

        # Sağ onay: sağdaki `right` mumun high'i <= candidate
        right_ok = all(bars[i + j].high <= candidate_high for j in range(1, right + 1))
        if not right_ok:
            continue

        # Pivot bulundu → mutlak bar_index ile kaydet
        result.append(SwingPoint(kind="high", price=candidate_high, bar_index=bar.index))

    return result


def find_swing_lows(
    bars: list[Bar],
    left: int = DEFAULT_LEFT,
    right: int = DEFAULT_RIGHT,
) -> list[SwingPoint]:
    """
    Fraktal tabanlı Swing Low (dip) noktalarını tespit eder.

    Kriterler:
      • Merkez mumun low'u, sol taraftaki `left` mumun low'undan <= olmalı
      • Merkez mumun low'u, sağ taraftaki `right` mumun low'undan <= olmalı
      • Mum `is_closed=True` olmalı
      • Inclusive comparison (>=) → double-bottom, equal-tick yakalanır

    Args:
        bars: Bar listesi (mutlak indeksli)
        left: Sol onay mum sayısı
        right: Sağ onay mum sayısı

    Returns:
        list[SwingPoint]: Tespit edilen swing low'lar (kronolojik sıralı)
    """
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


# ─────────────────────────────────────────────────────────
# SwingStateManager — Kalıcı Pivot Hafızası (Stateful)
# ─────────────────────────────────────────────────────────
class SwingStateManager:
    """
    Pivot'ları kalıcı hafızada tutan state manager.

    Neden gerekli?
      • Rolling buffer'da eski bar'lar silindiğinde pivot'lar "unutulmamalı"
      • CHoCH tespiti için geçmiş pivot'ların mitigasyon durumu takip edilmeli
      • FVG sweep detection için önceki swing high/low'lara erişim gerekli

    Invariant'lar:
      • _highs ve _lows listeleri bar_index'e göre kronolojik sıralı
      • Bir pivot sadece `mark_mitigated()` ile pasifleşir, otomatik silinmez
      • cleanup() ile çok eski pivot'lar bellek yönetimi için temizlenebilir
    """

    def __init__(self) -> None:
        self._highs: list[SwingPoint] = []
        self._lows: list[SwingPoint] = []

    def ingest(self, bars: list[Bar], left: int = DEFAULT_LEFT, right: int = DEFAULT_RIGHT) -> None:
        """
        Yeni bar listesinden pivot'ları tespit eder ve hafızaya ekler.

        Davranış:
          • Mevcut pivot'lar korunur (bar_index çakışması önlenir)
          • Yeni tespit edilen pivot'lar kronolojik olarak eklenir
          • Listeler bar_index'e göre sıralanır (binary search için optimize)

        Args:
            bars: İşlenecek bar listesi
            left: find_swing_* fonksiyonlarına geçirilecek sol onay sayısı
            right: find_swing_* fonksiyonlarına geçirilecek sağ onay sayısı
        """
        # Mevcut pivot indekslerini set'e al → O(1) lookup
        existing_high_idx = {p.bar_index for p in self._highs}
        existing_low_idx = {p.bar_index for p in self._lows}

        # Yeni swing high'leri ekle (çakışma kontrolü ile)
        for sp in find_swing_highs(bars, left, right):
            if sp.bar_index not in existing_high_idx:
                self._highs.append(sp)
                existing_high_idx.add(sp.bar_index)  # aynı bar'dan duplicate önle

        # Yeni swing low'ları ekle
        for sp in find_swing_lows(bars, left, right):
            if sp.bar_index not in existing_low_idx:
                self._lows.append(sp)
                existing_low_idx.add(sp.bar_index)

        # Kronolojik sıra koru (binary search / merge için önemli)
        self._highs.sort(key=lambda p: p.bar_index)
        self._lows.sort(key=lambda p: p.bar_index)

        logger.debug(
            "[PIVOT] ingest: +%d highs, +%d lows → total highs=%d, lows=%d",
            len([p for p in self._highs if p.bar_index >= bars[0].index]),
            len([p for p in self._lows if p.bar_index >= bars[0].index]),
            len(self._highs),
            len(self._lows),
        )

    def mark_mitigated(self, kind: Literal["high", "low"], bar_index: int) -> bool:
        """
        Belirtilen pivot'un fiyat tarafından aşıldığını (mitigate) işaretler.

        Args:
            kind: "high" veya "low"
            bar_index: Mitigate edilen pivot'un mutlak bar indeksi

        Returns:
            bool: Pivot bulundu ve işaretlendi mi?
        """
        pool = self._highs if kind == "high" else self._lows
        for p in pool:
            if p.bar_index == bar_index and not p.mitigated:
                object.__setattr__(p, "mitigated", True)
                logger.debug("[PIVOT] Mitigated: %s @ bar_index=%d", kind, bar_index)
                return True
        return False

    def active_highs(self) -> list[SwingPoint]:
        """Mitigate edilmemiş (aktif) swing high'leri döner."""
        return [p for p in self._highs if not p.mitigated]

    def active_lows(self) -> list[SwingPoint]:
        """Mitigate edilmemiş (aktif) swing low'ları döner."""
        return [p for p in self._lows if not p.mitigated]

    def get_latest_active(self, kind: Literal["high", "low"]) -> SwingPoint | None:
        """
        En son oluşan aktif pivot'u döner.

        Args:
            kind: "high" veya "low"

        Returns:
            SwingPoint | None: En son aktif pivot veya None
        """
        pool = self._highs if kind == "high" else self._lows
        # Listeler kronolojik sıralı → son eleman en güncel
        for p in reversed(pool):
            if not p.mitigated:
                return p
        return None

    def cleanup(self, max_age: int = MAX_PIVOT_AGE_BARS, current_abs: int | None = None) -> None:
        """
        Çok eski pivot'ları temizler (bellek yönetimi).

        Args:
            max_age: Pivot'un yaşayabileceği maksimum bar sayısı
            current_abs: Mevcut mutlak bar indeksi (None ise en son pivot'un index'i kullanılır)
        """
        if not self._highs and not self._lows:
            return

        # current_abs verilmediyse en son pivot'un index'ini referans al
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
        """Tüm pivot hafızasını sıfırlar (yeni sembol / timeframe geçişi için)."""
        self._highs.clear()
        self._lows.clear()
        logger.info("[PIVOT] State reset: tüm pivot hafızası temizlendi")

    @property
    def total_active(self) -> int:
        """Toplam aktif (mitigate edilmemiş) pivot sayısı."""
        return sum(1 for p in self._highs + self._lows if not p.mitigated)

    @property
    def total_stored(self) -> int:
        """Hafızada tutulan toplam pivot sayısı (aktif + mitigate)."""
        return len(self._highs) + len(self._lows)
