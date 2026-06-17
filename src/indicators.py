"""
indicators.py
─────────────
Teknik gösterge hesaplamaları — EMA, SMMA, ATR, ADX.
Yalnızca models modülüne bağımlıdır.
"""

from __future__ import annotations

import logging
import math

import config
import numpy as np
from models import Bar
from numba import jit

logger = logging.getLogger("nexus.indicators")


def clamp(val: float, lo: float, hi: float) -> float:
    """Değeri [lo, hi] aralığına sıkıştırır."""
    return max(lo, min(hi, val))


@jit(nopython=True)
def _ema_numba(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA hesaplaması — numba JIT-derlenmiş iç döngü."""
    n = len(arr) - period + 1
    out = np.zeros(n, dtype=np.float64)
    seed = 0.0
    for k in range(period):
        seed += arr[k]
    out[0] = seed / float(period)
    mult = 2.0 / (period + 1)
    for i in range(1, n):
        out[i] = arr[period + i - 1] * mult + out[i - 1] * (1.0 - mult)
    return out


def _ema(values: list[float], period: int) -> list[float]:
    """EMA hesaplar; yeterli veri yoksa boş liste döner."""
    if len(values) < period:
        return []
    arr = np.asarray(values, dtype=np.float64)
    return _ema_numba(arr, period).tolist()


@jit(nopython=True)
def _smma_numba(arr: np.ndarray, period: int) -> np.ndarray:
    """
    SMMA / RMA (Smoothed Moving Average) — numba JIT.
    - len(arr) < period durumunda boş dizi döner.
    - np.mean slice yerine explicit loop (Numba TypingError çözümü).
    """
    n = len(arr)
    if n < period:
        return np.empty(0, dtype=np.float64)
    out_len = n - period + 1
    out = np.zeros(out_len, dtype=np.float64)
    seed = 0.0
    for k in range(period):
        seed += arr[k]
    out[0] = seed / float(period)
    p = float(period)
    for i in range(1, out_len):
        out[i] = (out[i - 1] * (p - 1.0) + arr[period + i - 1]) / p
    return out


def _smma(values: list[float], period: int) -> list[float]:
    """SMMA hesaplar; yeterli veri yoksa boş liste döner."""
    if len(values) < period:
        return []
    arr = np.asarray(values, dtype=np.float64)
    return _smma_numba(arr, period).tolist()


def compute_atr_series(bars: list[Bar], period: int = config.CHoCH_ATR_PERIOD) -> list[float]:
    """
    ATR tam `bars` listesinden hesaplanır.
    bars[i] için ATR → atr_series[i]. Yeterli geçmiş yoksa 0.0.
    """
    n = len(bars)
    if n < period + 1:
        return [0.0] * n

    highs = np.array([b.high for b in bars], dtype=np.float64)
    lo_arr = np.array([b.low for b in bars], dtype=np.float64)
    closes = np.array([b.close for b in bars], dtype=np.float64)

    tr1 = highs[1:] - lo_arr[1:]
    tr2 = np.abs(highs[1:] - closes[:-1])
    tr3 = np.abs(lo_arr[1:] - closes[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))

    smma_vals = _smma(tr.tolist(), period)
    atr_series = [0.0] * (period + 1) + smma_vals

    while len(atr_series) < n:
        atr_series.append(atr_series[-1])
    return atr_series[:n]


def compute_ema100(bars: list[Bar]) -> float:
    """100-periyot EMA (günlük trend)."""
    closes = [b.close for b in bars]
    result = _ema(closes, 100)
    return result[-1] if result else math.nan


def compute_ema200(bars: list[Bar]) -> float:
    """config.EMA_PERIOD periyotlu EMA."""
    closes = [b.close for b in bars]
    result = _ema(closes, config.EMA_PERIOD)
    return result[-1] if result else math.nan


def compute_atr_point(bars: list[Bar], period: int = 14) -> float:
    """Son bar için anlık ATR değeri döner."""
    n = len(bars)
    if n < period + 1:
        return 0.0

    highs = np.array([b.high for b in bars])
    lo_arr = np.array([b.low for b in bars])
    closes = np.array([b.close for b in bars])

    tr1 = highs[1:] - lo_arr[1:]
    tr2 = np.abs(highs[1:] - closes[:-1])
    tr3 = np.abs(lo_arr[1:] - closes[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))

    smma_tr = _smma(tr.tolist(), period)
    return smma_tr[-1] if smma_tr else 0.0


compute_atr = compute_atr_point  # skalar ATR döner — compute_atr_series DEĞİL


def compute_adx(bars: list[Bar], period: int = 14) -> float:
    """
    ADX (Average Directional Index) hesaplar.
    +DI/-DI içeride kalır, yalnızca skalar ADX döner.
    """
    n = len(bars)
    if n < period + 1:
        return 0.0

    highs = np.array([b.high for b in bars])
    lo_arr = np.array([b.low for b in bars])
    closes = np.array([b.close for b in bars])

    tr1 = highs[1:] - lo_arr[1:]
    tr2 = np.abs(highs[1:] - closes[:-1])
    tr3 = np.abs(lo_arr[1:] - closes[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))

    up = highs[1:] - highs[:-1]
    down = lo_arr[:-1] - lo_arr[1:]

    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    smma_tr = _smma(tr.tolist(), period)
    smma_plus = _smma(plus_dm.tolist(), period)
    smma_minus = _smma(minus_dm.tolist(), period)

    if not smma_tr or smma_tr[-1] == 0:
        return 0.0

    dx_series: list[float] = []
    min_len = min(len(smma_plus), len(smma_minus), len(smma_tr))
    for sp, sm, st in zip(smma_plus[:min_len], smma_minus[:min_len], smma_tr[:min_len]):
        dp = (sp / st) * 100 if st else 0
        dm = (sm / st) * 100 if st else 0
        di_sum = dp + dm
        dx_series.append((abs(dp - dm) / di_sum * 100) if di_sum else 0)

    adx_list = _smma(dx_series, period)
    return adx_list[-1] if adx_list else 0.0
