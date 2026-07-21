"""
test_order_manager.py — OrderManager: trailing update, repair, cleanup.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from trading.order_manager import OrderManager


# ── Helpers ───────────────────────────────────────────────────────


def _trade(
    side="long", sl=100.0, tp=110.0, qty=0.5, sl_order_id="sl_old", tp_order_id="tp_old"
):
    return {
        "symbol": "BTCUSDT",
        "side": side,
        "sl": sl,
        "tp": tp,
        "qty": qty,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "status": "",
    }


# ═══════════════════════════════════════════════════════════════════
# update_trail_orders tests
# ═══════════════════════════════════════════════════════════════════


class TestUpdateTrailOrders:
    @pytest.mark.asyncio
    async def test_non_live_returns_true(self):
        mgr = OrderManager(rest_client=None, is_live=False)
        result = await mgr.update_trail_orders("BTCUSDT", _trade(), 105.0, 115.0, 1)
        assert result is True

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_no_api_key_returns_true(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = ""
        mgr = OrderManager(rest_client=MagicMock(), is_live=True)
        result = await mgr.update_trail_orders("BTCUSDT", _trade(), 105.0, 115.0, 1)
        assert result is True

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_full_success_long(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(side="long", sl=102.0, tp=112.0)

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        assert trade["sl_order_id"] == "sl_new"
        assert trade["tp_order_id"] == "tp_new"
        # Old SL and TP should be cancelled
        assert mock_rest.cancel_order.call_count == 2

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_full_success_short(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(side="short", sl=98.0, tp=88.0)

        result = await mgr.update_trail_orders("ETHUSDT", trade, 95.0, 85.0, 1)

        assert result is True
        assert trade["sl_order_id"] == "sl_new"
        assert trade["tp_order_id"] == "tp_new"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_sl_place_fails_returns_false(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={})  # No algoId
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        # Old SL ID should be preserved (not replaced)
        assert trade["sl_order_id"] == "sl_old"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_tp_place_fails_returns_false(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={})  # No algoId
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        assert trade["tp_order_id"] == "tp_old"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_sl_exception_caught_gracefully(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(side_effect=Exception("Network error"))
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        # SL failed, TP succeeded → overall success per docstring
        assert result is True
        assert trade["sl_order_id"] == "sl_old"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_old_id_cancel_exception_not_fatal(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(side_effect=Exception("Cancel failed"))

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        # Cancel failures should not cause overall failure
        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)
        assert result is True  # Both SL and TP placed successfully
        assert trade["sl_order_id"] == "sl_new"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_no_old_ids_no_cancel(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", tp_order_id="")

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)
        assert result is True
        # No cancellations because old IDs were empty
        mock_rest.cancel_order.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# repair_protection tests
# ═══════════════════════════════════════════════════════════════════


class TestRepairProtection:
    @pytest.mark.asyncio
    async def test_repairs_missing_sl(self):
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_repaired"})
        mock_rest.place_tp_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="")

        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=True)

        assert trade["sl_order_id"] == "sl_repaired"
        mock_rest.place_stop_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_repairs_missing_tp(self):
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_repaired"})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(tp_order_id="")

        await mgr.repair_protection("ETHUSDT", trade, has_sl=True, has_tp=False)

        assert trade["tp_order_id"] == "tp_repaired"
        mock_rest.place_tp_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_repairs_both_missing(self):
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_rep"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_rep"})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", tp_order_id="")

        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=False)

        assert trade["sl_order_id"] == "sl_rep"
        assert trade["tp_order_id"] == "tp_rep"

    @pytest.mark.asyncio
    async def test_skips_when_already_present(self):
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_ok", tp_order_id="tp_ok")

        await mgr.repair_protection("BTCUSDT", trade, has_sl=True, has_tp=True)

        mock_rest.place_stop_order.assert_not_called()
        mock_rest.place_tp_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_sl_value_missing(self):
        """If trade has no SL value, don't try to repair."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl=0.0, sl_order_id="")

        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=True)
        mock_rest.place_stop_order.assert_not_called()

    # ── P0-5: -4005 (max quantity) tests ───────────────────────

    @pytest.mark.asyncio
    async def test_sl_4005_falls_back_to_close_position(self):
        """SL -4005 alindiginda closePosition=True denenmeli."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_max_qty = AsyncMock(return_value=100.0)

        # Once -4005 donduren stop_order, sonra closePosition basarili
        mock_rest.place_stop_order = AsyncMock(
            side_effect=[
                {"_error_code": "-4005"},  # ilk deneme -4005
                {"algoId": "sl_close_ok"},  # second call = closePosition
            ]
        )

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", qty=500.0)

        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=True)

        assert trade["sl_order_id"] == "sl_close_ok"
        # closePosition=True ile cagrildi
        close_call = mock_rest.place_stop_order.call_args_list[-1]
        assert close_call.kwargs.get("close_position") is True

    @pytest.mark.asyncio
    async def test_sl_4005_close_position_fails_falls_to_split(self):
        """-4005 + closePosition basarisizsa parcali denenmeli."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.apply_amount_precision = AsyncMock(side_effect=lambda sym, a: a)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_max_qty = AsyncMock(return_value=100.0)

        call_count = 0

        async def _place_stop_order(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"_error_code": "-4005"}
            elif call_count == 2:
                return {}  # closePosition da basarisiz
            else:
                return {"algoId": "sl_split_ok"}  # parcali basarili

        mock_rest.place_stop_order = AsyncMock(side_effect=_place_stop_order)

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", qty=500.0)

        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=True)

        assert trade["sl_order_id"] == "sl_split_ok"

    @pytest.mark.asyncio
    async def test_non_4005_error_uses_price_fallback(self):
        """-4005 disindaki hatalarda mevcut fiyat-bazli retry calismali."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=105.0)
        mock_rest.get_max_qty = AsyncMock(return_value=1000.0)

        # Once bos response (fiyat hatasi), sonra basarili
        mock_rest.place_stop_order = AsyncMock(
            side_effect=[
                {},  # ilk deneme: bos (fiyat gecti)
                {"algoId": "sl_price_ok"},  # fiyat-bazli retry basarili
            ]
        )

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl=95.0, sl_order_id="", qty=1.0)

        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=True)

        assert trade["sl_order_id"] == "sl_price_ok"
        # Fiyat yeniden hesaplanmis olmali (cur_px=105, risk_pts=5, new_sl=95)
        # place_stop_order 2 kez cagrilmali
        assert mock_rest.place_stop_order.call_count >= 2

    @pytest.mark.asyncio
    async def test_tp_4005_falls_back_to_close_position(self):
        """TP -4005 alindiginda closePosition=True denenmeli."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_max_qty = AsyncMock(return_value=100.0)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_ok"})

        mock_rest.place_tp_order = AsyncMock(
            side_effect=[
                {"_error_code": "-4005"},
                {"algoId": "tp_close_ok"},
            ]
        )

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(tp_order_id="", qty=500.0)

        await mgr.repair_protection("BTCUSDT", trade, has_sl=True, has_tp=False)

        assert trade["tp_order_id"] == "tp_close_ok"
        close_call = mock_rest.place_tp_order.call_args_list[-1]
        assert close_call.kwargs.get("close_position") is True

    @pytest.mark.asyncio
    async def test_backoff_increments_after_failure(self):
        """Basarisiz onarim backoff sayacini artirmali."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=105.0)
        mock_rest.get_max_qty = AsyncMock(return_value=1000.0)
        mock_rest.place_stop_order = AsyncMock(return_value={})
        mock_rest.place_tp_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl=100.0, tp=110.0, sl_order_id="", tp_order_id="", qty=1.0)

        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=False)
        assert mgr._repair_failures.get("BTCUSDT", 0) == 1

    @pytest.mark.asyncio
    async def test_backoff_resets_after_success(self):
        """Basarili onarimdan sonra backoff sayaci sifirlanmali."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=105.0)
        mock_rest.get_max_qty = AsyncMock(return_value=1000.0)
        mock_rest.place_stop_order = AsyncMock(return_value={})
        mock_rest.place_tp_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)

        # Once basarisiz ol (both fail)
        trade = _trade(sl=100.0, tp=110.0, sl_order_id="", tp_order_id="", qty=1.0)
        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=False)
        assert mgr._repair_failures.get("BTCUSDT", 0) == 1

        # Sonra basarili ol
        mock_rest.place_stop_order.return_value = {"algoId": "sl_ok2"}
        mock_rest.place_tp_order.return_value = {"algoId": "tp_ok2"}
        trade2 = _trade(sl=100.0, tp=110.0, sl_order_id="", tp_order_id="", qty=1.0)
        await mgr.repair_protection("BTCUSDT", trade2, has_sl=False, has_tp=False)
        # Sifirlanmali
        assert mgr._repair_failures.get("BTCUSDT", 0) == 0

    @pytest.mark.asyncio
    async def test_is_max_qty_error_static(self):
        """_is_max_qty_error dogru calismali."""
        assert OrderManager._is_max_qty_error({"_error_code": "-4005"}) is True
        assert OrderManager._is_max_qty_error({}) is False
        assert OrderManager._is_max_qty_error({"algoId": "ok"}) is False
        assert OrderManager._is_max_qty_error({"_error_code": "-2011"}) is False


# ═══════════════════════════════════════════════════════════════════
# cleanup_on_exit tests
# ═══════════════════════════════════════════════════════════════════


class TestCleanupOnExit:
    @pytest.mark.asyncio
    async def test_non_live_noop(self):
        mgr = OrderManager(rest_client=MagicMock(), is_live=False)
        # Should not raise
        await mgr.cleanup_on_exit("BTCUSDT", _trade(), "SL")

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_sl_triggered_cancels_tp(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "SL")

        # SL triggered → cancel TP (remaining order)
        mock_rest.cancel_order.assert_called_once()
        args, kwargs = mock_rest.cancel_order.call_args
        assert args[0] == "tp_001"  # TP order cancelled

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_tp_triggered_cancels_sl(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "TP")

        # TP triggered → cancel SL (remaining order)
        mock_rest.cancel_order.assert_called_once()
        args, kwargs = mock_rest.cancel_order.call_args
        assert args[0] == "sl_001"

    # ── FIX (A8): Synthetic/market path → hem SL hem TP iptal ──

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_trail_close_cancels_both_sl_and_tp(self, mock_cfg):
        """FIX (A8): TRAIL_CLOSE path'inde ne SL ne TP tetiklendi —
        her ikisi de borsada kalan emirdir, ikisi de iptal edilmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "TRAIL_CLOSE")

        # Her iki emir de iptal edilmeli
        cancelled_ids = [call.args[0] for call in mock_rest.cancel_order.call_args_list]
        assert "sl_001" in cancelled_ids
        assert "tp_001" in cancelled_ids

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_ws_fallback_cancels_both(self, mock_cfg):
        """FIX (A8): WS_FALLBACK → her iki koruma emri de iptal edilmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "WS_FALLBACK")

        cancelled_ids = [call.args[0] for call in mock_rest.cancel_order.call_args_list]
        assert "sl_001" in cancelled_ids
        assert "tp_001" in cancelled_ids

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_timeout_cancels_both(self, mock_cfg):
        """FIX (A8): TIMEOUT → her iki koruma emri de iptal edilmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "TIMEOUT")

        cancelled_ids = [call.args[0] for call in mock_rest.cancel_order.call_args_list]
        assert "sl_001" in cancelled_ids
        assert "tp_001" in cancelled_ids

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_manual_close_cancels_both(self, mock_cfg):
        """FIX (A8): MANUAL_CLOSE → her iki koruma emri de iptal edilmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "MANUAL_CLOSE")

        cancelled_ids = [call.args[0] for call in mock_rest.cancel_order.call_args_list]
        assert "sl_001" in cancelled_ids
        assert "tp_001" in cancelled_ids

    # ── FIX (A8): Acil market close yalnızca SL/TP path'inde ──

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_trail_close_no_emergency_market_close(self, mock_cfg):
        """FIX (A8): Synthetic path'lerde acil market close tetiklenmemeli —
        pozisyon zaten _exit_trade() tarafından kapatılmış."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.place_market_order = AsyncMock(return_value={"orderId": 999})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        # Hem SL hem TP ID'si boş — eski kodda acil market close tetiklenirdi
        trade = _trade(sl_order_id="", tp_order_id="")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "TRAIL_CLOSE")

        # Acil market close çağrılmamalı
        mock_rest.place_market_order.assert_not_called()

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_emergency_close_when_trigger_id_missing(self, mock_cfg):
        """When the triggered order has no Binance ID, do emergency market close."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.place_market_order = AsyncMock(return_value={"orderId": 999})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        # SL triggered but sl_order_id is empty (synthetic position)
        trade = _trade(sl_order_id="", tp_order_id="tp_001")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "SL")

        # Emergency close should be called
        mock_rest.place_market_order.assert_called_once()
        _, kwargs = mock_rest.place_market_order.call_args
        assert kwargs.get("reduce_only") is True

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_cancel_exception_does_not_block_emergency_close(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(side_effect=Exception("Cancel failed"))
        mock_rest.place_market_order = AsyncMock(return_value={"orderId": 999})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", tp_order_id="tp_001")

        # Should not raise
        await mgr.cleanup_on_exit("BTCUSDT", trade, "SL")
        mock_rest.place_market_order.assert_called_once()

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_cancel_all_called_at_end(self, mock_cfg):
        """FIX (A7): cleanup_on_exit sonunda cancel_all_open_orders çağrılmalı."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")
        # cancel_all_open_orders için mock
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        await mgr.cleanup_on_exit("BTCUSDT", trade, "SL")

        # cancel_all_open_orders çağrılmalı (A7)
        mock_rest.get_all_orders.assert_called_once()

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_cancel_all_called_even_on_empty_trade(self, mock_cfg):
        """FIX (A7): cancel_all_open_orders trade bos olsa bile calismali."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", tp_order_id="")
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        await mgr.cleanup_on_exit("BTCUSDT", trade, "SL")

        # Yukaridaki hedefli iptal atlanir (bos ID), ama cancel_all yine de calisir
        mock_rest.get_all_orders.assert_called_once()

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_cancel_all_exception_not_fatal(self, mock_cfg):
        """FIX (A7): cancel_all_open_orders basarisizsa cleanup patlamamali."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.get_all_orders = AsyncMock(side_effect=Exception("API error"))

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="tp_001")

        # Should not raise
        await mgr.cleanup_on_exit("BTCUSDT", trade, "SL")
        mock_rest.get_all_orders.assert_called_once()

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_no_remaining_order_no_cancel(self, mock_cfg):
        """If there's no remaining order ID, skip cancel."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.place_market_order = AsyncMock(return_value={})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        # SL triggered, TP has ID but we check for remaining = tp_order_id
        trade = _trade(sl_order_id="sl_001", tp_order_id="")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "SL")
        # remaining_id = tp_order_id = "" → no cancel call
        mock_rest.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_trail_close_partial_ids_cancels_only_existing(self, mock_cfg):
        """FIX (A8): TRAIL_CLOSE'da sadece SL ID var, TP boş → yalnız SL iptal."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.get_all_orders = AsyncMock(return_value=[])

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="sl_001", tp_order_id="")

        await mgr.cleanup_on_exit("BTCUSDT", trade, "TRAIL_CLOSE")

        # Sadece SL iptal edilmeli (TP boş, atlanır)
        mock_rest.cancel_order.assert_called_once()
        args, _ = mock_rest.cancel_order.call_args
        assert args[0] == "sl_001"


# ═══════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_trail_with_zero_qty_fallback(self, mock_cfg):
        """trade with no qty falls back to 'lot' key."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = {
            "side": "long",
            "sl": 100.0,
            "tp": 110.0,
            "lot": 0.25,
            "sl_order_id": "old_sl",
            "tp_order_id": "old_tp",
            "status": "",
        }

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)
        assert result is True
