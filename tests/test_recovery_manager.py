"""
test_recovery_manager.py — RecoveryManager unit tests
───────────────────────────────────────────────────────
Kapsam: recover_positions closePosition fallback (06067c6)
Mock: BinanceRESTClient, cfg
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from models import STATUS_ACTIVE, ActiveTrade


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
