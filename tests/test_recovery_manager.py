"""
test_recovery_manager.py — RecoveryManager unit tests
───────────────────────────────────────────────────────
Kapsam: recover_positions closePosition fallback (06067c6)
Mock: BinanceRESTClient, cfg
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from models import STATUS_ACTIVE, ActiveTrade
from trading.exit_lifecycle import _trade_identity_key


def _pl_noop(*args, **kwargs):
    pass


# ═══════════════════════════════════════════════════════════════
# recover_positions — emergency close with closePosition fallback
# ═══════════════════════════════════════════════════════════════


class TestRecoverPositionsCloseFallback:
    """SL kurulamadiginda market close basarisiz -> closePosition fallback (06067c6)"""

    @patch("trading.recovery_manager.cfg")
    def test_force_close_called_when_market_close_returns_none(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={})
        rest.place_market_order_priority = AsyncMock(return_value=None)
        rest.place_force_close_order = AsyncMock(return_value=True)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        rest.place_force_close_order.assert_called_once_with("BTCUSDT", "SELL", "long")

    @patch("trading.recovery_manager.cfg")
    def test_force_close_not_called_when_market_close_succeeds(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={})
        rest.place_market_order_priority = AsyncMock(return_value={"orderId": 123})
        rest.place_force_close_order = AsyncMock(return_value=True)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        rest.place_force_close_order.assert_not_called()

    @patch("trading.recovery_manager.cfg")
    def test_position_stays_when_both_close_methods_fail(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={})
        rest.place_market_order_priority = AsyncMock(return_value=None)
        rest.place_force_close_order = AsyncMock(return_value=False)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" in active_trades
        assert active_trades["BTCUSDT"].is_recovered is True
        assert active_trades["BTCUSDT"].trail_mode == "fvg"

    @patch("trading.recovery_manager.cfg")
    def test_force_close_exception_handled(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={})
        rest.place_market_order_priority = AsyncMock(return_value=None)
        rest.place_force_close_order = AsyncMock(side_effect=Exception("network error"))

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" in active_trades

    @patch("trading.recovery_manager.cfg")
    def test_emergency_close_success_without_tp_skips_critical_log(self, mock_cfg):
        """close_result truthy + tp_id bos -> continue ile kritik log atlanir,
        active_trades'e eklenmez (A4-04 ikincil bug)."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={})
        rest.place_market_order_priority = AsyncMock(
            return_value={"orderId": 123, "_status": "EXECUTION_CONFIRMED"}
        )
        rest.place_force_close_order = AsyncMock(return_value=True)
        rest.get_tick_size = AsyncMock(return_value=0.00001)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" not in active_trades
        rest.place_force_close_order.assert_not_called()

    @patch("trading.recovery_manager.cfg")
    def test_emergency_close_success_with_tp_cancel_skips_critical_log(self, mock_cfg):
        """close_result truthy + tp_id var + cancel basarili -> continue,
        active_trades'e eklenmez."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={"orderId": 456})
        rest.place_market_order_priority = AsyncMock(
            return_value={"orderId": 123, "_status": "EXECUTION_CONFIRMED"}
        )
        rest.cancel_order = AsyncMock(return_value=True)
        rest.place_force_close_order = AsyncMock(return_value=True)
        rest.get_tick_size = AsyncMock(return_value=0.00001)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" not in active_trades
        rest.cancel_order.assert_called_once_with(
            "456", "BTCUSDT", reason="recover_emergency_close", is_algo=True
        )

    @patch("trading.recovery_manager.cfg")
    def test_emergency_close_success_with_tp_cancel_failure_skips_critical_log(
        self, mock_cfg
    ):
        """close_result truthy + tp_id var + cancel basarisiz -> continue,
        active_trades'e eklenmez (kritik log atlanir)."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={"orderId": 456})
        rest.place_market_order_priority = AsyncMock(
            return_value={"orderId": 123, "_status": "EXECUTION_CONFIRMED"}
        )
        rest.cancel_order = AsyncMock(side_effect=Exception("cancel failed"))
        rest.place_force_close_order = AsyncMock(return_value=True)
        rest.get_tick_size = AsyncMock(return_value=0.00001)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" not in active_trades
        rest.cancel_order.assert_awaited()

    @patch("trading.recovery_manager.cfg")
    def test_sl_place_exception_triggers_emergency_close(self, mock_cfg):
        """A4-05: SL yerleştirme exception'da sl_id boş kalır -> acil kapanış dalına düşer."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(side_effect=Exception("SL place failed"))
        rest.place_tp_order = AsyncMock(return_value={"orderId": 456})
        rest.place_market_order_priority = AsyncMock(
            return_value={"orderId": 123, "_status": "EXECUTION_CONFIRMED"}
        )
        rest.get_tick_size = AsyncMock(return_value=0.00001)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" not in active_trades
        rest.place_market_order_priority.assert_called_once()

    @patch("trading.recovery_manager.cfg")
    def test_tp_place_exception_adds_trade_with_sl_only(self, mock_cfg):
        """A4-05: TP yerleştirme exception'da sl_id varsa pozisyon SL'li ama TP'siz active_trades'e girer."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={"orderId": 123})
        rest.place_tp_order = AsyncMock(side_effect=Exception("TP place failed"))
        rest.get_tick_size = AsyncMock(return_value=0.00001)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" in active_trades
        assert active_trades["BTCUSDT"].sl_order_id == "123"
        assert active_trades["BTCUSDT"].tp_order_id == ""

    @patch("trading.recovery_manager.cfg")
    def test_emergency_close_retry_succeeds_on_second_attempt(self, mock_cfg):
        """A4-08: Acil kapanis ilk deneme basarisiz, retry sonra basarili -> active_trades'e eklenmez."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_cfg.RECOVERY_SL_FALLBACK_PCT = 0.01

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.1",
                    "entryPrice": "50000",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")
        rest.get_order_price = MagicMock(return_value=0.0)
        rest.apply_price_precision = AsyncMock(return_value=49000.0)
        rest.place_stop_order = AsyncMock(return_value={})
        rest.place_tp_order = AsyncMock(return_value={})
        rest.place_market_order_priority = AsyncMock(
            side_effect=[None, {"orderId": 123, "_status": "EXECUTION_CONFIRMED"}]
        )
        rest.place_force_close_order = AsyncMock(return_value=False)
        rest.get_tick_size = AsyncMock(return_value=0.00001)

        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"BTCUSDT": 100.0},
        )

        asyncio.run(rm.recover_positions())

        assert "BTCUSDT" not in active_trades
        assert rest.place_market_order_priority.call_count == 2


# ═══════════════════════════════════════════════════════════════
# FIX (tick_size): recovery, _try_entry ile aynı ActiveTrade şemasını
# kurmalı — tick_size, status, trail_count, trail_mode.
# ═══════════════════════════════════════════════════════════════


class TestRecoveredTradeFieldParity:
    """Recover edilen trade'ler sessizce models default'larına (tick_size=0.10,
    status="") düşmemeli — trailing normalize'u kilitler (bkz. ALGO/RENDER bug)."""

    def _rest_with_sltp(self, get_tick_size_value: float = 0.00001):
        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "ALGOUSDT",
                    "positionAmt": "-21683.2",
                    "entryPrice": "0.08993",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(
            return_value=[
                {
                    "symbol": "ALGOUSDT",
                    "type": "STOP_MARKET",
                    "orderId": 1000000157670490,
                    "stopPrice": "0.09353",
                    "reduceOnly": True,
                },
                {
                    "symbol": "ALGOUSDT",
                    "type": "TAKE_PROFIT_MARKET",
                    "orderId": 1000000157670494,
                    "stopPrice": "0.08669",
                    "reduceOnly": True,
                },
            ]
        )
        rest.get_order_type = MagicMock(side_effect=lambda o: o.get("type", ""))
        rest.get_order_price = MagicMock(
            side_effect=lambda o: float(o.get("stopPrice", 0))
        )
        rest.get_tick_size = AsyncMock(return_value=get_tick_size_value)
        return rest

    @patch("trading.recovery_manager.cfg")
    def test_new_recovered_trade_carries_full_schema(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = self._rest_with_sltp(get_tick_size_value=0.00001)
        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["ALGOUSDT"],
            cfgs={"ALGOUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"ALGOUSDT": 0.001},
        )

        asyncio.run(rm.recover_positions())

        t = active_trades["ALGOUSDT"]
        assert t.is_recovered is True
        assert t.tick_size == 0.00001
        assert t.status == STATUS_ACTIVE
        assert t.trail_mode == "fvg"
        assert t.trail_count == 0
        assert t.trailing_count == 0
        assert t.sl == 0.09353
        assert t.tp == 0.08669
        assert t.sl_order_id == "1000000157670490"
        assert t.tp_order_id == "1000000157670494"
        assert t.protection_orders["sl"]["order_id"] == "1000000157670490"
        assert t.protection_orders["sl"]["type"] == "STOP_MARKET"
        assert t.protection_orders["tp"]["order_id"] == "1000000157670494"
        assert t.protection_orders["tp"]["type"] == "TAKE_PROFIT_MARKET"

    @patch("trading.recovery_manager.cfg")
    def test_existing_trade_tick_size_refreshed(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = self._rest_with_sltp(get_tick_size_value=0.001)
        existing = ActiveTrade(
            symbol="ALGOUSDT",
            side="short",
            entry_price=0.08993,
            sl=0.09353,
            tp=0.08669,
            is_recovered=True,
            tick_size=0.10,
            sl_order_id="old_sl",
            tp_order_id="old_tp",
        )
        active_trades = {"ALGOUSDT": existing}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["ALGOUSDT"],
            cfgs={"ALGOUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"ALGOUSDT": 0.001},
        )

        asyncio.run(rm.recover_positions())

        assert existing["tick_size"] == 0.001
        assert existing["sl"] == 0.09353
        assert existing["sl_order_id"] == "1000000157670490"


# ═══════════════════════════════════════════════════════════════
# P1-4 — reconcile_ghost_positions periyodik hale getirme
# (restart-only → periyodik + restart)
# ═══════════════════════════════════════════════════════════════


class TestPeriodicLoopGhostReconcile:
    """P1-4: periodic_check_loop, reconcile_ghost_positions'i de
    calistirmali (orphan sweep ile ayni 60sn aralikta)."""

    @patch("trading.recovery_manager.cfg")
    def test_periodic_loop_runs_ghost_reconcile(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(return_value=[])
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades={},
            pl_callback=_pl_noop,
            atr_state={},
        )
        rm.recover_positions = AsyncMock()
        rm.reconcile_orphan_orders = AsyncMock()
        rm.reconcile_ghost_positions = AsyncMock()

        async def fake_sleep(_s):
            raise asyncio.CancelledError()

        with patch("trading.recovery_manager.asyncio.sleep", fake_sleep):
            try:
                asyncio.run(rm.periodic_check_loop())
            except asyncio.CancelledError:
                pass

        rm.reconcile_orphan_orders.assert_awaited()
        rm.reconcile_ghost_positions.assert_awaited()

    @patch("trading.recovery_manager.cfg")
    def test_ghost_reconcile_cleans_closed_position(self, mock_cfg):
        """Ghost pozisyon temizligi mantigi degismemeli (P1-4 kapsam kilidi):
        state open=true + Binance kapali -> mark_trade_closed + ghost_cleaned."""
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = MagicMock()
        rest.get_positions = AsyncMock(return_value=[])
        rest.get_all_orders = AsyncMock(return_value=[])
        rest.get_order_type = MagicMock(return_value="")

        states = {"BTCUSDT": MagicMock()}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["BTCUSDT"],
            cfgs={"BTCUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states=states,
            active_trades={},
            pl_callback=_pl_noop,
            atr_state={},
        )

        with patch("state_manager.dump_state") as m_dump, patch(
            "state_manager.mark_trade_closed"
        ) as m_close, patch("trading.recovery_manager.log_event") as m_event:
            m_dump.return_value = {"BTCUSDT": {"open": True, "count": 1}}
            asyncio.run(rm.reconcile_ghost_positions())

        m_close.assert_called_once_with("BTCUSDT")
        m_event.assert_called_once_with("ghost_cleaned", "BTCUSDT")
        assert states["BTCUSDT"].trades_today == 0
        rest.get_positions.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════
# recover_positions — DOGEUSDT çift emir kazası (Fix B + C)
# ═══════════════════════════════════════════════════════════════


class TestRecoverPositionsProtectionDedupe:
    """Restore sirasinda borsada ayni pozisyon icin 2 SL + 2 TP birikmisse:
    - Fix C: en yeni cift tutulur, fazlalar iptal edilir (dedupe, state yazimindan ONCE).
    - Fix B: protection_orders gercek borsa tipi ile doldurulur."""

    def _make_rest(self):
        rest = MagicMock()
        rest.get_positions = AsyncMock(
            return_value=[
                {
                    "symbol": "DOGEUSDT",
                    "positionAmt": "26770",
                    "entryPrice": "0.07037",
                }
            ]
        )
        rest.get_all_orders = AsyncMock(
            return_value=[
                {
                    "symbol": "DOGEUSDT",
                    "type": "STOP_MARKET",
                    "orderId": 1000000161372927,
                    "stopPrice": "0.070140",
                    "reduceOnly": True,
                },
                {
                    "symbol": "DOGEUSDT",
                    "type": "STOP_MARKET",
                    "orderId": 1000000161375000,
                    "stopPrice": "0.070240",
                    "reduceOnly": True,
                },
                {
                    "symbol": "DOGEUSDT",
                    "type": "TAKE_PROFIT_MARKET",
                    "orderId": 1000000161372934,
                    "stopPrice": "0.070770",
                    "reduceOnly": True,
                },
                {
                    "symbol": "DOGEUSDT",
                    "type": "TAKE_PROFIT_MARKET",
                    "orderId": 1000000161375100,
                    "stopPrice": "0.070870",
                    "reduceOnly": True,
                },
            ]
        )
        rest.get_order_type = MagicMock(side_effect=lambda o: o.get("type", ""))
        rest.get_order_price = MagicMock(
            side_effect=lambda o: float(o.get("stopPrice", 0))
        )
        rest.get_tick_size = AsyncMock(return_value=0.00001)
        rest.cancel_order = AsyncMock(return_value={})
        return rest

    @patch("trading.recovery_manager.cfg")
    def test_recover_dedupes_duplicate_sl_tp_and_fills_protection_orders(
        self, mock_cfg
    ):
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = self._make_rest()
        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["DOGEUSDT"],
            cfgs={"DOGEUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"DOGEUSDT": 0.0001},
        )

        asyncio.run(rm.recover_positions())

        t = active_trades["DOGEUSDT"]
        assert t.sl_order_id == "1000000161375000"
        assert t.tp_order_id == "1000000161375100"

        cancel_ids = {c.args[0] for c in rest.cancel_order.call_args_list}
        assert cancel_ids == {"1000000161372927", "1000000161372934"}

        assert t.protection_orders["sl"]["order_id"] == "1000000161375000"
        assert t.protection_orders["sl"]["type"] == "STOP_MARKET"
        assert t.protection_orders["sl"]["stop_price"] == 0.07024
        assert t.protection_orders["tp"]["order_id"] == "1000000161375100"
        assert t.protection_orders["tp"]["type"] == "TAKE_PROFIT_MARKET"
        assert t.protection_orders["tp"]["stop_price"] == 0.07087

    @patch("trading.recovery_manager.cfg")
    def test_recover_existing_trade_gets_protection_orders(self, mock_cfg):
        """Mevcut trade restore edilirken protection_orders flat ID'lerle
        birlikte guncellenir — trail sonrasi replace iptal bulabilsin."""
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = self._make_rest()
        existing = ActiveTrade(
            symbol="DOGEUSDT",
            side="long",
            status=STATUS_ACTIVE,
            entry_bar_index=0,
            entry_price=0.07037,
            sl=0.07014,
            tp=0.07077,
            qty=26770,
            initial_sl=0.07014,
            initial_tp=0.07077,
            trailing_count=0,
        )
        active_trades = {"DOGEUSDT": existing}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["DOGEUSDT"],
            cfgs={"DOGEUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"DOGEUSDT": 0.0001},
        )

        asyncio.run(rm.recover_positions())

        assert existing["sl_order_id"] == "1000000161375000"
        assert existing["tp_order_id"] == "1000000161375100"
        assert existing["protection_orders"]["sl"]["order_id"] == "1000000161375000"
        assert existing["protection_orders"]["sl"]["type"] == "STOP_MARKET"
        assert existing["protection_orders"]["tp"]["order_id"] == "1000000161375100"
        assert existing["protection_orders"]["tp"]["type"] == "TAKE_PROFIT_MARKET"

    @patch("trading.recovery_manager.cfg")
    def test_new_trade_lock_key_matches_exit_lifecycle(self, mock_cfg):
        """P0-1 kilit uyumlulugu (yeni trade dali): recovery'nin aktif trade'e
        yazdigi trade icin urettigi lock key, exit_lifecycle/bot.py'nin AYNI trade
        objesi uzerinde uretecegi key ile birebir ayni olmali ve ayni asyncio.Lock
        nesnesine karsilik gelmeli. Eski kod sym bazli RLock kullaniyordu — exit
        tarafi {sym}_{trade_id} bazli asyncio.Lock kullandigi icin iki taraf hic
        cakismiyordu (her ikisi de 'kilitli' gorunup gercekte farkli kilitlerdeydi)."""
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = self._make_rest()
        exit_locks = {}
        active_trades = {}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["DOGEUSDT"],
            cfgs={"DOGEUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"DOGEUSDT": 0.0001},
            exit_locks=exit_locks,
        )

        asyncio.run(rm.recover_positions())

        t = active_trades["DOGEUSDT"]
        assert isinstance(t, ActiveTrade)
        # exit_lifecycle.execute() / bot.py ile birebir ayni formül:
        expected_key = f"DOGEUSDT_{_trade_identity_key(t)}"
        assert set(exit_locks.keys()) == {expected_key}

        # exit tarafi ayni trade objesiyle setdefault yaparsa AYNI kilidi alir.
        async def _parity_check():
            lock = exit_locks.setdefault(expected_key, asyncio.Lock())
            return lock is exit_locks[expected_key], isinstance(lock, asyncio.Lock)

        same_lock, is_async_lock = asyncio.run(_parity_check())
        assert is_async_lock
        assert same_lock

    @patch("trading.recovery_manager.cfg")
    def test_existing_trade_lock_key_matches_exit_lifecycle(self, mock_cfg):
        """P0-1 kilit uyumlulugu (existing dali): recovery mevcut trade'i restore
        ederken ayni trade objesi uzerinden key uretmeli — exit_lifecycle/bot.py
        o trade'i sonlandirirken ayni kilide kilitlenmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"

        from trading.recovery_manager import RecoveryManager

        rest = self._make_rest()
        existing = ActiveTrade(
            symbol="DOGEUSDT",
            side="long",
            status=STATUS_ACTIVE,
            entry_bar_index=0,
            entry_price=0.07037,
            sl=0.07014,
            tp=0.07077,
            qty=26770,
            initial_sl=0.07014,
            initial_tp=0.07077,
            trailing_count=0,
        )
        exit_locks = {}
        active_trades = {"DOGEUSDT": existing}
        rm = RecoveryManager(
            rest_client=rest,
            symbols=["DOGEUSDT"],
            cfgs={"DOGEUSDT": {"SL_ATR_MULT": 1.5, "TP_RR": 2.0}},
            states={},
            active_trades=active_trades,
            pl_callback=_pl_noop,
            atr_state={"DOGEUSDT": 0.0001},
            exit_locks=exit_locks,
        )

        asyncio.run(rm.recover_positions())

        # recovery'nin kilitledigi key, exit tarafinin ayni objeyle üretecegi key.
        expected_key = f"DOGEUSDT_{_trade_identity_key(active_trades['DOGEUSDT'])}"
        assert set(exit_locks.keys()) == {expected_key}
        assert isinstance(exit_locks[expected_key], asyncio.Lock)

        async def _parity_check():
            lock = exit_locks.setdefault(expected_key, asyncio.Lock())
            return lock is exit_locks[expected_key]

        assert asyncio.run(_parity_check())
