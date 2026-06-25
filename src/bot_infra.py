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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from models import Bar, Result

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


def extract_order_id(resp: dict) -> str:
    """Binance response'dan order ID çıkar (algoId > orderId > id)."""
    return resp.get("algoId") or resp.get("orderId") or resp.get("id") or ""


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
    ts = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    writer.writerow([ts, bar.open, bar.high, bar.low, bar.close, bar.volume])


def export_ohlc_1m(bar: Bar, symbol: str) -> None:
    filepath = os.path.join("output", "live_ohlc", f"{symbol}_1m.csv")
    writer = _get_ohlc_writer(filepath)
    ts = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
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


# ─────────────────────────────────────────────────────────────────
# P9.1: RetryConfig + CircuitBreaker
# ─────────────────────────────────────────────────────────────────


@dataclass
class RetryConfig:
    """Yapılandırılabilir retry politikası.

    Varsayılanlar Binance API için optimize edilmiştir:
    - 3 deneme, exponential backoff (1s → 2s → 4s)
    - ±%25 jitter (thundering herd önleme)
    - 429/5xx hatalarında retry
    """

    max_retries: int = 3
    base_delay: float = 1.0  # ilk bekleme (saniye)
    max_delay: float = 30.0  # maksimum bekleme
    backoff_multiplier: float = 2.0  # exponential factor
    jitter: bool = True  # random jitter (±%25)
    retry_on_http: tuple[int, ...] = (429, 500, 502, 503, 504)


class CircuitBreaker:
    """Arka arkaya hatalarda devreyi kesen koruma katmanı.

    N başarısız istekten sonra M saniye boyunca tüm istekleri
    anında reddeder. Bu sayede peş peşe hatalarda API'ye gereksiz
    yük binmesini engeller.

    Tek bir kullanıcı isteğinin retry'leri tek failure sayılır
    (retry zinciri circuit breaker sayacını etkilemez).
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._open_time: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        """Devre açık mı? (istekler reddediliyor)"""
        if self._failure_count < self._failure_threshold:
            return False
        elapsed = time.time() - self._open_time
        if elapsed >= self._recovery_timeout:
            # Recovery süresi doldu → half-open
            return False
        return True

    async def record_success(self) -> None:
        """Başarılı istek — sayacı sıfırla."""
        async with self._lock:
            self._failure_count = 0

    async def record_failure(self) -> None:
        """Başarısız istek — sayacı artır, eşik aşılırsa devreyi aç."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self._failure_threshold:
                self._open_time = time.time()
                log.warning(
                    "[CIRCUIT] Devre açıldı! %d başarısız istek, "
                    "%.0f saniye boyunca istekler reddedilecek.",
                    self._failure_count,
                    self._recovery_timeout,
                )

    async def call(self, fn, *args, **kwargs) -> Any:
        """Circuit breaker kontrollü çağrı.

        Devre açıksa anında Result.fail döner, kapalıysa fn'i çağırır.
        """
        if self.is_open:
            remaining = self._recovery_timeout - (time.time() - self._open_time)
            return Result.fail(f"Circuit breaker open — {remaining:.0f}s remaining")
        try:
            result = await fn(*args, **kwargs)
            await self.record_success()
            return result
        except Exception:
            await self.record_failure()
            raise


rate_limiter = _RateLimiter(max_per_minute=1200)
