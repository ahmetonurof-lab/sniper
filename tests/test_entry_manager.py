"""
test_entry_manager.py — EntryManager: risk validation, position sizing,
SL/TP calc, live order execution.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models import Bar
from trading.entry_manager import (
    EntryExecutionResult,
    EntryManager,
)


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


def _mock_fvg(top=105.0, bottom=103.0, direction="bullish"):
    """Create a duck-typed FVG object for calculate_sl_tp."""
    fvg = MagicMock()
    fvg.top = top
    fvg.bottom = bottom
    fvg.direction = direction
    return fvg


def _mock_ss():
    """Create a minimal SessionState mock."""
    ss = MagicMock()
    ss.trades_today = 0
    return ss


# ═══════════════════════════════════════════════════════════════════
# EntryExecutionResult tests
# ═══════════════════════════════════════════════════════════════════


class TestEntryExecutionResult:
    def test_defaults(self):
        r = EntryExecutionResult(success=False)
        assert r.success is False
        assert r.qty == 0.0
        assert r.sl_order_id == ""
        assert r.tp_order_id == ""
        assert r.error == ""

    def test_success_with_ids(self):
        r = EntryExecutionResult(
            success=True, qty=0.5, sl_order_id="sl_123", tp_order_id="tp_456"
        )
        assert r.success is True
        assert r.qty == 0.5
        assert r.sl_order_id == "sl_123"
        assert r.tp_order_id == "tp_456"

    def test_failure_with_error(self):
        r = EntryExecutionResult(success=False, error="MARKET BASARISIZ")
        assert r.success is False
        assert r.error == "MARKET BASARISIZ"


# ═══════════════════════════════════════════════════════════════════
# validate_risk tests
# ═══════════════════════════════════════════════════════════════════


class TestValidateRisk:
    @patch("trading.entry_manager.cfg")
    def test_passes_when_risk_dist_above_min(self, mock_cfg):
        mock_cfg.MIN_RISK_DIST_ATR_MULT = 0.1
        valid, msg = EntryManager.validate_risk(risk_dist=5.0, atr_val=10.0)
        # min = 10.0 * 0.1 = 1.0, risk_dist=5.0 >= 1.0 → passes
        assert valid is True
        assert msg == ""

    @patch("trading.entry_manager.cfg")
    def test_fails_when_risk_dist_below_min(self, mock_cfg):
        mock_cfg.MIN_RISK_DIST_ATR_MULT = 0.1
        valid, msg = EntryManager.validate_risk(risk_dist=0.5, atr_val=10.0)
        # min = 1.0, risk_dist=0.5 < 1.0 → fails
        assert valid is False
        assert "risk_dist" in msg
        assert "min=" in msg

    @patch("trading.entry_manager.cfg")
    def test_exact_boundary_passes(self, mock_cfg):
        mock_cfg.MIN_RISK_DIST_ATR_MULT = 0.1
        valid, _ = EntryManager.validate_risk(risk_dist=1.0, atr_val=10.0)
        assert valid is True

    @patch("trading.entry_manager.cfg")
    def test_zero_atr_handled(self, mock_cfg):
        mock_cfg.MIN_RISK_DIST_ATR_MULT = 0.1
        # min = 0.0 * 0.1 = 0.0, risk_dist=1.0 >= 0.0 → passes
        valid, _ = EntryManager.validate_risk(risk_dist=1.0, atr_val=0.0)
        assert valid is True

    @patch("trading.entry_manager.cfg")
    def test_zero_risk_dist_fails(self, mock_cfg):
        mock_cfg.MIN_RISK_DIST_ATR_MULT = 0.1
        valid, _ = EntryManager.validate_risk(risk_dist=0.0, atr_val=10.0)
        assert valid is False


# ═══════════════════════════════════════════════════════════════════
# calculate_qty tests
# ═══════════════════════════════════════════════════════════════════


class TestCalculateQty:
    def test_normal_calculation(self):
        # balance=1000, risk_pct=0.01 (1%), risk_dist=5, leverage=10
        # qty = (1000 * 0.01) / 5 / 10 = 10 / 5 / 10 = 0.2
        qty = EntryManager.calculate_qty(
            balance=1000.0, risk_pct=0.01, risk_dist=5.0, leverage=10
        )
        assert qty == pytest.approx(0.2)

    def test_zero_balance(self):
        qty = EntryManager.calculate_qty(
            balance=0.0, risk_pct=0.01, risk_dist=5.0, leverage=10
        )
        assert qty == 0.0

    def test_zero_risk_dist_returns_zero(self):
        qty = EntryManager.calculate_qty(
            balance=1000.0, risk_pct=0.01, risk_dist=0.0, leverage=10
        )
        assert qty == 0.0

    def test_negative_risk_dist_returns_zero(self):
        qty = EntryManager.calculate_qty(
            balance=1000.0, risk_pct=0.01, risk_dist=-5.0, leverage=10
        )
        assert qty == 0.0

    def test_high_leverage_increases_qty(self):
        qty_10x = EntryManager.calculate_qty(1000.0, 0.01, 5.0, 10)
        qty_20x = EntryManager.calculate_qty(1000.0, 0.01, 5.0, 20)
        assert qty_20x == pytest.approx(qty_10x / 2)

    def test_large_risk_pct_increases_qty(self):
        qty_1pct = EntryManager.calculate_qty(1000.0, 0.01, 5.0, 10)
        qty_2pct = EntryManager.calculate_qty(1000.0, 0.02, 5.0, 10)
        assert qty_2pct == pytest.approx(qty_1pct * 2)

    def test_wide_risk_dist_decreases_qty(self):
        qty_narrow = EntryManager.calculate_qty(1000.0, 0.01, 5.0, 10)
        qty_wide = EntryManager.calculate_qty(1000.0, 0.01, 10.0, 10)
        assert qty_wide == pytest.approx(qty_narrow / 2)


# ═══════════════════════════════════════════════════════════════════
# calculate_sl_tp tests
# ═══════════════════════════════════════════════════════════════════


class TestCalculateSlTp:
    # ── Long with FVG ──

    def test_long_with_fvg_uses_fvg_bottom(self):
        fvg = _mock_fvg(top=105.0, bottom=103.0, direction="bullish")
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=fvg,
            london_high=115.0,
            london_low=100.0,
        )
        # sl = fvg.bottom - (risk_pts * fvg_buf) = 103.0 - (3.0 * 0.3) = 103.0 - 0.9 = 102.1
        assert sl == pytest.approx(102.1)
        # tp = london_high (115 > 108.0) = 115.0
        assert tp == pytest.approx(115.0)

    def test_long_without_fvg_uses_risk_fallback(self):
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
            london_high=115.0,
            london_low=100.0,
        )
        # sl = entry_price - risk_pts * 2 = 108.0 - 6.0 = 102.0
        assert sl == pytest.approx(102.0)
        # tp = london_high (115 > 108) = 115.0
        assert tp == pytest.approx(115.0)

    def test_long_tp_fallback_when_london_high_below_entry(self):
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
            london_high=105.0,
            london_low=100.0,
        )
        # london_high=105 < 108 → tp fallback = entry_price + risk_pts * tp_rr = 108 + 6 = 114.0
        assert tp == pytest.approx(114.0)

    # ── Short with FVG ──

    def test_short_with_fvg_uses_fvg_top(self):
        fvg = _mock_fvg(top=100.0, bottom=98.0, direction="bearish")
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=95.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=fvg,
            london_high=110.0,
            london_low=90.0,
        )
        # sl = fvg.top + (risk_pts * fvg_buf) = 100.0 + 0.9 = 100.9
        assert sl == pytest.approx(100.9)
        # tp = london_low (90 < 95) = 90.0
        assert tp == pytest.approx(90.0)

    def test_short_without_fvg_uses_risk_fallback(self):
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=95.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
            london_high=110.0,
            london_low=90.0,
        )
        # sl = entry_price + risk_pts * 2 = 95.0 + 6.0 = 101.0
        assert sl == pytest.approx(101.0)
        # tp = london_low (90 < 95) = 90.0
        assert tp == pytest.approx(90.0)

    def test_short_tp_fallback_when_london_low_above_entry(self):
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=95.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
            london_high=110.0,
            london_low=98.0,
        )
        # london_low=98 > 95 → tp fallback = entry_price - risk_pts * tp_rr = 95 - 6 = 89.0
        assert tp == pytest.approx(89.0)

    # ── Zero/edge values ──

    def test_long_zero_risk_pts(self):
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=100.0,
            risk_pts=0.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
            london_high=110.0,
            london_low=95.0,
        )
        # sl = 100.0 - 0.0 = 100.0
        assert sl == pytest.approx(100.0)

    def test_long_zero_london_high(self):
        """London high=0 means use fallback TP."""
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=100.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
            london_high=0.0,
            london_low=95.0,
        )
        # london_high=0 < entry_price=100 → tp = 100 + 6 = 106.0
        assert tp == pytest.approx(106.0)


# ═══════════════════════════════════════════════════════════════════
# execute_live_entry tests
# ═══════════════════════════════════════════════════════════════════


class TestExecuteLiveEntry:
    @pytest.mark.asyncio
    async def test_not_live_returns_success_with_qty(self):
        mgr = EntryManager(rest_client=None, is_live=False)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)
        assert result.success is True
        assert result.qty == 0.5

    async def _entry_mock_base(self):
        mock_rest = MagicMock()
        mock_rest.apply_amount_precision = AsyncMock(return_value=0.5)
        mock_rest.validate_min_amount = AsyncMock(return_value=0.5)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_min_notional = AsyncMock(return_value=5.0)
        mock_rest.get_step_size = AsyncMock(return_value=0.001)
        mock_rest.apply_price_precision = AsyncMock(side_effect=[99.9, 110.1])
        mock_rest.get_max_qty = AsyncMock(return_value=1000.0)
        return mock_rest

    @pytest.mark.asyncio
    async def test_live_success_path(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "0.5",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "50.0",
            }
        )
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_001"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_001"})

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is True
        assert result.sl_order_id == "sl_001"
        assert result.tp_order_id == "tp_001"
        assert result.qty == 0.5

    @pytest.mark.asyncio
    async def test_qty_below_min_rejected(self):
        mock_rest = MagicMock()
        mock_rest.apply_amount_precision = AsyncMock(return_value=0.001)
        mock_rest.validate_min_amount = AsyncMock(return_value=0.0)

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.001, 100.0, 110.0)

        assert result.success is False
        assert "minQty" in result.error

    @pytest.mark.asyncio
    async def test_market_order_failure(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(return_value={})  # No orderId → fail

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is False
        assert "MARKET BASARISIZ" in result.error

    @pytest.mark.asyncio
    async def test_sl_order_failure_triggers_emergency_close(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "0.5",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "50.0",
            }
        )
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": None})

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is False
        assert "SL BASARISIZ" in result.error
        # Emergency close should have been called (opposite side)
        # place_market_order called for entry + emergency close
        assert mock_rest.place_market_order.call_count == 2

    @pytest.mark.asyncio
    async def test_tp_failure_still_returns_success(self):
        """TP failure is non-fatal — execution still succeeds."""
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "0.5",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "50.0",
            }
        )
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_001"})
        mock_rest.place_tp_order = AsyncMock(return_value={})  # No algoId

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is True
        assert result.sl_order_id == "sl_001"
        assert result.tp_order_id == ""  # TP failed, but still success

    @pytest.mark.asyncio
    async def test_qty_clamped_to_max_qty(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.get_max_qty = AsyncMock(return_value=500.0)
        mock_rest.apply_amount_precision = AsyncMock(return_value=500.0)
        mock_rest.validate_min_amount = AsyncMock(return_value=500.0)
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "500",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "50000.0",
            }
        )
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_001"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_001"})

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 1000.0, 100.0, 110.0)

        assert result.success is True
        assert result.qty == 500.0
        mkt_call = mock_rest.place_market_order.call_args
        assert mkt_call.args[2] == 500.0

    @pytest.mark.asyncio
    async def test_max_qty_zero_skips_clamp(self):
        mock_rest = MagicMock()
        mock_rest.apply_amount_precision = AsyncMock(side_effect=lambda sym, q: q)
        mock_rest.validate_min_amount = AsyncMock(side_effect=lambda sym, q: q)
        mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
        mock_rest.get_min_notional = AsyncMock(return_value=5.0)
        mock_rest.get_step_size = AsyncMock(return_value=0.001)
        mock_rest.apply_price_precision = AsyncMock(side_effect=[99.9, 110.1])
        mock_rest.get_max_qty = AsyncMock(return_value=0.0)
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "1000",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "100000.0",
            }
        )
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_001"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_001"})

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 1000.0, 100.0, 110.0)

        assert result.success is True
        assert result.qty == 1000.0
        mkt_call = mock_rest.place_market_order.call_args
        assert mkt_call.args[2] == 1000.0

    @pytest.mark.asyncio
    async def test_clamp_below_min_qty_fails(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.get_max_qty = AsyncMock(return_value=500.0)
        mock_rest.apply_amount_precision = AsyncMock(return_value=500.0)
        mock_rest.validate_min_amount = AsyncMock(return_value=0.0)

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 1000.0, 100.0, 110.0)

        assert result.success is False
        assert "minQty altinda" in result.error
