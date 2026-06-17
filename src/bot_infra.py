"""
bot_infra.py — NEXUS V4
────────────────────────
Saf yardımcılar: tip tanımları, kilit yönetimi, tick size,
OHLC export (buffered), D1 cache, rate limiter.

Bağımlılıklar: models.Bar, config — LiveTradingBot instance state'ine ERIŞMEZ.
Test edilebilirlik: her sınıf/fonksiyon bağımsız olarak test edilebilir.

Orijinal konum: sonnet/src/main.py satır 38–323
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

import config
from models import Bar

log = logging.getLogger("nexus.live")

# ─────────────────────────────────────────────────────────────────────────────
# TradeEntry — active_trades için tip güvenliği
# ─────────────────────────────────────────────────────────────────────────────


class TradeEntry(TypedDict, total=False):
    """active_trades için tip güvenliği — tüm alanlar opsiyoneldir."""

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
    sl_order_id: str
    tp_order_id: str
    d1_bias: str
    h4_bias: str
    bias_strength: str | float | None
    d1_adx_at_entry: float
    fvg_score: float
    h4_sl: float
    h1_tp: float
    sweep: bool
    sweep_side: str
    sweep_level: float
    sweep_bar_index: int
    mss: bool
    mss_level: float
    mss_bar_index: int
    mss_direction: str
    impulse_origin: float | None
    fvg_upper: float
    fvg_lower: float
    fvg_bar_index: int
    fvg_direction: str
    retrace: bool
    ltf: bool
    fvg_missed: bool
    state: str
    partial: bool
    protection_missing: bool
    protection_repairing: bool
    sl: float
    tp_val: float
    rr: float
    exit: float | None
    lot_val: float
    exit_price: float
    close_time: int


# ─────────────────────────────────────────────────────────────────────────────
# Asenkron kilit yönetimi
# ─────────────────────────────────────────────────────────────────────────────

trade_locks: dict[str, asyncio.Lock] = {}
_trade_locks_lock = threading.Lock()


def get_lock(symbol: str) -> asyncio.Lock:
    with _trade_locks_lock:
        if symbol not in trade_locks:
            trade_locks[symbol] = asyncio.Lock()
    return trade_locks[symbol]


# ─────────────────────────────────────────────────────────────────────────────
# Tick size + fiyat yuvarlama
# ─────────────────────────────────────────────────────────────────────────────

_tick_size_cache: dict[str, float] = {}


def _get_tick_size(symbol: str) -> float:
    """http_client modül seviyesi global'i kullanır — main.py'den gelir."""
    if symbol in _tick_size_cache:
        return _tick_size_cache[symbol]
    try:
        # http_client main.py'de tanımlı, buradan erişmek için geç import kullanılır
        from bot import http_client as _http_client  # noqa: PLC0415

        tick = _http_client.get_tick_size(symbol)
        _tick_size_cache[symbol] = tick
        return tick
    except Exception:
        return 0.0001


def _round_price(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    decimals = max(0, -int(math.floor(math.log10(tick))))
    return round(round(price / tick) * tick, decimals)


def fmt_bool(val: bool) -> str:
    """✅ / ❌ — boolean değerleri görsel log için formatla."""
    return "✅" if val else "❌"


# ─────────────────────────────────────────────────────────────────────────────
# OHLC Export — Buffered CSV writers
# ─────────────────────────────────────────────────────────────────────────────

_ohlc_writers: dict[str, tuple] = {}  # filepath → (file_handle, csv_writer)


def _get_ohlc_writer(filepath: str) -> Any:
    """Cache'lenmiş CSV writer — her çağrıda open() yapmaz."""
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
    """Tüm açık OHLC dosyalarını flush'la (periyodik çağrı)."""
    for f, _ in _ohlc_writers.values():
        try:
            f.flush()
        except Exception as e:
            log.debug("[OHLC] flush hatası: %s", e)


def _close_ohlc_writers() -> None:
    """Tüm açık OHLC dosyalarını kapat (test temizliği / shutdown)."""
    for f, _ in _ohlc_writers.values():
        try:
            f.close()
        except Exception as e:
            log.debug("[OHLC] close hatası: %s", e)
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


# ─────────────────────────────────────────────────────────────────────────────
# D1 Cache
# ─────────────────────────────────────────────────────────────────────────────


class DailyDataCache:
    def __init__(self) -> None:
        self._cache: dict[str, list[Bar]] = {}
        self._last_update: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def get(self, symbol: str) -> list[Bar]:
        now = datetime.now().timestamp()
        async with self._lock:
            if symbol not in self._cache or now - self._last_update.get(symbol, 0) > 86400:
                try:
                    await self._fetch(symbol)
                except Exception:
                    pass
            return self._cache.get(symbol, [])

    async def _fetch(self, symbol: str) -> None:
        try:
            # rate_limiter ve http_client main modülünden gelir
            from bot import (
                http_client as _http_client,  # noqa: PLC0415
                rate_limiter as _rate_limiter,  # noqa: PLC0415
            )

            await _rate_limiter.acquire()
            loop = asyncio.get_running_loop()
            ohlcv = await loop.run_in_executor(
                None,
                lambda: _http_client.get_klines(symbol, interval="1d", limit=config.D1_BARS, max_retries=2),
            )
            bars = [
                Bar(
                    index=i,
                    open=k[1],
                    high=k[2],
                    low=k[3],
                    close=k[4],
                    volume=k[5],
                    timestamp=int(k[0]),
                )
                for i, k in enumerate(ohlcv)
            ]
            self._cache[symbol] = bars
            self._last_update[symbol] = datetime.now().timestamp()
            log.info("D1 cache yenilendi: %s (%d bar)", symbol.ljust(12), len(bars))
        except Exception as e:
            log.error("D1 verisi alınamadı %s: %s", symbol.ljust(12), e)


# ─────────────────────────────────────────────────────────────────────────────
# Rate Limiter
# ─────────────────────────────────────────────────────────────────────────────


class _RateLimiter:
    """Token bucket: dakikada max N istek, asyncio-safe."""

    def __init__(self, max_per_minute: int = 5000) -> None:
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


# Modül seviyesi singleton — bot.py tarafından import edilir
rate_limiter = _RateLimiter(max_per_minute=5000)
