"""
bot_infra.py — sniper paper trade
Saf yardimcilar: tip tanimlari, kilit yonetimi, tick size,
OHLC export (buffered), D1 cache, rate limiter.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import math
import os
import threading
import time
from datetime import UTC, datetime
from typing import Any, TypedDict

from models import Bar

log = logging.getLogger("sniper.live")


class TradeEntry(TypedDict, total=False):
    symbol: str
    direction: str
    entry: float
    initial_sl: float
    current_sl: float
    tp: float
    lot: float
    risk_usd: float
    breakeven_level: float
    trailing_level: float
    breakeven_done: bool
    trailing_done: bool
    open_time: int | None
    status: str
    pnl: float
    last_price: float
    sl: float
    tp_val: float
    rr: float
    exit: float | None
    lot_val: float
    exit_price: float
    close_time: int


trade_locks: dict[str, asyncio.Lock] = {}
_trade_locks_lock = threading.Lock()


def get_lock(symbol: str) -> asyncio.Lock:
    with _trade_locks_lock:
        if symbol not in trade_locks:
            trade_locks[symbol] = asyncio.Lock()
    return trade_locks[symbol]


_tick_size_cache: dict[str, float] = {}


def _round_price(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    decimals = max(0, -int(math.floor(math.log10(tick))))
    return round(round(price / tick) * tick, decimals)


def fmt_bool(val: bool) -> str:
    return "✅" if val else "❌"


_ohlc_writers: dict[str, tuple] = {}


def _get_ohlc_writer(filepath: str) -> Any:
    if filepath in _ohlc_writers:
        return _ohlc_writers[filepath][1]
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    f = open(filepath, "a", newline="", encoding="utf-8-sig")
    writer = csv.writer(f)
    if f.tell() == 0:
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    _ohlc_writers[filepath] = (f, writer)
    return writer


def _flush_ohlc_writers() -> None:
    for f, _ in _ohlc_writers.values():
        try:
            f.flush()
        except Exception as e:
            log.debug("[OHLC] flush hatasi: %s", e)


def _close_ohlc_writers() -> None:
    for f, _ in _ohlc_writers.values():
        try:
            f.close()
        except Exception as e:
            log.debug("[OHLC] close hatasi: %s", e)
    _ohlc_writers.clear()


def export_ohlc_15m(bar: Bar, symbol: str) -> None:
    filepath = os.path.join("output", "live_ohlc", f"{symbol}_15m.csv")
    writer = _get_ohlc_writer(filepath)
    ts = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    writer.writerow([ts, bar.open, bar.high, bar.low, bar.close, bar.volume])


def export_ohlc_1m(bar: Bar, symbol: str) -> None:
    filepath = os.path.join("output", "live_ohlc", f"{symbol}_1m.csv")
    writer = _get_ohlc_writer(filepath)
    ts = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    writer.writerow([ts, bar.open, bar.high, bar.low, bar.close, bar.volume])


class _RateLimiter:
    def __init__(self, max_per_minute: int = 1200) -> None:
        self._interval = 60.0 / max_per_minute
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.time()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.time()


rate_limiter = _RateLimiter(max_per_minute=1200)
