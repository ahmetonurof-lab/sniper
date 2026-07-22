"""
test_user_data_handler.py — UserDataHandler WS normalization tests (Patch Set 4).

normalize_order_event(), ID helpers, ve normalized/legacy handler behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.user_data_handler import (
    normalize_order_event,
    _collect_trade_order_ids,
    _oid_matches_trade,
    _resolve_fill_result,
)
from models import ActiveTrade


def _trade(**kw):
    base = dict(
        symbol="BTCUSDT",
        side="long",
        entry_price=50000.0,
        sl=49000.0,
        tp=52000.0,
        qty=0.1,
        sl_order_id="SL_1",
        tp_order_id="TP_1",
        sl_order_id_prev="",
        tp_order_id_prev="",
        sl_order_id_history=[],
        tp_order_id_history=[],
        pending_sl_order_id="",
        pending_tp_order_id="",
        status="ACTIVE",
    )
    base.update(kw)
    t = ActiveTrade(
        symbol=base["symbol"],
        side=base["side"],
        entry_price=base["entry_price"],
        sl=base["sl"],
        tp=base["tp"],
        qty=base["qty"],
        status=base.get("status", ""),
    )
    for k in (
        "sl_order_id",
        "tp_order_id",
        "sl_order_id_prev",
        "tp_order_id_prev",
        "pending_sl_order_id",
        "pending_tp_order_id",
    ):
        t[k] = base.get(k, "")
    t["sl_order_id_history"] = base.get("sl_order_id_history", [])
    t["tp_order_id_history"] = base.get("tp_order_id_history", [])
    return t


def _raw_order(**kw):
    base = {
        "s": "BTCUSDT",
        "c": "order_123",
        "i": 12345,
        "X": "FILLED",
        "R": True,
        "ap": "51000",
        "L": "50950",
        "z": "0.1",
        "Z": "5100",
    }
    base.update(kw)
    return base


# ═══════════════════════════════════════════════════════════════════
# normalize_order_event() tests
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeOrderEvent:
    def test_full_filled_event(self):
        raw = _raw_order()
        evt = normalize_order_event(raw)
        assert evt is not None
        assert evt.symbol == "BTCUSDT"
        assert evt.order_id == "order_123"
        assert evt.status == "FILLED"
        assert evt.reduce_only is True
        assert evt.avg_price == 51000.0
        assert evt.last_price == 50950.0
        assert evt.fill_price == 51000.0
        assert evt.cum_qty == 0.1
        assert evt.cum_quote_qty == 5100.0

    def test_fill_price_fallback_to_last(self):
        raw = _raw_order(ap="0", L="50950")
        evt = normalize_order_event(raw)
        assert evt.fill_price == 50950.0

    def test_missing_symbol_returns_none(self):
        evt = normalize_order_event({"X": "FILLED"})
        assert evt is None

    def test_empty_ap_and_L(self):
        raw = _raw_order(ap="", L="")
        evt = normalize_order_event(raw)
        assert evt.fill_price == 0.0

    def test_order_id_falls_back_to_i(self):
        raw = _raw_order(c="", i=99999)
        evt = normalize_order_event(raw)
        assert evt.order_id == "99999"

    def test_reduce_only_false(self):
        raw = _raw_order(R=False, reduceOnly=False)
        evt = normalize_order_event(raw)
        assert evt.reduce_only is False

    def test_reduce_only_true_via_R(self):
        raw = _raw_order(R=True, reduceOnly=False)
        evt = normalize_order_event(raw)
        assert evt.reduce_only is True

    def test_status_new(self):
        raw = _raw_order(X="NEW")
        evt = normalize_order_event(raw)
        assert evt.status == "NEW"

    def test_ts_ms_set(self):
        raw = _raw_order()
        evt = normalize_order_event(raw)
        assert evt.ts_ms is not None

    def test_raw_stored(self):
        raw = _raw_order()
        evt = normalize_order_event(raw)
        assert evt.raw is raw


# ═══════════════════════════════════════════════════════════════════
# _collect_trade_order_ids() tests
# ═══════════════════════════════════════════════════════════════════


class TestCollectTradeOrderIds:
    def test_current_ids(self):
        t = _trade(sl_order_id="SL_CUR", tp_order_id="TP_CUR")
        s, tp, sp, tp_p, sh, th = _collect_trade_order_ids(t)
        assert s == "SL_CUR"
        assert tp == "TP_CUR"

    def test_prev_ids(self):
        t = _trade(sl_order_id_prev="SL_OLD", tp_order_id_prev="TP_OLD")
        s, tp, sp, tp_p, sh, th = _collect_trade_order_ids(t)
        assert sp == "SL_OLD"
        assert tp_p == "TP_OLD"

    def test_history_ids(self):
        t = _trade(
            sl_order_id_history=["SL_H1", "SL_H2"],
            tp_order_id_history=["TP_H1"],
        )
        s, tp, sp, tp_p, sh, th = _collect_trade_order_ids(t)
        assert sh == ["SL_H1", "SL_H2"]
        assert th == ["TP_H1"]

    def test_all_str_type(self):
        t = _trade(sl_order_id=123, tp_order_id=456)
        s, tp, sp, tp_p, sh, th = _collect_trade_order_ids(t)
        assert isinstance(s, str)
        assert s == "123"


# ═══════════════════════════════════════════════════════════════════
# _oid_matches_trade() tests
# ═══════════════════════════════════════════════════════════════════


class TestOidMatchesTrade:
    def test_matches_current_sl(self):
        assert _oid_matches_trade("SL_1", "SL_1", "TP_1", "", "", [], []) is True

    def test_matches_current_tp(self):
        assert _oid_matches_trade("TP_1", "SL_1", "TP_1", "", "", [], []) is True

    def test_matches_prev_sl(self):
        assert (
            _oid_matches_trade("SL_OLD", "SL_CUR", "TP_CUR", "SL_OLD", "", [], [])
            is True
        )

    def test_matches_history(self):
        assert (
            _oid_matches_trade("SL_H1", "SL_CUR", "TP_CUR", "", "", ["SL_H1"], [])
            is True
        )

    def test_no_match(self):
        assert _oid_matches_trade("UNKNOWN", "SL_1", "TP_1", "", "", [], []) is False

    def test_empty_oid(self):
        assert _oid_matches_trade("", "SL_1", "TP_1", "", "", [], []) is False


# ═══════════════════════════════════════════════════════════════════
# _resolve_fill_result() tests
# ═══════════════════════════════════════════════════════════════════


class TestResolveFillResult:
    def test_current_sl_is_sl(self):
        assert _resolve_fill_result("SL_1", "SL_1", "TP_1", "", []) == "SL"

    def test_prev_sl_is_sl(self):
        assert _resolve_fill_result("SL_OLD", "SL_CUR", "TP_CUR", "SL_OLD", []) == "SL"

    def test_history_sl_is_sl(self):
        assert _resolve_fill_result("SL_H1", "SL_CUR", "TP_CUR", "", ["SL_H1"]) == "SL"

    def test_current_tp_is_tp(self):
        assert _resolve_fill_result("TP_1", "SL_1", "TP_1", "", []) == "TP"


# ═══════════════════════════════════════════════════════════════════
# Normalized handler: pending_* writes (not confirmed)
# ═══════════════════════════════════════════════════════════════════


def _make_handler(active_trades, exit_cb=None):
    """Create UserDataHandler and return the on_order_update callback."""
    from trading.user_data_handler import UserDataHandler

    handler = UserDataHandler(
        active_trades,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        exit_cb or AsyncMock(),
    )
    captured = {}

    def _mock_user_data(event_type):
        def _decorator(func):
            captured[event_type] = func
            return func

        return _decorator

    hub = MagicMock()
    hub.on_user_data = _mock_user_data
    handler.register(hub)
    return captured.get("ORDER_TRADE_UPDATE")


class TestNormalizedHandlerPendingWrites:
    """Patch Set 4: matched fill path writes to pending_exit_*, not confirmed."""

    @pytest.mark.asyncio
    @patch("trading.user_data_handler.WS_EVENT_NORMALIZATION_ENABLED", True)
    @patch("trading.user_data_handler.cfg")
    async def test_matched_fill_writes_to_pending(self, mock_cfg):
        active_trades = {}
        exit_cb = AsyncMock()
        on_order = _make_handler(active_trades, exit_cb)
        assert on_order is not None

        t = _trade(sl_order_id="SL_MATCH", tp_order_id="TP_X")
        active_trades["BTCUSDT"] = t

        raw_msg = {
            "o": {
                "s": "BTCUSDT",
                "c": "SL_MATCH",
                "X": "FILLED",
                "R": True,
                "ap": "49000",
                "z": "0.1",
                "Z": "4900",
            }
        }
        await on_order(raw_msg)

        assert t.get("pending_exit_price") == 49000.0
        assert t.get("pending_exit_qty") == 0.1
        assert t.get("pending_exit_order_id") == "SL_MATCH"
        assert t.get("result") == "SL"
        exit_cb.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("trading.user_data_handler.WS_EVENT_NORMALIZATION_ENABLED", True)
    @patch("trading.user_data_handler.cfg")
    async def test_unmatched_reduce_only_writes_pending(self, mock_cfg):
        active_trades = {}
        exit_cb = AsyncMock()
        on_order = _make_handler(active_trades, exit_cb)
        assert on_order is not None

        t = _trade(sl_order_id="SL_X", tp_order_id="TP_X")
        active_trades["BTCUSDT"] = t

        raw_msg = {
            "o": {
                "s": "BTCUSDT",
                "c": "UNKNOWN_ID",
                "X": "FILLED",
                "R": True,
                "ap": "50000",
                "z": "0.1",
            }
        }
        with pytest.raises(Exception):  # WSFallbackError
            await on_order(raw_msg)

        assert t.get("pending_exit_price") == 50000.0
        assert t.get("pending_exit_reason") is not None
        assert t.get("result") == "WS_FALLBACK"
        exit_cb.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════
# _exit_trade promotion tests (Patch Set 4)
# ═══════════════════════════════════════════════════════════════════


class TestExitTradePromotion:
    """Pending → confirmed promotion runs for non-WS_FALLBACK results."""

    @patch("bot.cfg", autospec=True)
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    def test_sl_matched_pending_promoted_to_confirmed(
        self, mock_hub, mock_rest, mock_cfg
    ):
        mock_cfg.SYMBOLS = ["BTCUSDT"]
        mock_cfg.BINANCE_API_KEY = ""
        mock_cfg.IS_TESTNET = True
        mock_cfg.FVG_SIZE_MAP = {}
        mock_cfg.FVG_BUFFER_MULT = 1.5
        mock_cfg.FVG_WICK_RATIO_MAX = 0.5
        mock_cfg.SL_ATR_MULT = 2.0
        mock_cfg.TP_RR = 2.0
        mock_cfg.RISK_PER_TRADE = 0.01
        mock_cfg.FVG_MIN_SIZE_ATR_MULT = 0.3
        mock_cfg.DEFAULT_ATR_FALLBACK_PCT = 0.01
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            result="SL",
        )
        trade["pending_exit_price"] = 49000.0
        trade["pending_exit_qty"] = 0.1
        trade["pending_exit_order_id"] = "SL_MATCH"
        trade["pending_exit_timestamp"] = 100000

        bot.active_trades["BTCUSDT"] = trade

        import asyncio

        asyncio.run(bot._exit_trade_legacy("BTCUSDT", trade, 100000))

        assert trade["exit_price"] == 49000.0
        assert trade["exit_actual_price"] == 49000.0
        assert trade["exit_actual_qty"] == 0.1
        assert trade["exit_order_id"] == "SL_MATCH"
        assert trade["exit_timestamp"] == 100000
        assert trade.get("pending_exit_price") is None

    @patch("bot.cfg", autospec=True)
    @patch("bot.BinanceRESTClient")
    @patch("bot.BinanceWSHub")
    def test_ws_fallback_promotion_still_works(self, mock_hub, mock_rest, mock_cfg):
        mock_cfg.SYMBOLS = ["BTCUSDT"]
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.IS_TESTNET = True
        mock_cfg.FVG_SIZE_MAP = {}
        mock_cfg.FVG_BUFFER_MULT = 1.5
        mock_cfg.FVG_WICK_RATIO_MAX = 0.5
        mock_cfg.SL_ATR_MULT = 2.0
        mock_cfg.TP_RR = 2.0
        mock_cfg.RISK_PER_TRADE = 0.01
        mock_cfg.FVG_MIN_SIZE_ATR_MULT = 0.3
        mock_cfg.DEFAULT_ATR_FALLBACK_PCT = 0.01
        from bot import PaperTrader

        bot = PaperTrader(symbols=["BTCUSDT"])
        bot.order_manager.position_still_open = AsyncMock(return_value=False)
        bot.order_manager.verify_protection = AsyncMock(return_value=(True, True))

        trade = ActiveTrade(
            symbol="BTCUSDT",
            side="long",
            entry_price=50000.0,
            sl=49000.0,
            tp=52000.0,
            qty=0.1,
            result="WS_FALLBACK",
        )
        trade["pending_exit_price"] = 51000.0
        trade["pending_exit_qty"] = 0.1
        trade["pending_exit_order_id"] = "WF_001"
        trade["pending_exit_timestamp"] = 100000

        bot.active_trades["BTCUSDT"] = trade

        import asyncio

        asyncio.run(bot._exit_trade_legacy("BTCUSDT", trade, 100000))

        assert trade["exit_price"] == 51000.0
        assert trade["exit_actual_price"] == 51000.0
        assert trade["pending_exit_price"] is None


# ═══════════════════════════════════════════════════════════════════
# Self-exit race guard (P2-4)
# ═══════════════════════════════════════════════════════════════════


class TestSelfExitRaceGuard:
    """Unmatched reduceOnly fill during self-exit should NOT trigger WS_FALLBACK."""

    @pytest.mark.asyncio
    @patch("trading.user_data_handler.WS_EVENT_NORMALIZATION_ENABLED", True)
    @patch("trading.user_data_handler.cfg")
    async def test_unmatched_reduce_only_skipped_when_exit_submitted(self, mock_cfg):
        active_trades = {}
        exit_cb = AsyncMock()
        on_order = _make_handler(active_trades, exit_cb)
        assert on_order is not None

        t = _trade(sl_order_id="SL_X", tp_order_id="TP_X", status="EXIT_SUBMITTED")
        active_trades["BTCUSDT"] = t

        raw_msg = {
            "o": {
                "s": "BTCUSDT",
                "c": "MARKET_CLOSE_001",
                "i": 77777,
                "X": "FILLED",
                "R": True,
                "ap": "50000",
                "z": "0.1",
            }
        }
        await on_order(raw_msg)

        assert t.get("result") != "WS_FALLBACK"
        assert t.get("pending_exit_reason") is None
        exit_cb.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("trading.user_data_handler.WS_EVENT_NORMALIZATION_ENABLED", True)
    @patch("trading.user_data_handler.cfg")
    async def test_unmatched_reduce_only_skipped_when_exit_verifying(self, mock_cfg):
        active_trades = {}
        exit_cb = AsyncMock()
        on_order = _make_handler(active_trades, exit_cb)
        assert on_order is not None

        t = _trade(sl_order_id="SL_X", tp_order_id="TP_X", status="EXIT_VERIFYING")
        active_trades["BTCUSDT"] = t

        raw_msg = {
            "o": {
                "s": "BTCUSDT",
                "c": "TRAIL_CLOSE_001",
                "i": 88888,
                "X": "FILLED",
                "R": True,
                "ap": "50500",
                "z": "0.1",
            }
        }
        await on_order(raw_msg)

        assert t.get("result") != "WS_FALLBACK"
        assert t.get("pending_exit_reason") is None
        exit_cb.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("trading.user_data_handler.WS_EVENT_NORMALIZATION_ENABLED", True)
    @patch("trading.user_data_handler.cfg")
    async def test_unmatched_reduce_only_skipped_when_exit_requested(self, mock_cfg):
        active_trades = {}
        exit_cb = AsyncMock()
        on_order = _make_handler(active_trades, exit_cb)
        assert on_order is not None

        t = _trade(sl_order_id="SL_X", tp_order_id="TP_X", status="EXIT_REQUESTED")
        active_trades["BTCUSDT"] = t

        raw_msg = {
            "o": {
                "s": "BTCUSDT",
                "c": "FORCE_CLOSE_001",
                "i": 99999,
                "X": "FILLED",
                "R": True,
                "ap": "50200",
                "z": "0.1",
            }
        }
        await on_order(raw_msg)

        assert t.get("result") != "WS_FALLBACK"
        assert t.get("pending_exit_reason") is None
        exit_cb.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("trading.user_data_handler.WS_EVENT_NORMALIZATION_ENABLED", True)
    @patch("trading.user_data_handler.cfg")
    async def test_unmatched_reduce_only_still_fallback_when_active(self, mock_cfg):
        """Regression guard: ACTIVE trade should still trigger WS_FALLBACK."""
        from models import WSFallbackError

        active_trades = {}
        exit_cb = AsyncMock()
        on_order = _make_handler(active_trades, exit_cb)
        assert on_order is not None

        t = _trade(sl_order_id="SL_X", tp_order_id="TP_X", status="ACTIVE")
        active_trades["BTCUSDT"] = t

        raw_msg = {
            "o": {
                "s": "BTCUSDT",
                "c": "UNKNOWN_ORPHAN",
                "i": 66666,
                "X": "FILLED",
                "R": True,
                "ap": "50000",
                "z": "0.1",
            }
        }
        with pytest.raises(WSFallbackError):
            await on_order(raw_msg)

        assert t.get("result") == "WS_FALLBACK"
        assert t.get("pending_exit_reason") is not None
        exit_cb.assert_awaited_once()
