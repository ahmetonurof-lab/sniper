"""
test_exit_lifecycle.py — ExitLifecycleService unit tests (Patch Set 2).

Bot üzerinden değil, doğrudan DI ile kurulup test edilir.
"""

from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.exit_lifecycle import ExitLifecycleService
from models import (
    STATUS_REPAIR_REQUIRED,
    STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED,
)


pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════
# WS-FALLBACK guard tests
# ═══════════════════════════════════════════════════════════════════


class TestWsFallbackGuard:
    """WS-FALLBACK guard: stale event vs real close."""

    @patch("trading.exit_lifecycle.cfg")
    async def test_stale_event_cancels_exit(self, mock_cfg, service):
        """Position still open → exit cancelled, pending fields cleared."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        om.position_still_open = AsyncMock(return_value=True)

        trade = _trade(result="WS_FALLBACK", exit_price=51000.0)
        trade["pending_exit_price"] = 51000.0
        trade["pending_exit_qty"] = 0.1
        trade["pending_exit_order_id"] = "123"
        trade["pending_exit_timestamp"] = 50000
        trade["pending_exit_reason"] = "trail"

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False
        assert trade["pending_exit_price"] is None
        assert trade["pending_exit_reason"] is None
        assert trade["result"] is None
        om.repair_protection.assert_not_called()

    @patch("trading.exit_lifecycle.cfg")
    async def test_stale_event_repairs_missing_protection(self, mock_cfg, service):
        """Position open + missing SL/TP → repair_protection called."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        om.position_still_open = AsyncMock(return_value=True)
        om.verify_protection = AsyncMock(return_value=(True, False))

        trade = _trade(result="WS_FALLBACK")
        trade["pending_exit_reason"] = "trail"

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False
        om.repair_protection.assert_awaited_once_with(
            "BTCUSDT", trade, has_sl=True, has_tp=False
        )

    @patch("trading.exit_lifecycle.cfg")
    async def test_stale_exception_failsafe(self, mock_cfg, service):
        """REST exception in stale check → fail-safe, return False."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        om.position_still_open = AsyncMock(side_effect=Exception("timeout"))

        trade = _trade(result="WS_FALLBACK")

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False

    @patch("trading.exit_lifecycle.cfg")
    async def test_real_close_promotes_pending(self, mock_cfg, service):
        """Position already closed → pending fields promoted to confirmed."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        om.position_still_open = AsyncMock(return_value=False)

        active_trades["BTCUSDT"] = _trade(result="WS_FALLBACK")
        active_trades["BTCUSDT"]["pending_exit_price"] = 51000.0
        active_trades["BTCUSDT"]["pending_exit_qty"] = 0.1
        active_trades["BTCUSDT"]["pending_exit_order_id"] = "123"
        active_trades["BTCUSDT"]["pending_exit_timestamp"] = 50000
        active_trades["BTCUSDT"]["pending_exit_reason"] = "trail"

        svc._rsms["BTCUSDT"] = _rsm()

        trade = active_trades["BTCUSDT"]
        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True
        assert trade["exit_price"] == 51000.0
        assert trade["exit_actual_price"] == 51000.0
        assert trade["exit_actual_qty"] == 0.1
        assert trade["exit_order_id"] == "123"
        assert trade["exit_timestamp"] == 50000
        assert trade["pending_exit_reason"] is None

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_real_close_no_pending_still_works(
        self, mock_sleep, mock_cfg, service
    ):
        """Position closed but no pending data → ok, uses existing."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        om.position_still_open = AsyncMock(return_value=False)

        active_trades["BTCUSDT"] = _trade(result="WS_FALLBACK")
        svc._rsms["BTCUSDT"] = _rsm()

        trade = active_trades["BTCUSDT"]
        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True


# ═══════════════════════════════════════════════════════════════════
# Second exit prevention
# ═══════════════════════════════════════════════════════════════════


class TestSecondExitPrevention:
    @patch("trading.exit_lifecycle.cfg")
    async def test_trade_not_in_active(self, mock_cfg, service):
        """Trade already popped from active_trades → return False."""
        svc, *_, active_trades, _ = service
        mock_cfg.BINANCE_API_KEY = ""

        trade = _trade()
        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False


# ═══════════════════════════════════════════════════════════════════
# Paper-mode (no API key) exit — SL/TP/WS_FALLBACK already closed
# ═══════════════════════════════════════════════════════════════════


class TestPaperModeAlreadyClosed:
    @patch("trading.exit_lifecycle.cfg")
    async def test_tp_result_skips_market_close(self, mock_cfg, service):
        """No API key + result=TP → skip market close, go to commit."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = ""

        trade = _trade(result="TP")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True
        rest.place_market_order.assert_not_called()

    @patch("trading.exit_lifecycle.cfg")
    async def test_sl_result_skips_market_close(self, mock_cfg, service):
        """No API key + result=SL → skip market close."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = ""

        trade = _trade(result="SL")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True
        rest.place_market_order.assert_not_called()

    @patch("trading.exit_lifecycle.cfg")
    async def test_ws_fallback_skips_market_close(self, mock_cfg, service):
        """No API key + result=WS_FALLBACK → skip market close."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = ""

        trade = _trade(result="WS_FALLBACK")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True
        rest.place_market_order.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# Market close adapter ambiguity
# ═══════════════════════════════════════════════════════════════════


class TestMarketCloseAmbiguity:
    """Adapter response ambiguity: REJECTED, EXECUTION_CONFIRMED,
    REQUEST_SENT, ORDER_ACKNOWLEDGED, empty response."""

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    @patch("trading.exit_lifecycle.EntryManager.parse_market_fill")
    async def test_rejected_triggers_force_close(
        self, mock_parse, mock_sleep, mock_cfg, service
    ):
        """REJECTED → force_close attempted, verification runs."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={"_status": "REJECTED"})
        rest.get_positions = AsyncMock(return_value=[])
        mock_parse.return_value = (0, 0, None)

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        rest.place_force_close_order.assert_awaited_once_with("BTCUSDT", "SELL", "long")
        assert result is True

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    @patch("trading.exit_lifecycle.EntryManager.parse_market_fill")
    async def test_execution_confirmed_with_fill(
        self, mock_parse, mock_sleep, mock_cfg, service
    ):
        """EXECUTION_CONFIRMED with fill → exit_actual_price/qty set."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(
            return_value={"_status": "EXECUTION_CONFIRMED"}
        )
        rest.get_positions = AsyncMock(return_value=[])
        mock_parse.return_value = (0.1, 50900.0, None)

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert trade["exit_actual_price"] == 50900.0
        assert trade["exit_actual_qty"] == 0.1
        assert result is True

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    @patch("trading.exit_lifecycle.EntryManager.parse_market_fill")
    async def test_request_sent_ambiguous(
        self, mock_parse, mock_sleep, mock_cfg, service
    ):
        """REQUEST_SENT → ambiguous, verification allowed, commit if closed."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={"_status": "REQUEST_SENT"})
        rest.get_positions = AsyncMock(return_value=[])
        mock_parse.return_value = (0, 0, None)

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_empty_response_triggers_force_close(
        self, mock_sleep, mock_cfg, service
    ):
        """Empty {} response → force_close attempted."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={})
        rest.get_positions = AsyncMock(return_value=[])

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        rest.place_force_close_order.assert_awaited_once()
        assert result is True

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_empty_response_pos_open_repair(self, mock_sleep, mock_cfg, service):
        """Empty response + position still open → REPAIR_REQUIRED."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={})
        rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False
        assert trade["status"] == STATUS_REPAIR_REQUIRED

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_verify_all_attempts_fail(self, mock_sleep, mock_cfg, service):
        """5 verification attempts all fail → REPAIR_REQUIRED."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={})
        rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False
        assert trade["status"] == STATUS_REPAIR_REQUIRED
        assert rest.get_positions.await_count == 5

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_verify_succeeds_on_3rd_attempt(self, mock_sleep, mock_cfg, service):
        """Position closes on 3rd verify attempt → commit."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={})

        call_count = 0

        async def _pos():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return []
            return [{"symbol": "BTCUSDT", "positionAmt": "0.1"}]

        rest.get_positions = AsyncMock(side_effect=_pos)

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True
        assert call_count == 3


# ═══════════════════════════════════════════════════════════════════
# _commit_confirmed_exit tests
# ═══════════════════════════════════════════════════════════════════


class TestCommitConfirmedExit:
    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    @patch("trading.exit_lifecycle.mark_trade_closed")
    @patch("trading.exit_lifecycle.capture_snapshot")
    async def test_valid_fill_long(
        self, mock_snap, mock_mark_closed, mock_sleep, mock_cfg, service
    ):
        """Long exit with valid fill → PnL calculated, balance updated."""
        svc, rest, om, active_trades, trades, pl_callback, risk_mgr = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.get_positions = AsyncMock(return_value=[])

        balance_holder = [1000.0]
        svc._get_balance = lambda: balance_holder[0]
        svc._set_balance = lambda v: balance_holder.__setitem__(0, v)

        trade = _trade(side="long", result="TP")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True
        expected_pnl = round(
            (51000 - 50000) * 0.1 - (50000 * 0.1 * 0.0005 + 51000 * 0.1 * 0.0005), 2
        )
        assert balance_holder[0] == pytest.approx(1000.0 + expected_pnl)
        assert len(trades) == 1
        assert trades[0]["sym"] == "BTCUSDT"
        assert trades[0]["pnl"] == expected_pnl

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_valid_fill_short(self, mock_sleep, mock_cfg, service):
        """Short exit with valid fill → PnL calculated correctly."""
        svc, rest, om, active_trades, trades, pl_callback, risk_mgr = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.get_positions = AsyncMock(return_value=[])

        balance_holder = [1000.0]
        svc._get_balance = lambda: balance_holder[0]
        svc._set_balance = lambda v: balance_holder.__setitem__(0, v)

        trade = _trade(
            side="short", entry_price=50000.0, exit_price=49000.0, result="TP"
        )
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True
        expected_pnl = round(
            (50000 - 49000) * 0.1 - (50000 * 0.1 * 0.0005 + 49000 * 0.1 * 0.0005), 2
        )
        assert balance_holder[0] == pytest.approx(1000.0 + expected_pnl)
        assert trades[0]["pnl"] == expected_pnl

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    @patch("trading.exit_lifecycle.mark_trade_closed")
    @patch("trading.exit_lifecycle.capture_snapshot")
    async def test_update_peak_called(
        self, mock_snap, mock_mark_closed, mock_sleep, mock_cfg, service
    ):
        """update_peak called with new balance."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.get_positions = AsyncMock(return_value=[])

        peak_calls = []
        risk_mgr = MagicMock()
        risk_mgr.update_peak = lambda v: peak_calls.append(v)
        svc._risk_mgr = risk_mgr

        trade = _trade(side="long", result="TP")
        active_trades["BTCUSDT"] = trade

        await svc.execute("BTCUSDT", trade, 50000)

        assert len(peak_calls) == 1
        assert peak_calls[0] > 1000.0

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    @patch("trading.exit_lifecycle.mark_trade_closed")
    @patch("trading.exit_lifecycle.capture_snapshot")
    async def test_cleanup_on_exit_called(
        self, mock_snap, mock_mark_closed, mock_sleep, mock_cfg, service
    ):
        """cleanup_on_exit called after commit."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.get_positions = AsyncMock(return_value=[])

        trade = _trade(side="long", result="TP")
        active_trades["BTCUSDT"] = trade

        await svc.execute("BTCUSDT", trade, 50000)

        om.cleanup_on_exit.assert_awaited_once()

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    @patch("trading.exit_lifecycle.mark_trade_closed")
    @patch("trading.exit_lifecycle.capture_snapshot")
    async def test_invalid_fill_data(
        self, mock_snap, mock_mark_closed, mock_sleep, mock_cfg, service
    ):
        """Invalid fill data (zero entry price) → BROKEN_MANUAL_INTERVENTION_REQUIRED."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.get_positions = AsyncMock(return_value=[])

        trade = _trade(side="long", entry_price=0.0, exit_price=51000.0, result="TP")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False
        assert trade["status"] == STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED
        # Trade should still be in active_trades for manual inspection
        assert "BTCUSDT" in active_trades

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_second_exit_before_commit(self, mock_sleep, mock_cfg, service):
        """Trade popped before commit → return False."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.get_positions = AsyncMock(return_value=[])

        trade = _trade(side="long", result="TP")
        active_trades["BTCUSDT"] = trade

        # Pop the trade before execute completes the commit step
        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is True  # first attempt works

        # Second attempt with same trade should fail
        result2 = await svc.execute("BTCUSDT", trade, 50001)
        assert result2 is False


# ═══════════════════════════════════════════════════════════════════
# _mark_repair_required
# ═══════════════════════════════════════════════════════════════════


class TestMarkRepairRequired:
    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_repair_required_status_set(self, mock_sleep, mock_cfg, service):
        """Failed verification → status=REPAIR_REQUIRED, protection verified."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={})
        rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        result = await svc.execute("BTCUSDT", trade, 50000)

        assert result is False
        assert trade["status"] == STATUS_REPAIR_REQUIRED
        om.verify_protection.assert_awaited_once()

    @patch("trading.exit_lifecycle.cfg")
    @patch("trading.exit_lifecycle.asyncio.sleep")
    async def test_repair_repairs_missing_protection(
        self, mock_sleep, mock_cfg, service
    ):
        """REPAIR_REQUIRED + missing SL → repair_protection called."""
        svc, rest, om, active_trades, *_ = service
        mock_cfg.BINANCE_API_KEY = "test_key"
        rest.place_market_order = AsyncMock(return_value={})
        rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.1"}]
        )
        om.verify_protection = AsyncMock(return_value=(False, True))

        trade = _trade(side="long", result="TRAIL_CLOSE")
        active_trades["BTCUSDT"] = trade

        await svc.execute("BTCUSDT", trade, 50000)

        om.repair_protection.assert_awaited_once_with(
            "BTCUSDT", trade, has_sl=False, has_tp=True
        )


@pytest.fixture
def service():
    rest = AsyncMock()
    rest.place_market_order = AsyncMock(return_value={})
    rest.place_force_close_order = AsyncMock(return_value=True)
    rest.get_positions = AsyncMock(return_value=[])

    om = AsyncMock()
    om.position_still_open = AsyncMock(return_value=False)
    om.verify_protection = AsyncMock(return_value=(True, True))
    om.repair_protection = AsyncMock()
    om.cleanup_on_exit = AsyncMock()

    active_trades: dict = {}
    states: dict = {}
    rsms: dict = {}
    trades: deque = deque(maxlen=1000)

    rsm = _rsm()
    rsms["BTCUSDT"] = rsm

    pl_callback = MagicMock()
    risk_mgr = MagicMock()
    risk_mgr.update_peak = MagicMock()

    svc = ExitLifecycleService(
        rest_client=rest,
        order_manager=om,
        active_trades=active_trades,
        states=states,
        rsms=rsms,
        trades=trades,
        pl_callback=pl_callback,
        risk_mgr=risk_mgr,
        balance_getter=lambda: 1000.0,
        balance_setter=lambda v: None,
        wallet_balance_getter=lambda: 1000.0,
        output_dir="/tmp",
        fvg_state_file="/tmp/fvg.json",
    )
    return svc, rest, om, active_trades, trades, pl_callback, risk_mgr


def _rsm():
    """Return a minimal RetraceStateMachine-like mock."""
    rsm = MagicMock()
    rsm.sweep_level = None
    rsm.direction = None
    rsm.reset = MagicMock()
    return rsm


def _trade(side="long", **kw):
    """Quick ActiveTrade factory with dict access."""
    from models import ActiveTrade

    base = dict(
        symbol="BTCUSDT",
        side=side,
        entry_price=50000.0,
        sl=49000.0,
        tp=52000.0,
        qty=0.1,
        exit_price=51000.0,
        result="TP",
        status="",
        trailing_count=0,
        exit_bar=50,
    )
    base.update(kw)
    return ActiveTrade(
        **{
            k: v
            for k, v in base.items()
            if k
            in (
                "symbol",
                "side",
                "entry_price",
                "sl",
                "tp",
                "qty",
                "exit_price",
                "result",
                "status",
                "trailing_count",
                "exit_bar",
            )
        }
    )
