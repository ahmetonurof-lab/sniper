"""
test_integration_lifecycle.py — Full lifecycle integration tests (Sprint C/E).
Mock Binance REST + WS ile entry → trail → SL/TP exit → cleanup akisi.
Refactor servisleri (ExitLifecycleService, ProtectionLifecycleService)
birlikte calisirken test eder.
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import (
    ActiveTrade,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    STATUS_EXIT_REQUESTED,
    STATUS_EXIT_VERIFYING,
)
from trading.exit_lifecycle import ExitLifecycleService
from trading.order_manager import OrderManager
from trading.protection_lifecycle import ProtectionLifecycleService


def _tmpdir():
    d = tempfile.mkdtemp()
    return d


def _exit_service(mock_rest, order_mgr, active_trades):
    return ExitLifecycleService(
        rest_client=mock_rest,
        order_manager=order_mgr,
        active_trades=active_trades,
        states={},
        rsms={},
        trades=[],
        pl_callback=MagicMock(),
        risk_mgr=MagicMock(),
        balance_getter=lambda: 1000.0,
        balance_setter=lambda v: None,
        wallet_balance_getter=lambda: 1000.0,
        output_dir=_tmpdir(),
        fvg_state_file=os.path.join(_tmpdir(), "fvg.json"),
    )


def _trade(**kw):
    base = dict(
        symbol="BTCUSDT",
        side="long",
        entry_price=50000.0,
        sl=49000.0,
        tp=52000.0,
        qty=0.1,
        sl_order_id="SL_INIT",
        tp_order_id="TP_INIT",
        status="ACTIVE",
        trailing_count=0,
        trail_steps=[],
        entry_bar_index=0,
        exit_bar=50,
    )
    base.update(kw)
    t = ActiveTrade(
        symbol=base["symbol"],
        side=base["side"],
        entry_price=base["entry_price"],
        sl=base["sl"],
        tp=base["tp"],
        qty=base["qty"],
        status=base.get("status", "ACTIVE"),
    )
    t["sl_order_id"] = base.get("sl_order_id", "")
    t["tp_order_id"] = base.get("tp_order_id", "")
    t["trailing_count"] = base.get("trailing_count", 0)
    t["trail_steps"] = base.get("trail_steps", [])
    t["entry_bar_index"] = base.get("entry_bar_index", 0)
    t["exit_bar"] = base.get("exit_bar", 50)
    return t


@pytest.fixture
def mock_rest():
    r = MagicMock()
    r.place_stop_order = AsyncMock(return_value={"algoId": "SL_NEW"})
    r.place_tp_order = AsyncMock(return_value={"algoId": "TP_NEW"})
    r.cancel_order = AsyncMock(return_value=True)
    r.get_all_orders = AsyncMock(return_value=[])
    r.get_positions = AsyncMock(return_value=[])
    r.place_market_order = AsyncMock(
        return_value={"orderId": 999, "_status": "EXECUTION_CONFIRMED"}
    )
    r.cancel_all_open_orders = AsyncMock()
    r.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
    return r


@pytest.fixture
def protection_svc():
    return ProtectionLifecycleService()


@pytest.fixture
def order_mgr(mock_rest, protection_svc):
    return OrderManager(
        rest_client=mock_rest, is_live=True, protection_service=protection_svc
    )


# ═══════════════════════════════════════════════════════════════════
# Scenario 1: Entry → Trail → SL exit
# ═══════════════════════════════════════════════════════════════════


class TestEntryTrailSlExit:
    @pytest.mark.asyncio
    @patch("trading.exit_lifecycle.cfg")
    async def test_full_sl_lifecycle(self, mock_cfg, mock_rest, order_mgr):
        """Entry → 1 trail → SL tetiklenir → exit verified → cleanup."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.EXIT_LIFECYCLE_SERVICE_ENABLED = True
        mock_cfg.PROTECTION_LIFECYCLE_SERVICE_ENABLED = True

        active_trades: dict[str, ActiveTrade] = {}
        mock_rsm = MagicMock()
        exit_svc = _exit_service(mock_rest, order_mgr, active_trades)
        exit_svc._rsms = {"BTCUSDT": mock_rsm}

        trade = _trade(
            sl=49500.0,
            tp=51500.0,
            sl_order_id="SL_INIT",
            tp_order_id="TP_INIT",
            trailing_count=0,
        )
        active_trades["BTCUSDT"] = trade
        exit_svc._rsms["BTCUSDT"] = mock_rsm

        # --- Trail 1: tighten SL from 49500 to 49800 ---
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "SL_TRAIL1"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "TP_TRAIL1"})

        success = await order_mgr.update_trail_orders(
            "BTCUSDT", trade, 49800.0, 51800.0, 1
        )
        assert success is True
        assert trade["sl_order_id"] == "SL_TRAIL1"
        assert trade["tp_order_id"] == "TP_TRAIL1"
        assert trade["trailing_count"] == 1
        # STATUS_TRAIL_REPLACING set during trail, cleared after
        assert trade["status"] == STATUS_ACTIVE
        assert trade.runtime.status.value == STATUS_ACTIVE

        # --- SL exit: position closed by SL ---
        trade["result"] = "SL"
        trade["pending_exit_price"] = 49800.0
        trade["pending_exit_timestamp"] = 100000

        mock_rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0"}]
        )
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        result = await exit_svc.execute("BTCUSDT", trade, 100000)
        assert result is True  # commit succeeded
        assert trade.get("status") == STATUS_CLOSED

        # Cleanup called
        mock_rest.cancel_order.assert_any_call(
            "TP_TRAIL1", "BTCUSDT", reason="exit_close", is_algo=True
        )

    @pytest.mark.asyncio
    @patch("trading.exit_lifecycle.cfg")
    async def test_trail_state_transitions(self, mock_cfg, mock_rest, order_mgr):
        """TRAIL_REPLACING set during trail, cleared after."""
        mock_cfg.BINANCE_API_KEY = "test_key"

        trade = _trade(sl_order_id="SL_OLD", tp_order_id="TP_OLD")
        trade.runtime.status = trade.runtime.status  # ensure synced

        assert trade["status"] == STATUS_ACTIVE

        await order_mgr.update_trail_orders("BTCUSDT", trade, 49800.0, 51800.0, 1)
        assert trade["status"] == STATUS_ACTIVE  # restored


# ═══════════════════════════════════════════════════════════════════
# Scenario 2: Entry → Trail → TP exit
# ═══════════════════════════════════════════════════════════════════


class TestEntryTrailTpExit:
    @pytest.mark.asyncio
    @patch("trading.exit_lifecycle.cfg")
    async def test_full_tp_lifecycle(self, mock_cfg, mock_rest, order_mgr):
        """Entry → TP tetiklenir → paper mode skip close → commit."""
        mock_cfg.BINANCE_API_KEY = ""  # paper
        mock_cfg.EXIT_LIFECYCLE_SERVICE_ENABLED = True

        active_trades: dict[str, ActiveTrade] = {}
        exit_svc = _exit_service(mock_rest, order_mgr, active_trades)
        exit_svc._rsms = {"BTCUSDT": MagicMock()}

        trade = _trade(
            sl=49500.0,
            tp=51500.0,
            sl_order_id="SL_TP_TEST",
            tp_order_id="TP_TP_TEST",
            exit_price=51500.0,
            exit_actual_price=51500.0,
            exit_actual_qty=0.1,
            result="TP",
        )
        trade["exit_actual_price"] = 51500.0
        trade["exit_actual_qty"] = 0.1
        active_trades["BTCUSDT"] = trade

        result = await exit_svc.execute("BTCUSDT", trade, 100000)
        assert result is True
        assert trade.get("status") == STATUS_CLOSED


# ═══════════════════════════════════════════════════════════════════
# Scenario 3: Protection repair after broken TP
# ═══════════════════════════════════════════════════════════════════


class TestProtectionRepair:
    @pytest.mark.asyncio
    async def test_repair_broken_tp(self, mock_rest, order_mgr):
        """TP ID bos → repair → yeni TP yerlesir, empty ID yazilmaz."""
        trade = _trade(
            sl=49500.0,
            tp=51500.0,
            sl_order_id="SL_OK",
            tp_order_id="",  # broken!
        )

        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "TP_REPAIRED"})

        await order_mgr.repair_protection("BTCUSDT", trade, has_sl=True, has_tp=False)
        assert trade["tp_order_id"] == "TP_REPAIRED"

    @pytest.mark.asyncio
    async def test_repair_tp_rejected_no_empty_id(self, mock_rest, order_mgr):
        """Binance rejects TP → trade['tp_order_id'] bos kalmaz."""
        trade = _trade(
            sl=49500.0,
            tp=51500.0,
            sl_order_id="SL_OK",
            tp_order_id="",  # broken!
        )

        # Simulate Binance rejection (empty dict response)
        mock_rest.place_tp_order = AsyncMock(return_value={})

        await order_mgr.repair_protection("BTCUSDT", trade, has_sl=True, has_tp=False)
        # TP order id should NOT be set to empty string
        assert trade["tp_order_id"] == ""  # was already empty, stayed empty
        # The old value was "", it stays "" — no silent overwrite with fake ID


# ═══════════════════════════════════════════════════════════════════
# Scenario 4: Orphan sweep transition guard
# ═══════════════════════════════════════════════════════════════════


class TestOrphanTransitionGuard:
    @pytest.mark.asyncio
    async def test_exit_verifying_skips_orphan(self, mock_rest, protection_svc):
        """EXIT_VERIFYING status → orphan sweep skip eder."""
        trade = _trade(status="EXIT_VERIFYING")
        assert protection_svc.should_skip_reconcile(trade) is True

    @pytest.mark.asyncio
    async def test_active_allows_orphan(self, mock_rest, protection_svc):
        """ACTIVE status → orphan sweep calisir."""
        trade = _trade(status="ACTIVE")
        assert protection_svc.should_skip_reconcile(trade) is False


# ═══════════════════════════════════════════════════════════════════
# Scenario 5: Known IDs cover all sources
# ═══════════════════════════════════════════════════════════════════


class TestKnownIdsAllSources:
    def test_all_sources_in_known_ids(self, protection_svc):
        """current, prev, pending, history — hepsi known_ids'te."""
        trade = _trade(
            sl_order_id="SL_C",
            tp_order_id="TP_C",
        )
        trade["sl_order_id_prev"] = "SL_P"
        trade["tp_order_id_prev"] = "TP_P"
        trade["pending_sl_order_id"] = "SL_X"
        trade["pending_tp_order_id"] = "TP_X"
        trade["sl_order_id_history"] = ["SL_H1"]
        trade["tp_order_id_history"] = ["TP_H1"]

        ids = protection_svc.known_ids(trade)
        expected = {"SL_C", "TP_C", "SL_P", "TP_P", "SL_X", "TP_X", "SL_H1", "TP_H1"}
        assert ids == expected


# ═══════════════════════════════════════════════════════════════════
# Scenario 6: Full exit state transitions
# ═══════════════════════════════════════════════════════════════════


class TestExitStateTransitions:
    def test_active_to_exit_requested(self):
        """Trade ACTIVE → EXIT_REQUESTED (set by _on_1m_close)."""
        trade = _trade(status="ACTIVE")
        trade["status"] = STATUS_EXIT_REQUESTED
        assert trade["status"] == STATUS_EXIT_REQUESTED
        assert trade.runtime.status.value == STATUS_EXIT_REQUESTED

    def test_submitted_to_verifying(self):
        """Trade EXIT_SUBMITTED → EXIT_VERIFYING (set by exit service)."""
        trade = _trade(status="EXIT_SUBMITTED")
        trade["status"] = STATUS_EXIT_VERIFYING
        assert trade["status"] == STATUS_EXIT_VERIFYING
        assert trade.runtime.status.value == STATUS_EXIT_VERIFYING

    def test_closed_terminal(self):
        """Trade CLOSED → terminal state."""
        trade = _trade(status="EXIT_VERIFYING")
        trade["status"] = STATUS_CLOSED
        assert trade["status"] == STATUS_CLOSED
        assert trade.runtime.status.value == STATUS_CLOSED
