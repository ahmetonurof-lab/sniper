"""
test_bot.py — PaperTrader orchestrator unit tests.
Heavy mocking of external dependencies (WS, REST, config, trading).
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import Bar, ActiveTrade
from retrace_state import RetraceState, HTFFVG
from session import SessionState


# ── Helpers ───────────────────────────────────────────────────────


def _bar(index, open_, high, low, close, is_closed=True, timestamp=0):
    return Bar(
        index=index,
        open=open_,
        high=high,
        low=low,
        close=close,
        is_closed=is_closed,
        timestamp=timestamp or (index * 900000),
    )


def _make_15m_bars_sweep_bullish(n=30, base=100.0):
    """Craft bars with a CBDR-like sweep scenario (bullish)."""
    bars = []
    for i in range(n):
        o = base + i * 0.5
        if i == 25:  # Sweep bar: wick breaks below, close recovers
            bars.append(_bar(i, 112, 115, 103, 114, timestamp=i * 900000))
        elif i == 26:  # Body breaks above
            bars.append(_bar(i, 114, 118, 112, 117, timestamp=i * 900000))
        else:
            bars.append(_bar(i, o, o + 2, o - 2, o + 1, timestamp=i * 900000))
    return bars


# ═══════════════════════════════════════════════════════════════════
# PaperTrader init tests
# ═══════════════════════════════════════════════════════════════════


class TestPaperTraderInit:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot._setup_logging", return_value=MagicMock())
    def test_init_sets_up_symbols_states_rsms(
        self, mock_setup_log, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        _setup_minimal_cfg(mock_cfg, symbols=["BTCUSDT", "ETHUSDT"])
        mock_cfg.FVG_SIZE_MAP = {"BTCUSDT": 0.5, "ETHUSDT": 0.3}

        # Use a fresh import of PaperTrader class with active patches
        import bot as bot_module

        trader = bot_module.PaperTrader()

        assert trader.symbols == ["BTCUSDT", "ETHUSDT"]
        assert len(trader.states) == 2
        assert isinstance(trader.states["BTCUSDT"], SessionState)
        assert len(trader.rsms) == 2
        assert len(trader.cfgs) == 2
        assert trader.cfgs["BTCUSDT"]["MIN_FVG_SIZE"] == 0.5

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_init_custom_symbols(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        mock_cfg.SYMBOLS = ["BTCUSDT", "ETHUSDT"]
        mock_cfg.IS_TESTNET = False
        mock_cfg.FVG_SIZE_MAP = {}
        mock_cfg.SL_ATR_MULT = 1.5
        mock_cfg.TP_RR = 2.0
        mock_cfg.FVG_BUFFER_MULT = 0.3
        mock_cfg.INITIAL_BALANCE = 1000.0
        mock_cfg.RISK_PER_TRADE = 0.01
        mock_cfg.BINANCE_API_KEY = ""
        mock_cfg.BINANCE_API_SECRET = ""
        mock_cfg.RETRADE_FVG_SIZE_MULT = 0.8
        mock_cfg.LEVERAGE = 10
        mock_cfg.SYMBOL_RISK_MAP = {}
        mock_cfg.RETRADE_FVG_MAX_ATTEMPTS = 3
        mock_cfg.CBDR_SWEEP_ATR_TOLERANCE_MULT = 0.3
        mock_cfg.CBDR_SWEEP_DEFAULT_TOLERANCE = 5.0
        mock_cfg.CBDR_DEAD_THRESHOLD_PCT = 0.5
        mock_cfg.ASIA_DEAD_THRESHOLD_PCT = 0.3
        mock_cfg.DEFAULT_ATR_FALLBACK_PCT = 0.005

        from bot import PaperTrader

        bot = PaperTrader(symbols=["SOLUSDT"])
        assert bot.symbols == ["SOLUSDT"]


# ═══════════════════════════════════════════════════════════════════
# _session_label tests
# ═══════════════════════════════════════════════════════════════════


class TestSessionLabel:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_labels(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader()
        assert bot._session_label(22) == "ASIA"
        assert bot._session_label(23) == "ASIA"
        assert bot._session_label(0) == "ASIA"
        assert bot._session_label(1) == "ASIA"
        assert bot._session_label(2) == "LONDON"
        assert bot._session_label(8) == "LONDON"
        assert bot._session_label(12) == "LONDON"
        assert bot._session_label(13) == "NEWYORK"
        assert bot._session_label(18) == "NEWYORK"
        assert bot._session_label(21) == "NEWYORK"


# ═══════════════════════════════════════════════════════════════════
# _on_15m_close tests
# ═══════════════════════════════════════════════════════════════════


class TestOn15mClose:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_skips_asia_session(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        # Bar at 22:00 UTC
        bars = [_bar(i, 100, 102, 98, 101, timestamp=i * 900000) for i in range(20)]
        # Make bar timestamp = 22:00 UTC
        dt_22 = datetime(2026, 6, 26, 22, 0, tzinfo=UTC)
        bars[-1] = _bar(19, 100, 102, 98, 101, timestamp=int(dt_22.timestamp() * 1000))

        asyncio.run(bot._on_15m_close("BTCUSDT", bars))
        # ASIA session should skip, _pl should be called with "st_ses"
        # We verify the stage is None/popped
        assert "BTCUSDT" not in bot._stage

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_skips_when_cbdr_not_locked(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        # Bars at LONDON session (8:00 UTC) but CBDR not locked yet
        dt_8 = datetime(2026, 6, 26, 8, 0, tzinfo=UTC)
        bars = [_bar(i, 100, 102, 98, 101, timestamp=i * 900000) for i in range(20)]
        bars[-1] = _bar(19, 100, 102, 98, 101, timestamp=int(dt_8.timestamp() * 1000))

        asyncio.run(bot._on_15m_close("BTCUSDT", bars))
        # Should skip because CBDR is not locked
        assert bot.states["BTCUSDT"].cbdr_locked is False

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_skips_when_trade_active(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        # Put an active trade
        bot.active_trades["BTCUSDT"] = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
        )
        dt_13 = datetime(2026, 6, 26, 13, 0, tzinfo=UTC)
        bars = [_bar(i, 100, 102, 98, 101, timestamp=i * 900000) for i in range(20)]
        bars[-1] = _bar(19, 100, 102, 98, 101, timestamp=int(dt_13.timestamp() * 1000))

        with patch.object(bot.reporter, "display_active_position") as mock_active:
            asyncio.run(bot._on_15m_close("BTCUSDT", bars))
            mock_active.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# _try_entry tests
# ═══════════════════════════════════════════════════════════════════


class TestTryEntry:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.log")
    def test_skips_when_trade_already_active(
        self, mock_log, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]
        bot.active_trades["BTCUSDT"] = ActiveTrade(side="long")

        current = _bar(20, 100, 105, 95, 102)
        asyncio.run(
            bot._try_entry(
                sym="BTCUSDT",
                current=current,
                atr_val=3.0,
                rsm=rsm,
                ss=ss,
                sweep_dir="bullish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
                is_retrade=False,
            )
        )
        assert rsm.state == RetraceState.IDLE  # Reset called

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_calculates_sl_tp_long(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.SYMBOL_RISK_MAP = {}

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

        # Set up a trigger FVG
        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bullish"
        rsm.trigger_fvg = HTFFVG(
            top=105.0, bottom=103.0, direction="bullish", bar_index=5
        )
        ss.london_high = 110.0
        ss.london_low = 95.0

        current = _bar(20, 108, 110, 106, 109)
        asyncio.run(
            bot._try_entry(
                sym="BTCUSDT",
                current=current,
                atr_val=3.0,
                rsm=rsm,
                ss=ss,
                sweep_dir="bullish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
                is_retrade=False,
            )
        )
        # After a successful entry, active_trades should have BTCUSDT
        assert "BTCUSDT" in bot.active_trades
        trade = bot.active_trades["BTCUSDT"]
        assert trade.side == "long"
        assert trade.entry_price == 109.0

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_calculates_sl_tp_short(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.SYMBOL_RISK_MAP = {}

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bearish"
        rsm.trigger_fvg = HTFFVG(
            top=100.0, bottom=98.0, direction="bearish", bar_index=5
        )
        ss.london_high = 110.0
        ss.london_low = 95.0

        current = _bar(20, 95, 97, 93, 94)
        asyncio.run(
            bot._try_entry(
                sym="BTCUSDT",
                current=current,
                atr_val=3.0,
                rsm=rsm,
                ss=ss,
                sweep_dir="bearish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
                is_retrade=False,
            )
        )
        assert "BTCUSDT" in bot.active_trades
        trade = bot.active_trades["BTCUSDT"]
        assert trade.side == "short"
        assert trade.entry_price == 94.0

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.log")
    @patch("bot.EntryManager")
    def test_skips_when_qty_zero(
        self, mock_entry_mgr, mock_log, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.SYMBOL_RISK_MAP = {}

        # Mock EntryManager to return valid sl/tp but zero qty
        mock_entry_mgr.calculate_sl_tp.return_value = (100.0, 118.0)
        mock_entry_mgr.calculate_qty.return_value = 0.0
        mock_entry_mgr.validate_risk.return_value = (True, "")

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bullish"

        current = _bar(20, 108, 110, 106, 109)
        asyncio.run(
            bot._try_entry(
                sym="BTCUSDT",
                current=current,
                atr_val=3.0,
                rsm=rsm,
                ss=ss,
                sweep_dir="bullish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
                is_retrade=False,
            )
        )
        # Should skip because qty <= 0
        assert "BTCUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.IDLE


# ═══════════════════════════════════════════════════════════════════
# _on_1m_close tests
# ═══════════════════════════════════════════════════════════════════


class TestOn1mClose:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_skips_when_no_active_trade(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        # Should just return without error

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.TrailingManager")
    def test_calls_trail_check_when_active(
        self, mock_trail_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])

        trade = ActiveTrade(
            symbol="BTCUSDT", side="long", entry_price=50000.0, sl=49000.0, tp=52000.0
        )
        bot.active_trades["BTCUSDT"] = trade

        # Mock hub.get_bars to return some 15m bars
        mock_hub = bot.hub
        mock_hub.get_bars.return_value = [_bar(i, 100, 105, 95, 102) for i in range(20)]

        # Mock TrailingManager.evaluate_trail
        mock_result = MagicMock()
        mock_result.updated = False
        mock_trail_mgr.evaluate_trail.return_value = mock_result

        # Mock TrailingManager.check_exit
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.check_exit.return_value = mock_exit

        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        mock_trail_mgr.evaluate_trail.assert_called_once()
        mock_trail_mgr.check_exit.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# _exit_trade tests
# ═══════════════════════════════════════════════════════════════════


class TestExitTrade:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.RetradeEngine")
    @patch("bot.mark_trade_closed")
    def test_pnl_calc_long(
        self,
        mock_mark_closed,
        mock_retrade_engine,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        _setup_minimal_cfg(mock_cfg)
        # Override module-level INITIAL_CAPITAL
        with patch("bot.INITIAL_CAPITAL", 1000.0), patch("bot.RISK_PER_TRADE", 0.01):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            exit_price=51000.0,
            exit_bar=50,
            result="TP",
        )
        bot.active_trades["BTCUSDT"] = trade

        current = _bar(50, 51000, 51200, 50800, 51000)
        asyncio.run(bot._exit_trade("BTCUSDT", trade, current, 50000))

        # PnL = (51000 - 50000) * 0.1 = 100
        assert bot._balance == 1100.0  # 1000 + 100
        assert "BTCUSDT" not in bot.active_trades

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.RetradeEngine")
    @patch("bot.mark_trade_closed")
    def test_pnl_calc_short(
        self,
        mock_mark_closed,
        mock_retrade_engine,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        _setup_minimal_cfg(mock_cfg)
        with patch("bot.INITIAL_CAPITAL", 1000.0), patch("bot.RISK_PER_TRADE", 0.01):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="short",
            entry_price=50000.0,
            sl=51000.0,
            tp=48000.0,
            qty=0.1,
            exit_price=49000.0,
            exit_bar=50,
            result="TP",
        )
        bot.active_trades["BTCUSDT"] = trade

        current = _bar(50, 49000, 49200, 48800, 49000)
        asyncio.run(bot._exit_trade("BTCUSDT", trade, current, 50000))

        # PnL = (50000 - 49000) * 0.1 = 100
        assert bot._balance == 1100.0

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.RetradeEngine")
    @patch("bot.mark_trade_closed")
    def test_trade_appended_to_history(
        self,
        mock_mark_closed,
        mock_retrade_engine,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            exit_price=51000.0,
            exit_bar=50,
            result="TP",
        )
        bot.active_trades["BTCUSDT"] = trade

        current = _bar(50, 51000, 51200, 50800, 51000)
        asyncio.run(bot._exit_trade("BTCUSDT", trade, current, 50000))

        assert len(bot.trades) == 1
        assert bot.trades[0]["pnl"] == 100.0


# ═══════════════════════════════════════════════════════════════════
# _check_retrade tests
# ═══════════════════════════════════════════════════════════════════


class TestCheckRetrade:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_skips_when_not_armed(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        ss = bot.states["BTCUSDT"]
        ss.retrade_armed = False

        bars = [_bar(i, 100, 102, 98, 101) for i in range(20)]
        current = _bar(19, 100, 102, 98, 101)
        asyncio.run(bot._check_retrade("BTCUSDT", bars, current, 3.0, ss))
        # Should just return

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_skips_when_trades_today_not_1(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        ss = bot.states["BTCUSDT"]
        ss.retrade_armed = True
        ss.trades_today = 0

        bars = [_bar(i, 100, 102, 98, 101) for i in range(20)]
        current = _bar(19, 100, 102, 98, 101)
        asyncio.run(bot._check_retrade("BTCUSDT", bars, current, 3.0, ss))
        # Should skip

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_skips_when_active_trade_exists(
        self, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        ss = bot.states["BTCUSDT"]
        ss.retrade_armed = True
        ss.trades_today = 1
        bot.active_trades["BTCUSDT"] = ActiveTrade(side="long")

        bars = [_bar(i, 100, 102, 98, 101) for i in range(20)]
        current = _bar(19, 100, 102, 98, 101)
        asyncio.run(bot._check_retrade("BTCUSDT", bars, current, 3.0, ss))
        # Should skip


# ═══════════════════════════════════════════════════════════════════
# _warmup_cbdr tests
# ═══════════════════════════════════════════════════════════════════


class TestWarmupCbdr:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_noop_when_no_bars(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot.hub.get_bars.return_value = []
        bot._warmup_cbdr("BTCUSDT")
        # Should not raise

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_feeds_bars_to_session_state(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        ss = bot.states["BTCUSDT"]

        # Create bars with known timestamps
        dt_23 = datetime(2026, 6, 25, 23, 0, tzinfo=UTC)
        bars = []
        for i in range(20):
            ts = int(dt_23.timestamp() * 1000) + i * 900000
            bars.append(_bar(i, 100 + i, 102 + i, 98 + i, 101 + i, timestamp=ts))

        bot.hub.get_bars.return_value = bars
        bot._warmup_cbdr("BTCUSDT")
        # After warmup, cbdr should have tracked body
        assert ss.cbdr_body_high > 0.0


# ═══════════════════════════════════════════════════════════════════
# _prefill_bars tests
# ═══════════════════════════════════════════════════════════════════


class TestPrefillBars:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_prefill_success(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader, Result

        bot = PaperTrader(symbols=["BTCUSDT"])

        mock_rest = bot.rest
        mock_kline = [
            [
                1000000,
                "100",
                "105",
                "95",
                "102",
                "500",
                "999999",
                "250",
                "10",
                "1",
                "0",
            ],
        ]
        mock_rest.get = AsyncMock(return_value=Result.ok(mock_kline))

        result = asyncio.run(bot._prefill_bars("BTCUSDT", "15m"))
        assert result.is_ok
        bot.hub.prefill_bars.assert_called_once()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_prefill_failure(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader, Result

        bot = PaperTrader(symbols=["BTCUSDT"])

        mock_rest = bot.rest
        mock_rest.get = AsyncMock(return_value=Result.fail("Network error"))

        result = asyncio.run(bot._prefill_bars("BTCUSDT", "15m"))
        assert result.is_err


# ═══════════════════════════════════════════════════════════════════
# ActiveTrade dict compatibility
# ═══════════════════════════════════════════════════════════════════


class TestActiveTradeDict:
    def test_getitem(self):
        t = ActiveTrade(symbol="BTCUSDT", side="long", entry_price=50000.0)
        assert t["symbol"] == "BTCUSDT"
        assert t["side"] == "long"
        assert t["entry_price"] == 50000.0

    def test_setitem(self):
        t = ActiveTrade()
        t["symbol"] = "ETHUSDT"
        assert t.symbol == "ETHUSDT"

    def test_get_with_default(self):
        t = ActiveTrade()
        assert t.get("symbol", "default") == ""
        assert t.get("nonexistent", "fallback") == "fallback"

    def test_contains(self):
        t = ActiveTrade()
        assert "symbol" in t
        assert "nonexistent" not in t

    def test_keyerror_on_missing_attr(self):
        t = ActiveTrade()
        with pytest.raises(KeyError):
            _ = t["nonexistent_field"]


# ═══════════════════════════════════════════════════════════════════
# PendingLock tests
# ═══════════════════════════════════════════════════════════════════


class TestPendingLock:
    def test_enters_sets_pending(self):
        from models import PendingLock

        active = {}
        with PendingLock(active, "BTCUSDT") as _lock:
            assert "BTCUSDT" in active
            assert active["BTCUSDT"].status == "PENDING"
        # After context exit without commit, PENDING is removed
        assert "BTCUSDT" not in active

    def test_commit_preserves_pending(self):
        from models import PendingLock

        active = {}
        with PendingLock(active, "BTCUSDT") as _lock:
            _lock.commit()
        # After commit + context exit, PENDING stays
        assert "BTCUSDT" in active
        assert active["BTCUSDT"].status == "PENDING"

    def test_exception_cleans_up(self):
        from models import PendingLock

        active = {}
        try:
            with PendingLock(active, "BTCUSDT") as _lock:
                raise ValueError("test error")
        except ValueError:
            pass
        assert "BTCUSDT" not in active


# ── Helpers ───────────────────────────────────────────────────────


def _setup_minimal_cfg(mock_cfg, balance=1000.0, symbols=None):
    """Configure mock_cfg with minimal viable settings."""
    mock_cfg.SYMBOLS = symbols or ["BTCUSDT"]
    mock_cfg.IS_TESTNET = False
    mock_cfg.FVG_SIZE_MAP = {"BTCUSDT": 0.5}
    mock_cfg.SL_ATR_MULT = 1.5
    mock_cfg.TP_RR = 2.0
    mock_cfg.FVG_BUFFER_MULT = 0.3
    mock_cfg.INITIAL_BALANCE = balance
    mock_cfg.RISK_PER_TRADE = 0.01
    mock_cfg.BINANCE_API_KEY = ""
    mock_cfg.BINANCE_API_SECRET = ""
    mock_cfg.RETRADE_FVG_SIZE_MULT = 0.8
    mock_cfg.LEVERAGE = 10
    mock_cfg.SYMBOL_RISK_MAP = {}
    mock_cfg.RETRADE_FVG_MAX_ATTEMPTS = 3
    mock_cfg.CBDR_SWEEP_ATR_TOLERANCE_MULT = 0.3
    mock_cfg.CBDR_SWEEP_DEFAULT_TOLERANCE = 5.0
    mock_cfg.CBDR_DEAD_THRESHOLD_PCT = 0.5
    mock_cfg.ASIA_DEAD_THRESHOLD_PCT = 0.3
    mock_cfg.DEFAULT_ATR_FALLBACK_PCT = 0.005
