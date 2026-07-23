"""
test_order_manager.py — OrderManager: trailing update, repair, cleanup.
"""

import asyncio
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

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    @patch("trading.order_manager.log_event")
    async def test_sl_reject_logs_error_code(self, mock_log_event, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"_error_code": "-2011"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_ok"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        mock_log_event.assert_called_once()
        call_kwargs = mock_log_event.call_args[1]
        assert call_kwargs["error_code"] == "-2011"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    @patch("trading.order_manager.log_event")
    async def test_tp_reject_logs_error_code(self, mock_log_event, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_ok"})
        mock_rest.place_tp_order = AsyncMock(return_value={"_error_code": "-4005"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        mock_log_event.assert_called_once()
        call_kwargs = mock_log_event.call_args[1]
        assert call_kwargs["error_code"] == "-4005"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_sl_4005_falls_back_to_close_position(self, mock_cfg):
        """SL -4005 alindiginda closePosition=True denenmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(
            side_effect=[
                {"_error_code": "-4005"},
                {"algoId": "sl_close_ok"},
            ]
        )
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_ok"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        assert trade["sl_order_id"] == "sl_close_ok"
        close_call = mock_rest.place_stop_order.call_args_list[-1]
        assert close_call.kwargs.get("close_position") is True

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_sl_4005_close_position_fails_falls_to_split(self, mock_cfg):
        """-4005 + closePosition basarisizsa parcali denenmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(
            side_effect=[
                {"_error_code": "-4005"},
                {},
                {"algoId": "sl_split_ok"},
            ]
        )
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_ok"})
        mock_rest.cancel_order = AsyncMock(return_value={})
        mock_rest.get_max_qty = AsyncMock(return_value=100.0)
        mock_rest.apply_amount_precision = AsyncMock(side_effect=lambda sym, a: a)

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(qty=500.0)

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        assert trade["sl_order_id"] == "sl_split_ok"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_tp_4005_falls_back_to_close_position(self, mock_cfg):
        """TP -4005 alindiginda closePosition=True denenmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_ok"})
        mock_rest.place_tp_order = AsyncMock(
            side_effect=[
                {"_error_code": "-4005"},
                {"algoId": "tp_close_ok"},
            ]
        )
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade()

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)

        assert result is True
        assert trade["tp_order_id"] == "tp_close_ok"
        close_call = mock_rest.place_tp_order.call_args_list[-1]
        assert close_call.kwargs.get("close_position") is True

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_backoff_after_consecutive_failures(self, mock_cfg):
        """3 ardışık trailing basarisizligindan sonra backoff devreye girmeli."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={})
        mock_rest.place_tp_order = AsyncMock(return_value={})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl=100.0, tp=110.0, qty=1.0)

        for i in range(3):
            result = await mgr.update_trail_orders(
                "BTCUSDT", trade, 105.0, 115.0 + i, i + 1
            )
            assert result is False, f"attempt {i+1}: expected False"

        assert mgr._trail_failures.get("BTCUSDT", 0) == 3

        before = mgr._trail_failures.get("BTCUSDT", 0)
        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 120.0, 4)
        assert result is False
        assert mgr._trail_failures.get("BTCUSDT", 0) == before

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_backoff_resets_after_success(self, mock_cfg):
        """Basarili trailing'den sonra backoff sayaci sifirlanmali."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(side_effect=[{}, {"algoId": "sl_ok"}])
        mock_rest.place_tp_order = AsyncMock(side_effect=[{}, {"algoId": "tp_ok"}])
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl=100.0, tp=110.0, qty=1.0)

        result_fail = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)
        assert result_fail is False
        assert mgr._trail_failures.get("BTCUSDT", 0) == 1

        result_ok = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 116.0, 2)
        assert result_ok is True
        assert mgr._trail_failures.get("BTCUSDT", 0) == 0


class TestTpUnchangedNoChurn:
    """P0-7 regresyon: TP fiyati trailing sirasinda degismediginde (yalnizca
    SL trail ediyor, TP sabit RR'de kaliyor), update_trail_orders() ne
    tp_order_id'yi bos string ile ezmeli ne de hala gecerli olan eski TP
    emrini Binance'te iptal etmeli. Eski davranista bu, cancel<->repair
    penceresinde pozisyonu gercekten TP'siz birakiyordu."""

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_tp_price_unchanged_keeps_existing_tp_order_id(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        # TP fiyati degismedigi icin place_tp_order HIC cagrilmamali
        mock_rest.place_tp_order = AsyncMock(
            return_value={"algoId": "tp_SHOULD_NOT_BE_USED"}
        )
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        # tp=110.0 sabit kalacak, sadece SL trail ediyor (new_tp == mevcut tp)
        trade = _trade(side="long", sl=100.0, tp=110.0, tp_order_id="tp_still_valid")

        result = await mgr.update_trail_orders("BTCUSDT", trade, 105.0, 110.0, 1)

        assert result is True
        # SL normal sekilde trail etmis olmali
        assert trade["sl_order_id"] == "sl_new"
        # KRITIK: tp_order_id hala eski/gecerli id olmali, bos string DEGIL
        assert trade["tp_order_id"] == "tp_still_valid"
        # KRITIK: hala gecerli olan eski TP emri iptal EDILMEMELI
        cancelled_ids = [call.args[0] for call in mock_rest.cancel_order.call_args_list]
        assert "tp_still_valid" not in cancelled_ids
        # Yeni TP emri hic atilmamali (fiyat zaten dogru)
        mock_rest.place_tp_order.assert_not_called()

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_tp_price_unchanged_short_side(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "should_not_use"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(side="short", sl=98.0, tp=88.0, tp_order_id="tp_still_valid")

        result = await mgr.update_trail_orders("ETHUSDT", trade, 95.0, 88.0, 1)

        assert result is True
        assert trade["tp_order_id"] == "tp_still_valid"
        cancelled_ids = [call.args[0] for call in mock_rest.cancel_order.call_args_list]
        assert "tp_still_valid" not in cancelled_ids


class TestPrecisionResidualNoChurn:
    """P0-7 kok neden regresyonu: evaluate_trail() ham hedefi bir onceki
    cycle'da precision-rounded kaydedilmis mevcut sl/tp ile kiyaslar. Tick
    altinda kalan farklar precision uygulandiktan sonra sl/tp'yi fiilen hic
    degistirmez. Bu durumda update_trail_orders() emir atmamali/iptal
    etmemeli — aksi halde sonsuz churn (P1-11 log'unda gorulen desen)."""

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_both_unchanged_after_precision_skips_entirely(self, mock_cfg):
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        # apply_price_precision, ham hedefi mevcut sl/tp'ye yuvarliyor
        # (tick-size bu ince farki yutuyor) — SEIUSDT senaryosunun aynisi:
        # 0.045658 -> 0.0457 (mevcut sl ile ayni), 0.044958 -> 0.045 (mevcut tp ile ayni)
        mock_rest.apply_price_precision = AsyncMock(
            side_effect=lambda sym, p: round(p, 4)
        )
        mock_rest.place_stop_order = AsyncMock(
            return_value={"algoId": "should_not_use"}
        )
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "should_not_use"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(
            side="long",
            sl=0.045700,
            tp=0.045000,
            sl_order_id="sl_x",
            tp_order_id="tp_x",
        )

        # Ham (rounding-oncesi) hedef 0.045658 — ama precision sonrasi 0.045700'e
        # yuvarlaniyor, yani trade["sl"] ile AYNI. Gercek bir trail YOK.
        result = await mgr.update_trail_orders("SEIUSDT", trade, 0.045658, 0.044958, 7)

        assert result is False
        mock_rest.place_stop_order.assert_not_called()
        mock_rest.place_tp_order.assert_not_called()
        mock_rest.cancel_order.assert_not_called()
        # trailing_count'a hic dokunulmamali (state'te sahte artis olmamali)
        assert "trailing_count" not in trade
        # ID'ler dokunulmamis olmali
        assert trade["sl_order_id"] == "sl_x"
        assert trade["tp_order_id"] == "tp_x"

    @pytest.mark.asyncio
    @patch("trading.order_manager.cfg")
    async def test_sl_really_changed_still_proceeds(self, mock_cfg):
        """Guard yanlis pozitif vermemeli: SL gercekten degisiyorsa (precision
        sonrasi bile farkli) trailing normal calismali."""
        mock_cfg.BINANCE_API_KEY = "test_key"
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
        mock_rest.cancel_order = AsyncMock(return_value={})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(side="long", sl=100.0, tp=110.0)

        result = await mgr.update_trail_orders("BTCUSDT", trade, 101.0, 111.0, 1)

        assert result is True
        assert trade["sl_order_id"] == "sl_new"
        assert trade["tp_order_id"] == "tp_new"
        assert trade["trailing_count"] == 1


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


# ═══════════════════════════════════════════════════════════════════
# P0-3: repair_protection concurrency tests
# ═══════════════════════════════════════════════════════════════════


class TestRepairProtectionConcurrency:
    """Per-symbol asyncio.Lock ile eşzamanlı repair_protection çağrılarını
    doğrula (P0-3)."""

    @pytest.mark.asyncio
    async def test_concurrent_same_symbol_only_one_executes(self):
        """Aynı sembol için eşzamanlı 2 repair_protection çağrısı → sadece
        ilkinin REST çağrıları yapılmalı, ikincisi lock.locked() ile atlanmalı."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_max_qty = AsyncMock(return_value=1000.0)

        # İlk place_stop_order çağrısı hemen bitmesin diye event kullan
        import asyncio as _asyncio

        _blocker = _asyncio.Event()
        _call_count = 0

        async def _slow_place_stop(*args, **kwargs):
            nonlocal _call_count
            _call_count += 1
            await _blocker.wait()  # İlk çağrıyı blokla
            return {"algoId": "sl_ok"}

        mock_rest.place_stop_order = AsyncMock(side_effect=_slow_place_stop)
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_ok"})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", tp_order_id="")

        # İki eşzamanlı çağrı başlat
        async def _call_repair():
            await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=False)

        task1 = _asyncio.create_task(_call_repair())
        task2 = _asyncio.create_task(_call_repair())

        # Her iki task'ın da lock'a ulaşması için kısa bekle
        await _asyncio.sleep(0.05)

        # Blokeyi kaldır
        _blocker.set()
        await _asyncio.gather(task1, task2)

        # place_stop_order sadece 1 kez çağrılmalı (2. çağrı lock'ta atlandı)
        assert _call_count == 1, (
            f"Beklenen: 1, Gerçekleşen: {_call_count} — "
            "concurrent repair lock çalışmıyor"
        )

    @pytest.mark.asyncio
    async def test_sequential_same_symbol_both_execute(self):
        """Aynı sembol için SIRALI iki çağrı (biri bitince öbürü) → her ikisi
        de normal çalışmalı (lock kalıcı değil)."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_max_qty = AsyncMock(return_value=1000.0)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_ok"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_ok"})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade = _trade(sl_order_id="", tp_order_id="")

        # İlk çağrı
        await mgr.repair_protection("BTCUSDT", trade, has_sl=False, has_tp=False)
        assert trade["sl_order_id"] == "sl_ok"

        # İkinci çağrı (farklı trade objesi)
        trade2 = _trade(sl_order_id="", tp_order_id="")
        mock_rest.place_stop_order.return_value = {"algoId": "sl_ok2"}
        mock_rest.place_tp_order.return_value = {"algoId": "tp_ok2"}
        await mgr.repair_protection("BTCUSDT", trade2, has_sl=False, has_tp=False)
        assert trade2["sl_order_id"] == "sl_ok2"
        assert trade2["tp_order_id"] == "tp_ok2"

        # place_stop_order 2 kez çağrılmış olmalı
        assert mock_rest.place_stop_order.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_different_symbols_both_execute(self):
        """Farklı sembol için eşzamanlı çağrılar BLOKLANMAMALI (per-symbol
        lock, global lock değil)."""
        mock_rest = MagicMock()
        mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_max_qty = AsyncMock(return_value=1000.0)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_ok"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_ok"})

        mgr = OrderManager(rest_client=mock_rest, is_live=True)
        trade_a = _trade(sl_order_id="", tp_order_id="")
        trade_b = _trade(sl_order_id="", tp_order_id="")

        # İki farklı sembol için eşzamanlı çağrı
        async def _repair_a():
            await mgr.repair_protection("BTCUSDT", trade_a, has_sl=False, has_tp=False)

        async def _repair_b():
            await mgr.repair_protection("ETHUSDT", trade_b, has_sl=False, has_tp=False)

        await asyncio.gather(_repair_a(), _repair_b())

        # Her iki sembolün de onarımı yapılmış olmalı
        assert trade_a["sl_order_id"] == "sl_ok"
        assert trade_b["sl_order_id"] == "sl_ok"
        # place_stop_order 2 kez çağrılmış olmalı (her sembol için 1)
        assert mock_rest.place_stop_order.call_count == 2
