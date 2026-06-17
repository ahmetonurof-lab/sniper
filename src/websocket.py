"""
websocket.py
────────────
Binance Futures/Spot multi-symbol WebSocket hub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

import websockets
from models import Bar
from websockets.exceptions import ConnectionClosed, InvalidStatus

# ──────────────────────────────────────────────
# İşlem Cooldown (Soğuma) Mekanizması
# ──────────────────────────────────────────────
last_trade_time: dict[str, float] = {}
COOLDOWN_MINUTES = 15


def is_cooldown_active(symbol: str) -> bool:
    current_time = time.time()
    if symbol in last_trade_time:
        time_elapsed = (current_time - last_trade_time[symbol]) / 60
        if time_elapsed < COOLDOWN_MINUTES:
            return True
    return False


def register_trade(symbol: str) -> None:
    last_trade_time[symbol] = time.time()


log = logging.getLogger("ws_hub")

BarCallback = Callable[[list[Bar]], Awaitable[None]]

_TF_TO_STREAM = {
    "1m": "kline_1m",
    "3m": "kline_3m",
    "5m": "kline_5m",
    "15m": "kline_15m",
    "30m": "kline_30m",
    "1h": "kline_1h",
    "4h": "kline_4h",
    "1d": "kline_1d",
}

class _BarBuffer:
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        callbacks: list[BarCallback],
        max_bars: int = 500,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.callbacks = callbacks
        self.max_bars = max_bars
        self._bars: list[Bar] = []
        self._next_index: int = 0

    def _kline_to_bar(self, k: dict, is_closed: bool) -> Bar:
        bar = Bar(
            index=self._next_index if is_closed else max(0, self._next_index - 1),
            timestamp=int(k["t"]),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            is_closed=is_closed,
        )
        if is_closed:
            self._next_index += 1
        return bar

    async def feed(self, kline_payload: dict) -> None:
        k = kline_payload["k"]
        is_closed = k["x"]
        bar = self._kline_to_bar(k, is_closed)

        if is_closed:
            if self._bars and bar.timestamp == self._bars[-1].timestamp:
                return

            self._bars.append(bar)
            if len(self._bars) > self.max_bars:
                self._bars = self._bars[-self.max_bars :]

            snapshot = list(self._bars)
            for cb in self.callbacks:
                try:
                    await cb(snapshot)
                except Exception:
                    log.exception("Callback hatası: %s %s", self.symbol, self.timeframe)


class BinanceWSHub:
    BASE_URL = "wss://fstream.binance.com/stream?streams="

    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str] | None = None,
        max_bars: int = 500,
        base_url: str | None = None,
        reconnect_delay: float = 2.0,
        max_reconnect_delay: float = 60.0,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self.timeframes = list(timeframes or ["1m", "5m", "15m", "1h"])
        self.max_bars = max_bars
        self.base_url = base_url or self.BASE_URL
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay

        self._buffers: dict[tuple[str, str], _BarBuffer] = {}
        self._callbacks: dict[tuple[str, str], list[BarCallback]] = defaultdict(list)

        self._stop_event = asyncio.Event()
        self._reconnect_count = 0
        self._user_data_listen_key: str | None = None
        self._user_data_ws_url: str | None = None
        self._user_data_ws: websockets.WebSocketClientProtocol | None = None
        self._user_data_task: asyncio.Task | None = None
        self._user_data_callbacks: dict[str, list[Callable[[dict], Awaitable[None]]]] = defaultdict(list)
        self._ws: websockets.WebSocketClientProtocol | None = None

    def register_callback(self, symbol: str, timeframe: str, callback: BarCallback) -> None:
        key = (symbol.upper(), timeframe)
        self._callbacks[key].append(callback)

    def on_user_data(self, event_type: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self._user_data_callbacks[event_type].append(fn)
            return fn
        return decorator

    def set_user_data_listen_key(self, listen_key: str, ws_base_url: str = "wss://fstream.binance.com") -> None:
        self._user_data_listen_key = listen_key
        self._user_data_ws_url = f"{ws_base_url.rstrip('/')}/ws/{listen_key}"

    def _build_url(self) -> str:
        streams = []
        for sym in self.symbols:
            for tf in self.timeframes:
                stream_name = _TF_TO_STREAM.get(tf)
                if stream_name:
                    streams.append(f"{sym.lower()}@{stream_name}")
        return self.base_url + "/".join(streams)

    def get_bars(self, symbol: str, timeframe: str) -> list[Bar]:
        buf = self._get_buffer(symbol, timeframe)
        return list(buf._bars) if buf else []

    def _get_buffer(self, symbol: str, timeframe: str) -> _BarBuffer | None:
        key = (symbol.upper(), timeframe)
        if key not in self._buffers:
            # Buffer yoksa oluştur (prefill için bar listesi boş olsa bile nesne lazım)
            self._buffers[key] = _BarBuffer(symbol.upper(), timeframe, self._callbacks[key], self.max_bars)
        return self._buffers.get(key)

    async def _dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            stream = msg.get("stream", "")
            data = msg.get("data", msg)
            if data.get("e") != "kline": return
            parts = stream.split("@")
            if len(parts) != 2: return
            symbol = parts[0].upper()
            tf = parts[1].replace("kline_", "")
            key = (symbol, tf)
            if key not in self._buffers:
                self._buffers[key] = _BarBuffer(symbol, tf, self._callbacks[key], self.max_bars)
            await self._buffers[key].feed(data)
        except Exception:
            pass

    async def run(self) -> None:
        delay = self.reconnect_delay
        while not self._stop_event.is_set():
            try:
                url = self._build_url()
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    async for raw in ws:
                        if self._stop_event.is_set(): break
                        await self._dispatch(raw)
                delay = self.reconnect_delay
            except Exception:
                if self._stop_event.is_set(): break
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_reconnect_delay)

    def stop(self) -> None:
        self._stop_event.set()
