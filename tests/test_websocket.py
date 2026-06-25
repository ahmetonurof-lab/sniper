"""
test_websocket.py — BinanceWSHub + _BarBuffer + cooldown unit tests.
"""

import asyncio
import json
import time
from unittest.mock import patch

import pytest

from models import Bar
from websocket import (
    BinanceWSHub,
    _BarBuffer,
    _TF_TO_STREAM,
    is_cooldown_active,
    register_trade,
    COOLDOWN_MINUTES,
    last_trade_time,
)


# ── Helpers ───────────────────────────────────────────────────────


def _bar(index, open_, high, low, close, is_closed=True, timestamp=0):
    return Bar(
        index=index,
        open=open_,
        high=high,
        low=low,
        close=close,
        is_closed=is_closed,
        timestamp=timestamp,
    )


def _kline_msg(symbol="btcusdt", interval="5m", close=100.0, is_closed=True, ts=None):
    """Build a combined-stream kline message."""
    if ts is None:
        ts = int(time.time() * 1000)
    return json.dumps(
        {
            "stream": f"{symbol}@kline_{interval}",
            "data": {
                "e": "kline",
                "k": {
                    "t": ts,
                    "T": ts + 299999,
                    "o": "99.0",
                    "h": "102.0",
                    "l": "98.0",
                    "c": str(close),
                    "v": "1000.0",
                    "x": is_closed,
                },
            },
        }
    )


# ═══════════════════════════════════════════════════════════════════
# Cooldown tests
# ═══════════════════════════════════════════════════════════════════


class TestCooldown:
    def setup_method(self):
        last_trade_time.clear()

    def test_is_cooldown_active_false_initially(self):
        assert is_cooldown_active("BTCUSDT") is False

    def test_is_cooldown_active_true_after_register(self):
        register_trade("BTCUSDT")
        assert is_cooldown_active("BTCUSDT") is True

    def test_is_cooldown_active_false_after_cooldown(self):
        # Set trade time far in the past
        last_trade_time["BTCUSDT"] = time.time() - (COOLDOWN_MINUTES + 1) * 60
        assert is_cooldown_active("BTCUSDT") is False

    def test_register_trade_updates_time(self):
        register_trade("BTCUSDT")
        t1 = last_trade_time["BTCUSDT"]
        time.sleep(0.01)
        register_trade("BTCUSDT")
        t2 = last_trade_time["BTCUSDT"]
        assert t2 > t1


# ═══════════════════════════════════════════════════════════════════
# _BarBuffer tests
# ═══════════════════════════════════════════════════════════════════


class TestBarBuffer:
    def setup_method(self):
        self.callback_log = []

    async def _cb(self, bars):
        self.callback_log.append(list(bars))

    def test_kline_to_bar_conversion(self):
        buf = _BarBuffer("BTCUSDT", "5m", [])
        kline = {"t": 1000, "o": "100", "h": "110", "l": "90", "c": "105", "v": "500"}
        bar = buf._kline_to_bar(kline, is_closed=True)
        assert bar.open == 100.0
        assert bar.high == 110.0
        assert bar.low == 90.0
        assert bar.close == 105.0
        assert bar.volume == 500.0
        assert bar.timestamp == 1000

    @pytest.mark.asyncio
    async def test_feed_appends_closed_bar_and_calls_callback(self):
        buf = _BarBuffer("BTCUSDT", "5m", [self._cb])
        payload = {
            "k": {
                "t": 1000,
                "o": "100",
                "h": "110",
                "l": "90",
                "c": "105",
                "v": "500",
                "x": True,
            },
        }
        await buf.feed(payload)
        assert len(buf._bars) == 1
        assert buf._bars[0].close == 105.0
        assert len(self.callback_log) == 1
        assert len(self.callback_log[0]) == 1

    @pytest.mark.asyncio
    async def test_feed_does_not_call_callback_on_unclosed(self):
        buf = _BarBuffer("BTCUSDT", "5m", [self._cb])
        payload = {
            "k": {
                "t": 1000,
                "o": "100",
                "h": "110",
                "l": "90",
                "c": "105",
                "v": "500",
                "x": False,
            },
        }
        await buf.feed(payload)
        assert len(buf._bars) == 0
        assert len(self.callback_log) == 0

    @pytest.mark.asyncio
    async def test_feed_skips_duplicate_timestamp(self):
        buf = _BarBuffer("BTCUSDT", "5m", [self._cb])
        payload = {
            "k": {
                "t": 1000,
                "o": "100",
                "h": "110",
                "l": "90",
                "c": "105",
                "v": "500",
                "x": True,
            },
        }
        await buf.feed(payload)
        assert len(buf._bars) == 1
        await buf.feed(payload)  # Same timestamp
        assert len(buf._bars) == 1  # Not appended again

    @pytest.mark.asyncio
    async def test_feed_max_bars_eviction(self):
        buf = _BarBuffer("BTCUSDT", "5m", [self._cb], max_bars=3)
        for i in range(5):
            payload = {
                "k": {
                    "t": i * 1000,
                    "o": "100",
                    "h": "110",
                    "l": "90",
                    "c": str(100 + i),
                    "v": "500",
                    "x": True,
                },
            }
            await buf.feed(payload)
        assert len(buf._bars) == 3
        assert buf._bars[0].timestamp == 2000  # Evicted first 2
        assert buf._bars[-1].timestamp == 4000

    @pytest.mark.asyncio
    async def test_feed_next_index_management(self):
        buf = _BarBuffer("BTCUSDT", "5m", [])
        for i in range(3):
            payload = {
                "k": {
                    "t": i * 1000,
                    "o": "100",
                    "h": "110",
                    "l": "90",
                    "c": "105",
                    "v": "500",
                    "x": True,
                },
            }
            await buf.feed(payload)
        assert buf._next_index == 3
        assert buf._bars[0].index == 0
        assert buf._bars[1].index == 1
        assert buf._bars[2].index == 2

    @pytest.mark.asyncio
    async def test_multiple_callbacks(self):
        log1 = []
        log2 = []

        async def cb1(bars):
            log1.append(len(bars))

        async def cb2(bars):
            log2.append(len(bars))

        buf = _BarBuffer("BTCUSDT", "5m", [cb1, cb2])
        payload = {
            "k": {
                "t": 1000,
                "o": "100",
                "h": "110",
                "l": "90",
                "c": "105",
                "v": "500",
                "x": True,
            },
        }
        await buf.feed(payload)
        assert log1 == [1]
        assert log2 == [1]

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_block_other_callbacks(self):
        log = []

        async def bad_cb(bars):
            raise RuntimeError("callback error")

        async def good_cb(bars):
            log.append(len(bars))

        buf = _BarBuffer("BTCUSDT", "5m", [bad_cb, good_cb])
        payload = {
            "k": {
                "t": 1000,
                "o": "100",
                "h": "110",
                "l": "90",
                "c": "105",
                "v": "500",
                "x": True,
            },
        }
        await buf.feed(payload)
        assert log == [1]  # Good callback still fired


# ═══════════════════════════════════════════════════════════════════
# BinanceWSHub tests
# ═══════════════════════════════════════════════════════════════════


class TestBinanceWSHubInit:
    def test_default_initialization(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"])
        assert hub.symbols == ["BTCUSDT"]
        assert "1m" in hub.timeframes
        assert hub.max_bars == 500
        assert hub._stop_event.is_set() is False

    def test_custom_timeframes(self):
        hub = BinanceWSHub(symbols=["ETHUSDT"], timeframes=["5m", "15m"])
        assert hub.timeframes == ["5m", "15m"]

    def test_symbols_uppercased(self):
        hub = BinanceWSHub(symbols=["btcusdt"])
        assert hub.symbols == ["BTCUSDT"]

    def test_custom_base_url(self):
        hub = BinanceWSHub(
            symbols=["BTCUSDT"], base_url="wss://test.stream/stream?streams="
        )
        assert hub.base_url == "wss://test.stream/stream?streams="


class TestBuildUrl:
    def test_single_symbol_single_timeframe(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        url = hub._build_url()
        assert "btcusdt@kline_5m" in url

    def test_multi_symbol_multi_timeframe(self):
        hub = BinanceWSHub(symbols=["BTCUSDT", "ETHUSDT"], timeframes=["1m", "15m"])
        url = hub._build_url()
        assert "btcusdt@kline_1m" in url
        assert "btcusdt@kline_15m" in url
        assert "ethusdt@kline_1m" in url
        assert "ethusdt@kline_15m" in url

    def test_invalid_timeframe_raises(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["999m"])
        with pytest.raises(ValueError, match="Desteklenmeyen timeframe"):
            hub._build_url()


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_routes_kline_to_correct_buffer(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        msg = _kline_msg("btcusdt", "5m", close=105.0)
        with patch.object(_BarBuffer, "feed") as mock_feed:
            mock_feed.return_value = asyncio.Future()
            mock_feed.return_value.set_result(None)
            await hub._dispatch(msg)
            assert mock_feed.call_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_ignores_non_kline_events(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        msg = json.dumps(
            {
                "stream": "btcusdt@aggTrade",
                "data": {"e": "aggTrade", "p": "100.0"},
            }
        )
        with patch.object(_BarBuffer, "feed") as mock_feed:
            await hub._dispatch(msg)
            mock_feed.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_handles_invalid_json(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        # Should not raise
        await hub._dispatch("not valid json{")

    @pytest.mark.asyncio
    async def test_dispatch_updates_last_seen(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        msg = _kline_msg("btcusdt", "5m", close=105.0)
        with patch.object(_BarBuffer, "feed") as mock_feed:
            mock_feed.return_value = asyncio.Future()
            mock_feed.return_value.set_result(None)
            await hub._dispatch(msg)
        assert ("BTCUSDT", "5m") in hub._last_seen


class TestGetBarsPrefill:
    def test_get_bars_empty_initially(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        assert hub.get_bars("BTCUSDT", "5m") == []

    def test_prefill_bars_sets_buffer(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        bars = [_bar(0, 100, 105, 95, 102), _bar(1, 102, 108, 96, 104)]
        hub.prefill_bars("BTCUSDT", "5m", bars)
        result = hub.get_bars("BTCUSDT", "5m")
        assert len(result) == 2
        assert result[0].open == 100.0

    def test_get_bars_returns_copy(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        bars = [_bar(0, 100, 105, 95, 102)]
        hub.prefill_bars("BTCUSDT", "5m", bars)
        result = hub.get_bars("BTCUSDT", "5m")
        result.append(_bar(999, 1, 1, 1, 1))
        # Original buffer unchanged
        assert len(hub.get_bars("BTCUSDT", "5m")) == 1


class TestCallbackRegistration:
    def test_register_callback(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])
        called = []

        async def handler(bars):
            called.append(True)

        hub.register_callback("BTCUSDT", "5m", handler)
        key = ("BTCUSDT", "5m")
        assert len(hub._callbacks[key]) == 1
        assert hub._callbacks[key][0] is handler

    def test_on_bar_decorator(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"], timeframes=["5m"])

        @hub.on_bar("BTCUSDT", "5m")
        async def handler(bars):
            pass

        key = ("BTCUSDT", "5m")
        assert len(hub._callbacks[key]) == 1


class TestStop:
    def test_stop_sets_event(self):
        hub = BinanceWSHub(symbols=["BTCUSDT"])
        assert hub._stop_event.is_set() is False
        hub.stop()
        assert hub._stop_event.is_set() is True


# ═══════════════════════════════════════════════════════════════════
# _TF_TO_STREAM
# ═══════════════════════════════════════════════════════════════════


class TestTfToStream:
    def test_known_timeframes(self):
        assert _TF_TO_STREAM["1m"] == "kline_1m"
        assert _TF_TO_STREAM["5m"] == "kline_5m"
        assert _TF_TO_STREAM["15m"] == "kline_15m"
        assert _TF_TO_STREAM["1h"] == "kline_1h"
        assert _TF_TO_STREAM["4h"] == "kline_4h"
        assert _TF_TO_STREAM["1d"] == "kline_1d"
