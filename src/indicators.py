"""
indicators.py — Gerçek Wilder's Average True Range (ATR) ve yardımcıları.

Bu modül, kod tabanında sahte ATR olarak kullanılan
  max(current.range, current.close * DEFAULT_ATR_FALLBACK_PCT)
yerine gerçek Wilder's smoothing 14-periyotluk ATR hesaplar.
"""

from __future__ import annotations

from models import ATR_PERIOD, Bar


def calculate_true_range(bar: Bar, prev_close: float) -> float:
    """Tek bir bar için True Range hesapla.

    TR = max(
        high - low,
        |high - prev_close|,
        |low - prev_close|,
    )
    """
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


def update_atr(prev_atr: float | None, tr: float, period: int = ATR_PERIOD) -> float:
    """Wilder's smoothing ile ATR güncelle.

    Args:
        prev_atr: Önceki ATR değeri. None ise ilk hesaplama — tr başlangıç değeri olur.
        tr: Güncel True Range.
        period: ATR periyodu (default: ATR_PERIOD = 14).

    Returns:
        Güncellenmiş ATR değeri.
    """
    if prev_atr is None:
        return tr
    return (prev_atr * (period - 1) + tr) / period


def build_atr_from_bars(
    bars: list[Bar],
    period: int = ATR_PERIOD,
) -> float:
    """Verilen bar listesinden rolling Wilder's ATR inşa et.

    İlk bar'da prev_close = bar.open (ilk TR hesaplaması için),
    sonraki barlarda normal TR → Wilder's smoothing uygulanır.

    Args:
        bars: Kronolojik sıralı Bar listesi (en az 1 eleman).
        period: ATR periyodu.

    Returns:
        Son bar sonrası ATR değeri. Boş liste için 0.0 döner.
    """
    if not bars:
        return 0.0

    atr: float | None = None
    prev_close: float = bars[0].open  # ilk bar için referans

    for bar in bars:
        tr = calculate_true_range(bar, prev_close)
        atr = update_atr(atr, tr, period)
        prev_close = bar.close

    return atr if atr is not None else 0.0


def compute_atr_series(bars: list[Bar], period: int = ATR_PERIOD) -> list[float]:
    """Bar bazlı ATR serisi (sonnet indicators.compute_atr_series ile birebir).

    bars[i] için ATR → atr_series[i]. Yeterli geçmiş yoksa 0.0.
    SMMA (Wilder smoothing) seed'i ilk `period` TR'nin ortalamasıdır.
    Sonnet'teki numba/numpy implementasyonunun saf Python karşılığıdır
    (canlı venv'de numpy/numba bağımlılığı eklememek için).
    """
    n = len(bars)
    if n < period + 1:
        return [0.0] * n

    trs = [0.0] * n
    for i in range(1, n):
        b, pc = bars[i], bars[i - 1].close
        trs[i] = max(
            b.high - b.low,
            abs(b.high - pc),
            abs(b.low - pc),
        )

    smma_vals: list[float] = [sum(trs[1 : period + 1]) / period]
    for i in range(period + 1, n):
        smma_vals.append((smma_vals[-1] * (period - 1) + trs[i]) / period)

    atr_series = [0.0] * (period + 1) + smma_vals
    while len(atr_series) < n:
        atr_series.append(atr_series[-1])
    return atr_series[:n]
