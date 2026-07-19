"""
test_bot.py — PaperTrader orchestrator unit tests.
Heavy mocking of external dependencies (WS, REST, config, trading).
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import Bar, ActiveTrade, STATUS_REPAIR_REQUIRED
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
        mock_cfg.LEVERAGE = 10
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
        assert bot._session_label(0) == "NEWYORK"
        assert bot._session_label(1) == "NEWYORK"
        assert bot._session_label(2) == "LONDON"
        assert bot._session_label(8) == "LONDON"
        assert bot._session_label(12) == "LONDON"
        assert bot._session_label(13) == "NEWYORK"
        assert bot._session_label(18) == "NEWYORK"
        assert bot._session_label(21) == "NEWYORK"
        assert bot._session_label(22) == "NEWYORK"
        assert bot._session_label(23) == "NEWYORK"


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
            )
        )
        assert rsm.state == RetraceState.IDLE  # Reset called

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_calculates_sl_tp_long(self, mock_cfg, mock_hub_cls, mock_rest_cls):
        _setup_minimal_cfg(mock_cfg)

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

        # Mock TrailingManager.evaluate_break_even
        mock_be = MagicMock()
        mock_be.updated = False
        mock_trail_mgr.evaluate_break_even.return_value = mock_be

        # Mock TrailingManager.check_exit
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.check_exit.return_value = mock_exit

        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        mock_trail_mgr.evaluate_trail.assert_called_once()
        mock_trail_mgr.check_exit.assert_called_once()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.TrailingManager")
    def test_orphan_check_counter_triggers_every_5_calls(
        self, mock_trail_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot.recovery_manager.reconcile_orphan_orders = AsyncMock()

        trade = ActiveTrade(
            symbol="BTCUSDT", side="long", entry_price=50000.0, sl=49000.0, tp=52000.0
        )
        bot.active_trades["BTCUSDT"] = trade
        bot.hub.get_bars.return_value = [_bar(i, 100, 105, 95, 102) for i in range(20)]

        mock_be = MagicMock()
        mock_be.updated = False
        mock_trail_mgr.evaluate_break_even.return_value = mock_be
        mock_tr = MagicMock()
        mock_tr.updated = False
        mock_trail_mgr.evaluate_trail.return_value = mock_tr
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.check_exit.return_value = mock_exit

        bars = [_bar(i, 50010, 50020, 49980, 50015) for i in range(5)]
        for _ in range(4):
            asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        bot.recovery_manager.reconcile_orphan_orders.assert_not_called()

        asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        bot.recovery_manager.reconcile_orphan_orders.assert_called_once()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.TrailingManager")
    def test_restricted_status_skips_trailing(
        self, mock_trail_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        from models import STATUS_REPAIR_REQUIRED
        from bot import PaperTrader

        _setup_minimal_cfg(mock_cfg)
        bot = PaperTrader(symbols=["BTCUSDT"])
        bot.recovery_manager.reconcile_orphan_orders = AsyncMock()

        trade = ActiveTrade(
            symbol="BTCUSDT", side="long", entry_price=50000.0, sl=49000.0, tp=52000.0
        )
        trade["status"] = STATUS_REPAIR_REQUIRED
        bot.active_trades["BTCUSDT"] = trade

        bars = [_bar(i, 50010, 50020, 49980, 50015) for i in range(5)]

        # We manually trigger orphan check just in case, but it should also skip
        bot._orphan_check_counter = 4
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))

        # Assert trailing and exits were completely skipped
        mock_trail_mgr.evaluate_trail.assert_not_called()
        mock_trail_mgr.check_exit.assert_not_called()
        # Assert orphan orders were not checked for this symbol (though it's a global check, the loop skips)
        # Actually our mock intercepts reconcile_orphan_orders entirely. Wait, the global check doesn't pass
        # arguments, it just skips the restricted symbol inside the method.
        # But wait, in _on_1m_close, if status not in UNRESTRICTED, does it call reconcile_orphan_orders?
        # Let's verify our code logic in _on_1m_close.
        bot.recovery_manager.reconcile_orphan_orders.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# _exit_trade tests
# ═══════════════════════════════════════════════════════════════════


class TestExitTrade:
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.mark_trade_closed")
    def test_pnl_calc_long(
        self,
        mock_mark_closed,
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
    @patch("bot.mark_trade_closed")
    def test_pnl_calc_short(
        self,
        mock_mark_closed,
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
    @patch("bot.mark_trade_closed")
    def test_trade_appended_to_history(
        self,
        mock_mark_closed,
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
# _exit_trade — pos_closed=False balance/peak revert (e6ef7fe)
# ═══════════════════════════════════════════════════════════════════


class TestExitTradePosNotClosed:
    """peak_equity rollback + balance revert when position fails to close."""

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    @patch("bot.mark_trade_closed")
    @patch("bot.log")
    def test_balance_reverted_and_peak_rolled_back(
        self,
        mock_log,
        mock_mark_closed,
        mock_entry_mgr,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.parse_market_fill.return_value = (0.1, 51000.0, None)

        with patch("bot.INITIAL_CAPITAL", 1000.0), patch("bot.RISK_PER_TRADE", 0.01):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])

        bot.reporter.emit = MagicMock()
        bot._available_balance = 1000.0

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            exit_price=51000.0,
            exit_bar=50,
            result="TRAIL",
            trailing_count=0,
        )
        bot.active_trades["BTCUSDT"] = trade

        bot.order_manager.cancel_all_open_orders = AsyncMock()
        bot.rest.place_market_order = AsyncMock(return_value={"orderId": 12345})
        bot.rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )

        initial_peak = 1000.0
        bot.risk_mgr.peak_equity = initial_peak

        def _update_peak(val):
            if val > bot.risk_mgr.peak_equity:
                bot.risk_mgr.peak_equity = val

        bot.risk_mgr.update_peak = _update_peak
        bot.risk_mgr._save_state = MagicMock()

        asyncio.run(bot._exit_trade("BTCUSDT", trade, 50))

        assert abs(bot._available_balance - 1000.0) < 1e-6
        assert abs(bot.risk_mgr.peak_equity - initial_peak) < 1e-6
        bot.risk_mgr._save_state.assert_called()
        assert "BTCUSDT" in bot.active_trades

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    @patch("bot.mark_trade_closed")
    @patch("bot.log")
    def test_peak_not_rolled_back_when_another_trade_updated_it(
        self,
        mock_log,
        mock_mark_closed,
        mock_entry_mgr,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.parse_market_fill.return_value = (0.1, 51000.0, None)

        with patch("bot.INITIAL_CAPITAL", 1000.0), patch("bot.RISK_PER_TRADE", 0.01):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])

        bot.reporter.emit = MagicMock()
        bot._available_balance = 1000.0

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            exit_price=51000.0,
            exit_bar=50,
            result="TRAIL",
            trailing_count=0,
        )
        bot.active_trades["BTCUSDT"] = trade

        bot.order_manager.cancel_all_open_orders = AsyncMock()
        bot.rest.place_market_order = AsyncMock(return_value={"orderId": 12345})
        bot.rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )

        higher_peak = 1200.0
        bot.risk_mgr.peak_equity = higher_peak

        def _update_peak(val):
            if val > bot.risk_mgr.peak_equity:
                bot.risk_mgr.peak_equity = val

        bot.risk_mgr.update_peak = _update_peak
        bot.risk_mgr._save_state = MagicMock()

        asyncio.run(bot._exit_trade("BTCUSDT", trade, 50))

        assert abs(bot._available_balance - 1000.0) < 1e-6
        assert abs(bot.risk_mgr.peak_equity - higher_peak) < 1e-6
        bot.risk_mgr._save_state.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# _exit_trade — dust closePosition fallback (06067c6)
# ═══════════════════════════════════════════════════════════════════


class TestExitTradeDustFallback:
    """When reduceOnly market fails (dust), closePosition fallback is used."""

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    @patch("bot.mark_trade_closed")
    @patch("bot.log")
    def test_force_close_called_on_market_failure(
        self,
        mock_log,
        mock_mark_closed,
        mock_entry_mgr,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.parse_market_fill.return_value = (0, 0, None)

        with patch("bot.INITIAL_CAPITAL", 1000.0), patch("bot.RISK_PER_TRADE", 0.01):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])

        bot.reporter.emit = MagicMock()
        bot._available_balance = 1000.0

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            exit_price=51000.0,
            exit_bar=50,
            result="TRAIL",
            trailing_count=0,
        )
        bot.active_trades["BTCUSDT"] = trade

        bot.order_manager.cancel_all_open_orders = AsyncMock()
        bot.rest.place_market_order = AsyncMock(return_value=None)
        bot.rest.place_force_close_order = AsyncMock(return_value=True)
        bot.rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )

        bot.risk_mgr.peak_equity = 1000.0
        bot.risk_mgr.update_peak = lambda v: None
        bot.risk_mgr._save_state = MagicMock()

        asyncio.run(bot._exit_trade("BTCUSDT", trade, 50))

        bot.rest.place_force_close_order.assert_called_once_with(
            "BTCUSDT", "SELL", "long"
        )


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


# ═══════════════════════════════════════════════════════════════════
# _exit_trade — A10 adapter ambiguity tests
# ═══════════════════════════════════════════════════════════════════


class TestExitTradeAdapterAmbiguity:
    """A10: adapter belirsizliği explicit ambiguous state yaratacak.

    Boş/kimliksiz response → commit yapılmamalı, trade ACTIVE'e dönmemeli.
    """

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    @patch("bot.mark_trade_closed")
    @patch("bot.log")
    def test_empty_response_no_commit(
        self,
        mock_log,
        mock_mark_closed,
        mock_entry_mgr,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        """place_market_order {} (boş) dönerse → no commit, trade active kalır."""
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.parse_market_fill.return_value = (0, 0, None)

        with patch("bot.INITIAL_CAPITAL", 1000.0), patch("bot.RISK_PER_TRADE", 0.01):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])

        bot.reporter.emit = MagicMock()
        bot._available_balance = 1000.0

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            exit_price=51000.0,
            exit_bar=50,
            result="TRAIL_CLOSE",
            trailing_count=0,
        )
        bot.active_trades["BTCUSDT"] = trade

        # Adapter boş dict dönüyor — _status alanı yok
        bot.order_manager.cancel_all_open_orders = AsyncMock()
        bot.rest.place_market_order = AsyncMock(return_value={})
        bot.rest.place_force_close_order = AsyncMock(return_value=True)
        bot.rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )
        bot.order_manager.verify_protection = AsyncMock(return_value=(True, True))
        bot.order_manager.repair_protection = AsyncMock()

        bot.risk_mgr.peak_equity = 1000.0
        bot.risk_mgr.update_peak = lambda v: None
        bot.risk_mgr._save_state = MagicMock()

        asyncio.run(bot._exit_trade("BTCUSDT", trade, 50))

        # Trade commit edilmemiş olmalı — active_trades'te kalmalı
        assert "BTCUSDT" in bot.active_trades
        assert bot.active_trades["BTCUSDT"]["status"] == STATUS_REPAIR_REQUIRED
        assert bot._available_balance == 1000.0  # balance değişmemeli
        mock_mark_closed.assert_not_called()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    @patch("bot.mark_trade_closed")
    @patch("bot.log")
    def test_order_acknowledged_position_open_no_commit(
        self,
        mock_log,
        mock_mark_closed,
        mock_entry_mgr,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        """ORDER_ACKNOWLEDGED response + pozisyon açık → no commit."""
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.parse_market_fill.return_value = (0, 0, None)

        with patch("bot.INITIAL_CAPITAL", 1000.0), patch("bot.RISK_PER_TRADE", 0.01):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])

        bot.reporter.emit = MagicMock()
        bot._available_balance = 1000.0

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            exit_price=51000.0,
            exit_bar=50,
            result="TRAIL_CLOSE",
            trailing_count=0,
        )
        bot.active_trades["BTCUSDT"] = trade

        # Adapter ORDER_ACKNOWLEDGED dönüyor — orderId yok
        bot.order_manager.cancel_all_open_orders = AsyncMock()
        bot.rest.place_market_order = AsyncMock(
            return_value={"_status": "ORDER_ACKNOWLEDGED", "status": "NEW"}
        )
        bot.rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )
        bot.order_manager.verify_protection = AsyncMock(return_value=(True, True))
        bot.order_manager.repair_protection = AsyncMock()

        bot.risk_mgr.peak_equity = 1000.0
        bot.risk_mgr.update_peak = lambda v: None
        bot.risk_mgr._save_state = MagicMock()

        asyncio.run(bot._exit_trade("BTCUSDT", trade, 50))

        # Trade commit edilmemiş olmalı — active_trades'te REPAIR_REQUIRED
        assert "BTCUSDT" in bot.active_trades
        assert bot.active_trades["BTCUSDT"]["status"] == STATUS_REPAIR_REQUIRED
        assert bot._available_balance == 1000.0
        mock_mark_closed.assert_not_called()


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
    mock_cfg.LEVERAGE = 10
    mock_cfg.CBDR_SWEEP_ATR_TOLERANCE_MULT = 0.3
    mock_cfg.CBDR_SWEEP_DEFAULT_TOLERANCE = 5.0
    mock_cfg.CBDR_DEAD_THRESHOLD_PCT = 0.5
    mock_cfg.ASIA_DEAD_THRESHOLD_PCT = 0.3
    mock_cfg.DEFAULT_ATR_FALLBACK_PCT = 0.005
