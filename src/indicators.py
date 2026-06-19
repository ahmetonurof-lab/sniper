"""
indicators.py — Teknik gosterge hesaplamalari (pure Python).
ADX(14): Momentum/Oynaklik filtreleme icin.
"""

from __future__ import annotations

import logging

from models import Bar

logger = logging.getLogger("sniper.indicators")


def compute_adx(bars: list[Bar], period: int = 14) -> float:
    """
    ADX(14) hesaplar. Wilder's smoothing kullanir.

    Parametreler:
        bars: Bar listesi (en az period * 2 + 1 bar gerekli)
        period: ADX periyodu (default 14)

    Donus: Mevcut ADX degeri. Yetersiz veride 0.0 doner.
    """
    if len(bars) < period * 2 + 1:
        return 0.0

    tr_vals: list[float] = []
    plus_dm_vals: list[float] = []
    minus_dm_vals: list[float] = []

    for i in range(1, len(bars)):
        b = bars[i]
        pb = bars[i - 1]

        tr = max(b.high - b.low, abs(b.high - pb.close), abs(b.low - pb.close))
        tr_vals.append(tr)

        up_move = b.high - pb.high
        down_move = pb.low - b.low

        if up_move > down_move and up_move > 0:
            plus_dm_vals.append(up_move)
        else:
            plus_dm_vals.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm_vals.append(down_move)
        else:
            minus_dm_vals.append(0.0)

    if len(tr_vals) < period + 1:
        return 0.0

    # Wilder's smoothing: ilk deger SMA, sonrasi EMA(alpha=1/period)
    smoothed_tr = sum(tr_vals[:period]) / period
    smoothed_plus = sum(plus_dm_vals[:period]) / period
    smoothed_minus = sum(minus_dm_vals[:period]) / period

    dx_vals: list[float] = []
    for i in range(period, len(tr_vals)):
        smoothed_tr = (smoothed_tr * (period - 1) + tr_vals[i]) / period
        smoothed_plus = (smoothed_plus * (period - 1) + plus_dm_vals[i]) / period
        smoothed_minus = (smoothed_minus * (period - 1) + minus_dm_vals[i]) / period

        if smoothed_tr == 0:
            dx_vals.append(0.0)
            continue

        plus_di = 100.0 * smoothed_plus / smoothed_tr
        minus_di = 100.0 * smoothed_minus / smoothed_tr
        di_sum = plus_di + minus_di
        if di_sum > 0:
            dx = abs(plus_di - minus_di) / di_sum * 100.0
            dx_vals.append(dx)
        else:
            dx_vals.append(0.0)

    if len(dx_vals) < period:
        return 0.0

    # ADX = Wilder's smoothing of DX
    adx = sum(dx_vals[:period]) / period
    for i in range(period, len(dx_vals)):
        adx = (adx * (period - 1) + dx_vals[i]) / period

    return adx
