"""
test_recovery_manager.py — RecoveryManager unit tests
───────────────────────────────────────────────────────
Kapsam: recover_positions closePosition fallback (06067c6)
Mock: BinanceRESTClient, cfg
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


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
        rest.place_market_order = AsyncMock(return_value=None)
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
        rest.place_market_order = AsyncMock(return_value={"orderId": 123})
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
        rest.place_market_order = AsyncMock(return_value=None)
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
        rest.place_market_order = AsyncMock(return_value=None)
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
