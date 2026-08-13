"""
test_bot.py — PaperTrader orchestrator unit tests.
Heavy mocking of external dependencies (WS, REST, config, trading).
"""

import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import (
    Bar,
    ActiveTrade,
    STATUS_ACTIVE,
    STATUS_TRAIL_REPLACING,
    STATUS_REPAIR_REQUIRED,
)
from retrace_state import RetraceState, HTFFVG
from session import DailyBias, SessionState


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


# ═══════════════════════════════════════════════════════════════════
# _exit_trade — wiring tests for EXIT_LIFECYCLE_SERVICE_ENABLED
# ═══════════════════════════════════════════════════════════════════


class TestExitTradeWiring:
    """Verify _exit_trade routing based on EXIT_LIFECYCLE_SERVICE_ENABLED."""

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.mark_trade_closed")
    def test_flag_true_delegates_to_exit_service(
        self,
        mock_mark_closed,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        """EXIT_LIFECYCLE_SERVICE_ENABLED=True → exit_service.execute() called."""
        _setup_minimal_cfg(mock_cfg)
        with (
            patch("bot.INITIAL_CAPITAL", 1000.0),
            patch("bot.RISK_PER_TRADE", 0.01),
            patch("bot.EXIT_LIFECYCLE_SERVICE_ENABLED", True),
        ):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])
            bot.exit_service.execute = AsyncMock(return_value=True)

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

            asyncio.run(bot._exit_trade("BTCUSDT", trade, 50000))

            bot.exit_service.execute.assert_awaited_once_with("BTCUSDT", trade, 50000)

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.mark_trade_closed")
    def test_flag_false_calls_legacy(
        self,
        mock_mark_closed,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        """EXIT_LIFECYCLE_SERVICE_ENABLED=False → _exit_trade_legacy called."""
        _setup_minimal_cfg(mock_cfg)
        with (
            patch("bot.INITIAL_CAPITAL", 1000.0),
            patch("bot.RISK_PER_TRADE", 0.01),
            patch("bot.EXIT_LIFECYCLE_SERVICE_ENABLED", False),
        ):
            from bot import PaperTrader

            bot = PaperTrader(symbols=["BTCUSDT"])
            bot._exit_trade_legacy = AsyncMock(return_value=None)
            bot.exit_service.execute = AsyncMock()

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

            asyncio.run(bot._exit_trade("BTCUSDT", trade, 50000))

            bot._exit_trade_legacy.assert_awaited_once_with("BTCUSDT", trade, 50000)
            bot.exit_service.execute.assert_not_called()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.mark_trade_closed")
    def test_flag_true_by_default(
        self,
        mock_mark_closed,
        mock_cfg,
        mock_hub_cls,
        mock_rest_cls,
    ):
        """Sprint rollout: flag varsayilan True → ExitLifecycleService aktif."""
        import bot as bot_module

        assert bot_module.EXIT_LIFECYCLE_SERVICE_ENABLED is True
        assert bot_module.PROTECTION_LIFECYCLE_SERVICE_ENABLED is True


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
        # entry_timestamp fill anına yakın olmalı (snapshot bar tespiti için)
        assert trade.entry_timestamp > 0
        assert abs(int(time.time() * 1000) - trade.entry_timestamp) < 10_000

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
        mock_entry_mgr.validate_pre_entry_protection.return_value = (True, "")

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

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.log")
    @patch("bot.EntryManager")
    def test_pre_entry_sl_eps_guard_rejects_before_order(
        self, mock_entry_mgr, mock_log, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """PRE-ENTRY guard regresyon: SL/TP eps yakınsa sinyal pre-entry
        reddedilir — MARKET emri gönderilmez, pozisyon hiç açılmaz, acil
        kapanma trafiği (fee/slippage) oluşmaz. Genel kural (validate_pre_entry_protection)
        SEI/ENA dahil tüm semboller için tek noktada uygulanır."""
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.calculate_sl_tp.return_value = (100.0, 118.0)
        mock_entry_mgr.calculate_qty.return_value = 1.0
        mock_entry_mgr.validate_risk.return_value = (True, "")
        mock_entry_mgr.validate_pre_entry_protection.return_value = (
            False,
            "SL=100.0 >= actual_fill=109.0 - eps=0.0002",
        )

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot.rest.get_tick_size = AsyncMock(return_value=0.0001)
        bot._live = True
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

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
        assert "BTCUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.bias_locked is True
        assert rsm.locked_direction == "bullish"
        assert ss.sweep_confirmed is False
        mock_entry_mgr.validate_pre_entry_protection.assert_called_once()
        mock_entry_mgr.return_value.execute_live_entry.assert_not_called()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    def test_risk_reject_locks_bias(
        self, mock_entry_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Grup 2 (Sonnet direktifi): risk dogrulama hatasi o spesifik FVG'nin
        geometrisiyle ilgili — reset yerine lock_bias() -> bias kilitli kalir,
        sonraki taze FVG denenir."""
        _setup_minimal_cfg(mock_cfg)
        mock_entry_mgr.calculate_sl_tp.return_value = (100.0, 118.0)
        mock_entry_mgr.calculate_qty.return_value = 1.0
        mock_entry_mgr.validate_risk.return_value = (
            False,
            "risk_dist min_risk_dist altinda",
        )
        mock_entry_mgr.validate_pre_entry_protection.return_value = (True, "")

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

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
        assert "BTCUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.locked_direction == "bullish"
        assert rsm._locked_from_bar == current.index
        mock_entry_mgr.return_value.execute_live_entry.assert_not_called()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_pre_entry_fvg_clearance_guard_rejects_ena(
        self, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """ENAUSDT regresyon (08-06 18:00 direction-fail): FVG üst sınırı ile
        SL arası mesafe eps'in (2 tick) altında → sinyal pre-entry reddedilir.
        tick=0.001, eps=0.002; SL=FVG.top+0.0018 → 0.0018 < 0.002. Eski guard
        (entry-eps) bunu yakalayamazdı: SL entry'ye 4+ tick uzakta. Genel kural
        FVG sınırı bazlı kontrolle yakalar — MARKET emri gönderilmez."""
        _setup_minimal_cfg(mock_cfg)

        from bot import PaperTrader

        bot = PaperTrader(symbols=["ENAUSDT"])
        bot.rest.get_tick_size = AsyncMock(return_value=0.001)
        bot._live = True
        bot.entry_manager.execute_live_entry = AsyncMock()
        rsm = bot.rsms["ENAUSDT"]
        ss = bot.states["ENAUSDT"]

        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bearish"
        rsm.trigger_fvg = HTFFVG(
            top=0.600, bottom=0.590, direction="bearish", bar_index=5
        )
        ss.london_high = 0.650
        ss.london_low = 0.570

        current = _bar(20, 0.596, 0.600, 0.590, 0.592)
        asyncio.run(
            bot._try_entry(
                sym="ENAUSDT",
                current=current,
                atr_val=0.004,
                rsm=rsm,
                ss=ss,
                sweep_dir="bearish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
            )
        )
        assert "ENAUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.bias_locked is True
        assert ss.sweep_confirmed is False
        bot.entry_manager.execute_live_entry.assert_not_awaited()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_pre_entry_fvg_clearance_guard_rejects_sei(
        self, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """SEIUSDT regresyon (08-06 direction-fail döngüsü): tick=0.0001,
        eps=0.0002. apply_min_sl_distance SL'yi entry'den 4 tick uzağa (0.0415)
        iter ama SL FVG.top'a (0.0414) sadece 1 tick uzakta → genel kural reddeder."""
        _setup_minimal_cfg(mock_cfg)

        from bot import PaperTrader

        bot = PaperTrader(symbols=["SEIUSDT"])
        bot.rest.get_tick_size = AsyncMock(return_value=0.0001)
        bot._live = True
        bot.entry_manager.execute_live_entry = AsyncMock()
        rsm = bot.rsms["SEIUSDT"]
        ss = bot.states["SEIUSDT"]

        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bearish"
        rsm.trigger_fvg = HTFFVG(
            top=0.0414, bottom=0.0410, direction="bearish", bar_index=5
        )
        ss.london_high = 0.0450
        ss.london_low = 0.0390

        current = _bar(20, 0.0415, 0.0416, 0.0408, 0.0411)
        asyncio.run(
            bot._try_entry(
                sym="SEIUSDT",
                current=current,
                atr_val=0.0001,
                rsm=rsm,
                ss=ss,
                sweep_dir="bearish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
            )
        )
        assert "SEIUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.bias_locked is True
        assert ss.sweep_confirmed is False
        bot.entry_manager.execute_live_entry.assert_not_awaited()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_pre_entry_fvg_clearance_guard_rejects_ena_long(
        self, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Long taraf: SL, FVG.bottom'a eps'ten yakın (0.0018 < 0.002) → red."""
        _setup_minimal_cfg(mock_cfg)

        from bot import PaperTrader

        bot = PaperTrader(symbols=["ENAUSDT"])
        bot.rest.get_tick_size = AsyncMock(return_value=0.001)
        bot._live = True
        bot.entry_manager.execute_live_entry = AsyncMock()
        rsm = bot.rsms["ENAUSDT"]
        ss = bot.states["ENAUSDT"]

        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bullish"
        rsm.trigger_fvg = HTFFVG(
            top=0.600, bottom=0.590, direction="bullish", bar_index=5
        )
        ss.london_high = 0.650
        ss.london_low = 0.570

        current = _bar(20, 0.594, 0.600, 0.592, 0.598)
        asyncio.run(
            bot._try_entry(
                sym="ENAUSDT",
                current=current,
                atr_val=0.004,
                rsm=rsm,
                ss=ss,
                sweep_dir="bullish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
            )
        )
        assert "ENAUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.bias_locked is True
        assert ss.sweep_confirmed is False
        bot.entry_manager.execute_live_entry.assert_not_awaited()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_pre_entry_fvg_clearance_guard_passes_when_buffer_ok(
        self, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Genel kural fazla reddetmez: FVG buffer'ı eps'in üzerindeyse (2.5 tick
        ≥ 2 tick) aynı sembolde giriş normal şekilde açılır."""
        _setup_minimal_cfg(mock_cfg)

        from bot import PaperTrader

        bot = PaperTrader(symbols=["ENAUSDT"])
        bot.rest.get_tick_size = AsyncMock(return_value=0.001)
        rsm = bot.rsms["ENAUSDT"]
        ss = bot.states["ENAUSDT"]

        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bearish"
        rsm.trigger_fvg = HTFFVG(
            top=0.600, bottom=0.590, direction="bearish", bar_index=5
        )
        ss.london_high = 0.650
        ss.london_low = 0.570

        current = _bar(20, 0.596, 0.600, 0.590, 0.592)
        asyncio.run(
            bot._try_entry(
                sym="ENAUSDT",
                current=current,
                atr_val=0.006,
                rsm=rsm,
                ss=ss,
                sweep_dir="bearish",
                sl_atr=1.5,
                tp_rr=2.0,
                fvg_buf=0.3,
                min_fvg=0.5,
            )
        )
        assert "ENAUSDT" in bot.active_trades
        trade = bot.active_trades["ENAUSDT"]
        assert trade.side == "short"
        assert trade.entry_price == 0.592

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    def test_live_sl_fail_emergency_close_trade_not_recorded(
        self, mock_entry_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """BUG-1 regresyon: execute_live_entry success=False dondururse bot
        trade KAYDETMEZ; Grup 3 (Sonnet direktifi) geregi ilk order hatasi
        BIAS_LOCKED'a gecirir (ardisik 3 hata -> full reset)."""
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.calculate_sl_tp.return_value = (100.0, 118.0)
        mock_entry_mgr.calculate_qty.return_value = 1.0
        mock_entry_mgr.validate_risk.return_value = (True, "")
        mock_entry_mgr.validate_pre_entry_protection.return_value = (True, "")
        exec_result = AsyncMock()
        exec_result.return_value = SimpleNamespace(
            success=False,
            error="SL FAIL code=0 — pozisyon guvenle kapatildi",
            sl_order_id="",
            tp_order_id="",
            qty=0.0,
            actual_qty=0.0,
            actual_price=0.0,
            order_id="",
            entry_log_msg="",
        )
        mock_entry_mgr.return_value.execute_live_entry = exec_result

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot._live = True
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

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
        # Trade kaydedilmemeli (emergency close sonrasi pozisyon yok)
        assert "BTCUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.BIAS_LOCKED

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    def test_active_trade_skip_full_reset(
        self, mock_entry_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Grup 1 (Sonnet direktifi): aktif trade varken entry denenmez,
        RSM full reset kalir (hesap/oturum seviyesi)."""
        _setup_minimal_cfg(mock_cfg)

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]
        bot.active_trades["BTCUSDT"] = SimpleNamespace(
            side="long", entry_price=100.0, entry_time=0
        )

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
        assert "BTCUSDT" in bot.active_trades  # mevcut trade korunur
        assert rsm.state == RetraceState.IDLE  # full reset
        mock_entry_mgr.return_value.execute_live_entry.assert_not_called()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    @patch("bot.get_cbdr_multiplier", return_value=0.0)
    def test_toxic_zone_full_reset(
        self, mock_mult, mock_entry_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Grup 1 (Sonnet direktifi): Zehirli Bolge (cbdr_mult=0.0) sistemik —
        RSM full reset kalir."""
        _setup_minimal_cfg(mock_cfg)
        mock_entry_mgr.calculate_sl_tp.return_value = (100.0, 118.0)
        mock_entry_mgr.calculate_qty.return_value = 1.0
        mock_entry_mgr.validate_risk.return_value = (True, "")
        mock_entry_mgr.validate_pre_entry_protection.return_value = (True, "")

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]
        ss.cbdr_body_high = 110.0
        ss.cbdr_body_low = 100.0

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
        assert "BTCUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.IDLE
        mock_entry_mgr.return_value.execute_live_entry.assert_not_called()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    def test_fill_error_locks_bias(
        self, mock_entry_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Grup 3 (Sonnet direktifi): gecersiz fill (qty/price <= 0) ->
        lock_bias, IDLE'a donus yok."""
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.calculate_sl_tp.return_value = (100.0, 118.0)
        mock_entry_mgr.calculate_qty.return_value = 1.0
        mock_entry_mgr.validate_risk.return_value = (True, "")
        mock_entry_mgr.validate_pre_entry_protection.return_value = (True, "")
        exec_result = AsyncMock()
        exec_result.return_value = SimpleNamespace(
            success=True,
            error="",
            sl_order_id="sl1",
            tp_order_id="tp1",
            qty=0.0,
            actual_qty=0.0,
            actual_price=0.0,
            order_id="o1",
            entry_log_msg="",
        )
        mock_entry_mgr.return_value.execute_live_entry = exec_result

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot._live = True
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

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
        assert "BTCUSDT" not in bot.active_trades
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm._fail_count == 1

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.EntryManager")
    def test_three_consecutive_order_fails_full_reset(
        self, mock_entry_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Grup 3 sarti (Sonnet direktifi): ayni sembolde art arda 3 order
        hatasi -> lock_bias yerine full reset (IDLE) — sinirsiz tekrar dene
        dongusu riski kapanir."""
        _setup_minimal_cfg(mock_cfg)
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_entry_mgr.calculate_sl_tp.return_value = (100.0, 118.0)
        mock_entry_mgr.calculate_qty.return_value = 1.0
        mock_entry_mgr.validate_risk.return_value = (True, "")
        mock_entry_mgr.validate_pre_entry_protection.return_value = (True, "")
        exec_result = AsyncMock()
        exec_result.return_value = SimpleNamespace(
            success=False,
            error="API kesintisi",
            sl_order_id="",
            tp_order_id="",
            qty=0.0,
            actual_qty=0.0,
            actual_price=0.0,
            order_id="",
            entry_log_msg="",
        )
        mock_entry_mgr.return_value.execute_live_entry = exec_result

        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot._live = True
        rsm = bot.rsms["BTCUSDT"]
        ss = bot.states["BTCUSDT"]

        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bullish"
        ss.london_high = 110.0
        ss.london_low = 95.0

        states = []
        for idx in (20, 21, 22):
            current = _bar(idx, 108, 110, 106, 109)
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
            states.append(rsm.state)

        assert states[0] == RetraceState.BIAS_LOCKED  # 1. hata -> lock
        assert states[1] == RetraceState.BIAS_LOCKED  # 2. hata -> lock
        assert states[2] == RetraceState.IDLE  # 3. hata -> full reset
        assert "BTCUSDT" not in bot.active_trades
        assert rsm._fail_count == 0
        assert mock_entry_mgr.return_value.execute_live_entry.call_count == 3


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

        # Mock TrailingManager.orchestrate_trail (awaited)
        mock_result = MagicMock()
        mock_result.action = "none"
        mock_trail_mgr.return_value.orchestrate_trail = AsyncMock(
            return_value=mock_result
        )

        # Mock TrailingManager.check_exit
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.return_value.check_exit.return_value = mock_exit

        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        mock_trail_mgr.return_value.orchestrate_trail.assert_awaited_once()
        mock_trail_mgr.return_value.check_exit.assert_called_once()

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

        mock_tr = MagicMock()
        mock_tr.action = "none"
        mock_trail_mgr.return_value.orchestrate_trail = AsyncMock(return_value=mock_tr)
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.return_value.check_exit.return_value = mock_exit

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
    def test_rebuilds_fvg_extractor_when_missing(
        self, mock_trail_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Restart sonrasi recover edilen trade (extractor yok) FVG extractor alir."""
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])

        trade = ActiveTrade(
            symbol="BTCUSDT", side="long", entry_price=50000.0, sl=49000.0, tp=52000.0
        )
        assert not callable(trade.get("trail_level_extractor"))
        bot.active_trades["BTCUSDT"] = trade
        bot.hub.get_bars.return_value = [_bar(i, 100, 105, 95, 102) for i in range(20)]

        mock_tr = MagicMock()
        mock_tr.action = "none"
        mock_trail_mgr.return_value.orchestrate_trail = AsyncMock(return_value=mock_tr)
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.return_value.check_exit.return_value = mock_exit

        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        assert callable(trade.get("trail_level_extractor"))

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
        # Patch Set 5: orphan sweep artık symbol status'ten bağımsız
        # çalışır (tüm sembolleri tarar). reconcile_orphan_orders
        # çağrılır ama içeride transition guard skip eder.
        bot.recovery_manager.reconcile_orphan_orders.assert_called_once()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.TrailingManager")
    def test_orchestrate_trail_exception_sets_status_active_and_continues(
        self, mock_trail_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Fix B: orchestrate_trail exception firlatirsa status ACTIVE'e
        zorla geri cekilmeli, check_exit() yine de calismali."""
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot.recovery_manager.reconcile_orphan_orders = AsyncMock()

        trade = ActiveTrade(
            symbol="BTCUSDT", side="long", entry_price=50000.0, sl=49000.0, tp=52000.0
        )
        trade["status"] = STATUS_ACTIVE
        bot.active_trades["BTCUSDT"] = trade
        bot.hub.get_bars.return_value = [_bar(i, 100, 105, 95, 102) for i in range(20)]

        mock_trail_mgr.return_value.orchestrate_trail = AsyncMock(
            side_effect=RuntimeError("trail boom")
        )
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.return_value.check_exit.return_value = mock_exit

        bars = [_bar(i, 50010, 50020, 49980, 50015) for i in range(5)]
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))

        assert trade["status"] == STATUS_ACTIVE
        mock_trail_mgr.return_value.orchestrate_trail.assert_awaited_once()
        mock_trail_mgr.return_value.check_exit.assert_called_once()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    @patch("bot.TrailingManager")
    def test_watchdog_resets_stuck_trail_replacing_after_90s(
        self, mock_trail_mgr, mock_cfg, mock_hub_cls, mock_rest_cls
    ):
        """Fix C: TRAIL_REPLACING statusu 90s'den uzun surduyse watchdog
        ACTIVE'e zorla geri cekmeli."""
        _setup_minimal_cfg(mock_cfg)
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot.recovery_manager.reconcile_orphan_orders = AsyncMock()

        trade = ActiveTrade(
            symbol="BTCUSDT", side="long", entry_price=50000.0, sl=49000.0, tp=52000.0
        )
        trade["status"] = STATUS_TRAIL_REPLACING
        trade["status_since"] = time.time() - 100
        bot.active_trades["BTCUSDT"] = trade
        bot.hub.get_bars.return_value = [_bar(i, 100, 105, 95, 102) for i in range(20)]

        mock_tr = MagicMock()
        mock_tr.action = "none"
        mock_trail_mgr.return_value.orchestrate_trail = AsyncMock(return_value=mock_tr)
        mock_exit = MagicMock()
        mock_exit.triggered = False
        mock_trail_mgr.return_value.check_exit.return_value = mock_exit

        bars = [_bar(i, 50010, 50020, 49980, 50015) for i in range(5)]
        for _ in range(4):
            asyncio.run(bot._on_1m_close("BTCUSDT", bars))
        asyncio.run(bot._on_1m_close("BTCUSDT", bars))

        assert trade["status"] == STATUS_ACTIVE


# ═══════════════════════════════════════════════════════════════
# _exit_trade tests
# ═══════════════════════════════════════════════════════════════


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

    def test_pending_placeholder_has_symbol(self):
        from models import PendingLock

        active = {}
        with PendingLock(active, "BTCUSDT") as _lock:
            assert active["BTCUSDT"].symbol == "BTCUSDT"

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
    mock_cfg.SL_EPSILON_TICKS = 2


# ═══════════════════════════════════════════════════════════════════
# Rapor 4 — BIAS latch restart restore (_restore_bias_latch)
# ═══════════════════════════════════════════════════════════════════


class TestRestoreBiasLatch:
    """_restore_bias_latch: disk'teki gunluk BIAS latch'ini SessionState._cbdr
    + RSM'e yukler; latch yoksa/hataysa IDLE kalir, bayrak set edilir."""

    def _make_trader(self, mock_cfg):
        _setup_minimal_cfg(mock_cfg)
        import bot as bot_module

        return bot_module.PaperTrader()

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_restore_writes_cbdr_and_rsm(self, mock_cfg, mock_hub, mock_rest):
        trader = self._make_trader(mock_cfg)
        sym = "BTCUSDT"
        ss = trader.states[sym]
        ss._cbdr.day = "2026-06-19"
        with patch(
            "state_manager.load_bias_lock",
            return_value={
                "daily_bias": "BULLISH",
                "sweep_direction": "bullish",
                "sweep_level": 65550.0,
                "bias_lock_day": "2026-06-19",
                "bias_lock_bar_index": 42,
            },
        ):
            trader._restore_bias_latch(sym, ss, fallback_bar=99)
        assert ss.bias_locked is True
        assert ss.daily_bias == DailyBias.BULLISH
        assert ss.sweep_direction == "bullish"
        assert ss.sweep_level == 65550.0
        rsm = trader.rsms[sym]
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.locked_direction == "bullish"
        assert trader._bias_latch_restored[sym] is True

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_restore_none_keeps_idle(self, mock_cfg, mock_hub, mock_rest):
        trader = self._make_trader(mock_cfg)
        sym = "BTCUSDT"
        ss = trader.states[sym]
        ss._cbdr.day = "2026-06-19"
        with patch("state_manager.load_bias_lock", return_value=None):
            trader._restore_bias_latch(sym, ss, fallback_bar=99)
        assert ss.bias_locked is False
        assert trader.rsms[sym].state == RetraceState.IDLE
        assert trader._bias_latch_restored[sym] is True

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_restore_error_keeps_idle_and_sets_flag(
        self, mock_cfg, mock_hub, mock_rest
    ):
        trader = self._make_trader(mock_cfg)
        sym = "BTCUSDT"
        ss = trader.states[sym]
        ss._cbdr.day = "2026-06-19"
        with patch("state_manager.load_bias_lock", side_effect=Exception("disk error")):
            trader._restore_bias_latch(sym, ss, fallback_bar=99)
        assert trader.rsms[sym].state == RetraceState.IDLE
        assert ss.bias_locked is False
        assert trader._bias_latch_restored[sym] is True

    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    @patch("bot.cfg", autospec=True)
    def test_restore_uses_fallback_bar_when_not_persisted(
        self, mock_cfg, mock_hub, mock_rest
    ):
        trader = self._make_trader(mock_cfg)
        sym = "BTCUSDT"
        ss = trader.states[sym]
        ss._cbdr.day = "2026-06-19"
        with patch(
            "state_manager.load_bias_lock",
            return_value={
                "daily_bias": "BEARISH",
                "sweep_direction": "bearish",
                "sweep_level": 64000.0,
                "bias_lock_day": "2026-06-19",
                "bias_lock_bar_index": None,
            },
        ):
            trader._restore_bias_latch(sym, ss, fallback_bar=77)
        rsm = trader.rsms[sym]
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.locked_direction == "bearish"
        assert rsm._locked_from_bar == 77
