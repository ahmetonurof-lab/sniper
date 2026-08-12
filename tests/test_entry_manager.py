"""
test_entry_manager.py — EntryManager: risk validation, position sizing,
SL/TP calc, live order execution.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from decimal import Decimal

from models import Bar
from trading.entry_manager import (
    EntryExecutionResult,
    EntryManager,
    InvalidProtectionLevel,
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
        # balance=1000, risk_pct=0.01 (1%), risk_dist=5, leverage=10, entry_price=100
        # qty = (1000 * 0.01) / 5 = 10 / 5 = 2.0
        qty = EntryManager.calculate_qty(
            balance=1000.0, risk_pct=0.01, risk_dist=5.0, leverage=10, entry_price=100.0
        )
        assert qty == pytest.approx(2.0)

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


class TestFvgHeightValid:
    def test_valid_fvg(self):
        fvg = _mock_fvg(top=105.0, bottom=103.0)
        assert EntryManager._fvg_height_valid(fvg) is True

    def test_none_fvg(self):
        assert EntryManager._fvg_height_valid(None) is False

    def test_fvg_height_zero(self):
        fvg = _mock_fvg(top=100.0, bottom=100.0)
        assert EntryManager._fvg_height_valid(fvg) is False

    def test_fvg_height_negative(self):
        fvg = _mock_fvg(top=99.0, bottom=100.0)
        assert EntryManager._fvg_height_valid(fvg) is False


class TestCalculateSlTp:
    # ── Long with FVG ──

    @patch("trading.entry_manager.cfg")
    def test_long_with_fvg_uses_fvg_bottom(self, mock_cfg):
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=105.0, bottom=103.0, direction="bullish")
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        # adaptive_buf = max(2.0 * 0.10, max(0.3, min(2.0*0.25, 3.0*0.3)))
        # = max(0.20, max(0.3, min(0.50, 0.90)))
        # = max(0.20, 0.50) = 0.50
        # raw_sl = 103.0 - 0.50 = 102.50
        # apply_min_sl_distance: min(102.50, 108 - 0.162) = min(102.50, 107.838) = 102.50
        # risk_dist = 108.0 - 102.50 = 5.50
        # tp = 108.0 + 5.50 * 2.0 = 119.0
        assert sl == pytest.approx(102.50)
        assert tp == pytest.approx(119.0)

    @patch("trading.entry_manager.cfg")
    def test_long_without_fvg_uses_risk_fallback(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
        )
        # raw_sl = 108.0 - 6.0 = 102.0
        # apply_min_sl_distance: min(102.0, 107.838) = 102.0
        # risk_dist = 108 - 102 = 6.0
        # tp = 108 + 12 = 120.0
        assert sl == pytest.approx(102.0)
        assert tp == pytest.approx(120.0)

    @patch("trading.entry_manager.cfg")
    def test_long_fvg_height_zero_fallback(self, mock_cfg):
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=103.0, bottom=103.0)  # height=0
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        # height=0 => fallback: raw_sl = 108 - 6 = 102.0
        assert sl == pytest.approx(102.0)
        assert tp == pytest.approx(120.0)

    @patch("trading.entry_manager.cfg")
    def test_long_fvg_height_negative_fallback(self, mock_cfg):
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=102.0, bottom=103.0)  # height=-1
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        assert sl == pytest.approx(102.0)
        assert tp == pytest.approx(120.0)

    # ── Short with FVG ──

    @patch("trading.entry_manager.cfg")
    def test_short_with_fvg_uses_fvg_top(self, mock_cfg):
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=100.0, bottom=98.0, direction="bearish")
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=95.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        # adaptive_buf = max(2.0*0.10, max(0.3, min(2.0*0.25, 3.0*0.3))) = max(0.20, 0.50) = 0.50
        # raw_sl = 100.0 + 0.50 = 100.50
        # apply_min_sl_distance: max(100.50, 95 + 0.1425) = max(100.50, 95.1425) = 100.50
        # risk_dist = 100.50 - 95.0 = 5.50
        # tp = 95.0 - 5.50*2.0 = 84.0
        assert sl == pytest.approx(100.50)
        assert tp == pytest.approx(84.0)

    @patch("trading.entry_manager.cfg")
    def test_short_without_fvg_uses_risk_fallback(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=95.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
        )
        # raw_sl = 95 + 6 = 101.0
        # apply_min_sl_distance: max(101.0, 95.1425) = 101.0
        # risk_dist = 101 - 95 = 6.0
        # tp = 95 - 12 = 83.0
        assert sl == pytest.approx(101.0)
        assert tp == pytest.approx(83.0)

    @patch("trading.entry_manager.cfg")
    def test_short_seiusdt_narrow_fvg_tick_floor_passes_validation(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_TICKS = 4
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        fvg = _mock_fvg(top=0.0414, bottom=0.0411, direction="bearish")
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=0.0414,
            risk_pts=0.00075,
            fvg_buf=0.5,
            tp_rr=1.8,
            trigger_fvg=fvg,
            tick_size=0.0001,
        )
        # Tick tabanı = 4 * 0.0001 = 0.0004, % tabanı = 0.000062
        # raw_sl = 0.0414 + 0.000075 = 0.041475 → 0.0414 + 0.0004 = 0.0418
        assert sl == pytest.approx(0.0418)
        valid_dir, dir_msg = EntryManager.validate_protection_with_actual_fill(
            "short", 0.0414, sl, tp, Decimal("0.0001"), epsilon_ticks=2
        )
        assert valid_dir, dir_msg

    @patch("trading.entry_manager.cfg")
    def test_short_fvg_height_zero_fallback(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=100.0, bottom=100.0)  # height=0
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=95.0,
            risk_pts=3.0,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        assert sl == pytest.approx(101.0)
        assert tp == pytest.approx(83.0)

    # ── Error cases ──

    def test_risk_pts_zero_raises(self):
        with pytest.raises(InvalidProtectionLevel):
            EntryManager.calculate_sl_tp(
                side="long",
                entry_price=100.0,
                risk_pts=0.0,
                fvg_buf=0.3,
                tp_rr=2.0,
                trigger_fvg=None,
            )

    def test_risk_pts_negative_raises(self):
        with pytest.raises(InvalidProtectionLevel):
            EntryManager.calculate_sl_tp(
                side="long",
                entry_price=100.0,
                risk_pts=-1.0,
                fvg_buf=0.3,
                tp_rr=2.0,
                trigger_fvg=None,
            )

    # ── apply_min_sl_distance changes SL → TP re-anchors ──

    @patch("trading.entry_manager.cfg")
    def test_short_apply_min_sl_distance_tp_reanchor(self, mock_cfg):
        """Short SL min distance expansion -> TP recalculated from expanded distance."""
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.02  # %2 = 2.0
        entry = 100.0
        # sl too close: we want raw_sl right at min boundary, so guard expands
        # For short, apply_min_sl_distance: max(sl, entry + min_dist)
        # If raw_sl = 100.5 and min_dist = 2.0, then min_sl = 102.0
        # So we need risk_pts * 2 raw sl that's < 102.0
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=entry,
            risk_pts=0.5,
            fvg_buf=0.3,
            tp_rr=2.0,
            trigger_fvg=None,
        )
        # raw_sl = 100.0 + 1.0 = 101.0 (risk_pts*2)
        # min_dist = 100 * 0.02 = 2.0
        # apply_min_sl_distance: max(101.0, 100+2.0) = max(101.0, 102.0) = 102.0
        # risk_dist = 102.0 - 100.0 = 2.0
        # tp = 100 - 2.0*2.0 = 96.0
        # If TP hadn't re-anchored, it would've been: 100 - 1.0*2.0 = 98.0
        assert sl == pytest.approx(102.0)
        assert tp == pytest.approx(96.0)

    # ── Wide FVG (no max_risk_dist cap) ──

    @patch("trading.entry_manager.cfg")
    def test_wide_fvg_no_max_risk_dist_cap(self, mock_cfg):
        """Genis FVG'de max_risk_dist override'i yok - SL dogrudan FVG bottom."""
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        wide_fvg = _mock_fvg(top=200.0, bottom=100.0, direction="bullish")
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=210.0,
            risk_pts=5.0,
            fvg_buf=0.5,
            tp_rr=2.0,
            trigger_fvg=wide_fvg,
        )
        # adaptive_buf = max(100*0.10, max(0.5, min(100*0.25, 5*0.5)))
        # = max(10.0, max(0.5, min(25.0, 2.5))) = max(10.0, 2.5) = 10.0
        # raw_sl = 100 - 10.0 = 90.0
        # apply_min_sl: min(90.0, 210 - 0.315) = 90.0
        # risk_dist = 210 - 90 = 120.0
        # tp = 210 + 120*2 = 450.0
        assert sl == pytest.approx(90.0)
        assert tp == pytest.approx(450.0)

    # ── fvg_buf = 0 ──

    @patch("trading.entry_manager.cfg")
    def test_fvg_buf_zero(self, mock_cfg):
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=105.0, bottom=103.0)
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.0,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        # adaptive_buf = max(2*0.10, max(0.3, min(2*0.25, 0))) = max(0.20, 0.3) = 0.30
        # raw_sl = 103 - 0.30 = 102.70
        # apply_min_sl: min(102.70, 107.838) = 102.70
        # risk_dist = 108 - 102.70 = 5.30
        # tp = 108 + 5.30*2 = 118.60
        assert sl == pytest.approx(102.70)
        assert tp == pytest.approx(118.60)

    # ── fvg_buf < 0 (should be treated as 0 for the rp2 term) ──

    @patch("trading.entry_manager.cfg")
    def test_fvg_buf_negative(self, mock_cfg):
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.10
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=105.0, bottom=103.0)
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=-0.1,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        # risk_pts * fvg_buf = -0.3, but min(fh*0.25, -0.3) = -0.3
        # max(risk_pts*0.1, -0.3) = max(0.3, -0.3) = 0.3
        # adaptive_buf = max(0.20, 0.3) = 0.30
        # same as fvg_buf=0
        assert sl == pytest.approx(102.70)

    @patch("trading.entry_manager.cfg")
    def test_fvg_buf_zero_min_no_rp2(self, mock_cfg):
        """When fhb=0 and fbm=0 and rp2 term is zero: adaptive_buf = max(fhm, rp1)"""
        mock_cfg.FVG_BUFFER_MIN_FACTOR = 0.0  # fhm = 0
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        fvg = _mock_fvg(top=105.0, bottom=103.0)
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=108.0,
            risk_pts=3.0,
            fvg_buf=0.0,
            tp_rr=2.0,
            trigger_fvg=fvg,
        )
        # adaptive_buf = max(0, max(0.3, min(0.5, 0))) = max(0, 0.3) = 0.30
        assert sl == pytest.approx(102.70)


# ═══════════════════════════════════════════════════════════════════
# P3-4: apply_min_sl_distance tests
# ═══════════════════════════════════════════════════════════════════


class TestApplyMinSlDistance:
    """MIN_SL_DISTANCE_PCT=%0.15 taban guard'ı doğrulaması."""

    @patch("trading.entry_manager.cfg")
    def test_long_sl_too_close_expands(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        entry = 100.0
        tight_sl = 99.95  # %0.05 mesafe
        result = EntryManager.apply_min_sl_distance(entry, tight_sl, "long")
        assert result == entry - entry * 0.0015  # 99.85
        assert result < tight_sl  # SL daha uzağa itildi

    @patch("trading.entry_manager.cfg")
    def test_long_sl_already_wide_unchanged(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        entry = 100.0
        wide_sl = 98.0  # %2 mesafe
        result = EntryManager.apply_min_sl_distance(entry, wide_sl, "long")
        assert result == wide_sl  # dokunulmaz

    @patch("trading.entry_manager.cfg")
    def test_short_sl_too_close_expands(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        entry = 100.0
        tight_sl = 100.05  # %0.05 mesafe
        result = EntryManager.apply_min_sl_distance(entry, tight_sl, "short")
        assert result == entry + entry * 0.0015  # 100.15
        assert result > tight_sl

    @patch("trading.entry_manager.cfg")
    def test_short_sl_already_wide_unchanged(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        entry = 100.0
        wide_sl = 102.0
        result = EntryManager.apply_min_sl_distance(entry, wide_sl, "short")
        assert result == wide_sl

    @patch("trading.entry_manager.cfg")
    def test_long_tick_floor_overrides_pct_when_tighter(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_TICKS = 4
        entry = 0.0414  # SEIUSDT gibi düşük fiyatlı sembol
        tight_sl = 0.0415  # 1 tick mesafe — % tabanı (0.000062) yetmiyor
        result = EntryManager.apply_min_sl_distance(
            entry, tight_sl, "short", tick_size=0.0001
        )
        # tick tabanı = 4 * 0.0001 = 0.0004 > % tabanı 0.000062
        assert result == entry + 0.0004  # 0.0418
        assert result > tight_sl

    @patch("trading.entry_manager.cfg")
    def test_short_tick_floor_expands_when_pct_insufficient(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_TICKS = 4
        entry = 0.5
        tight_sl = 0.5003
        result = EntryManager.apply_min_sl_distance(
            entry, tight_sl, "short", tick_size=0.001
        )
        # tick tabanı = 4 * 0.001 = 0.004 > % tabanı 0.00075
        assert result == entry + 0.004
        assert result > tight_sl

    @patch("trading.entry_manager.cfg")
    def test_wide_sl_unchanged_with_tick_floor(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_TICKS = 4
        entry = 0.0414
        wide_sl = 0.0430  # zaten 16 tick mesafe
        result = EntryManager.apply_min_sl_distance(
            entry, wide_sl, "short", tick_size=0.0001
        )
        assert result == wide_sl

    @patch("trading.entry_manager.cfg")
    def test_tick_floor_zero_keeps_pct_only(self, mock_cfg):
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_TICKS = 4
        entry = 100.0
        tight_sl = 99.95
        result = EntryManager.apply_min_sl_distance(
            entry, tight_sl, "long", tick_size=0.0
        )
        assert result == entry - entry * 0.0015  # 99.85 — eski davranış


# ═══════════════════════════════════════════════════════════════════
# Tick rounding tests (floor_to_tick / ceil_to_tick / round_sl_tp)
# ═══════════════════════════════════════════════════════════════════


class TestTickRounding:
    TICK = Decimal("0.01")

    def test_floor_to_tick_basic(self):
        assert EntryManager.floor_to_tick(1.236, self.TICK) == 1.23

    def test_floor_to_tick_exact(self):
        assert EntryManager.floor_to_tick(1.23, self.TICK) == 1.23

    def test_floor_to_tick_zero(self):
        assert EntryManager.floor_to_tick(0.0, self.TICK) == 0.0

    def test_ceil_to_tick_basic(self):
        assert EntryManager.ceil_to_tick(1.234, self.TICK) == 1.24

    def test_ceil_to_tick_exact(self):
        assert EntryManager.ceil_to_tick(1.23, self.TICK) == 1.23

    def test_round_sl_tp_long(self):
        sl, tp = EntryManager.round_sl_tp("long", 99.235, 110.784, self.TICK)
        assert sl == 99.23
        assert tp == 110.79

    def test_round_sl_tp_short(self):
        sl, tp = EntryManager.round_sl_tp("short", 100.235, 89.784, self.TICK)
        assert sl == 100.24
        assert tp == 89.78

    def test_round_sl_tp_half_tick(self):
        sl, tp = EntryManager.round_sl_tp("long", 100.005, 110.005, self.TICK)
        assert sl == 100.00
        assert tp == 110.01

    def test_round_sl_tp_small_tick(self):
        tick = Decimal("0.0001")
        sl, tp = EntryManager.round_sl_tp("long", 1.23456, 1.24567, tick)
        assert sl == 1.2345
        assert tp == 1.2457


# ═══════════════════════════════════════════════════════════════════
# validate_protection_with_actual_fill tests
# ═══════════════════════════════════════════════════════════════════


class TestValidateProtectionWithActualFill:
    TICK = Decimal("0.01")

    def test_long_valid(self):
        ok, _ = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=100.0,
            sl=99.8,
            tp=102.0,
            tick_size=self.TICK,
        )
        assert ok is True

    def test_long_sl_too_close(self):
        ok, msg = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=100.0,
            sl=99.99,
            tp=102.0,
            tick_size=self.TICK,
        )
        assert ok is False
        assert "SL" in msg

    def test_long_tp_too_close(self):
        ok, msg = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=100.0,
            sl=99.8,
            tp=100.01,
            tick_size=self.TICK,
        )
        assert ok is False
        assert "TP" in msg

    def test_short_valid(self):
        ok, _ = EntryManager.validate_protection_with_actual_fill(
            "short",
            actual_fill=100.0,
            sl=100.2,
            tp=98.0,
            tick_size=self.TICK,
        )
        assert ok is True

    def test_short_sl_too_close(self):
        ok, msg = EntryManager.validate_protection_with_actual_fill(
            "short",
            actual_fill=100.0,
            sl=100.01,
            tp=98.0,
            tick_size=self.TICK,
        )
        assert ok is False
        assert "SL" in msg

    def test_short_tp_too_close(self):
        ok, msg = EntryManager.validate_protection_with_actual_fill(
            "short",
            actual_fill=100.0,
            sl=100.2,
            tp=99.99,
            tick_size=self.TICK,
        )
        assert ok is False
        assert "TP" in msg

    def test_custom_epsilon(self):
        ok, _ = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=100.0,
            sl=99.95,
            tp=102.0,
            tick_size=self.TICK,
            epsilon_ticks=1,
        )
        # epsilon = 0.01 * 1 = 0.01
        # sl=99.95 < 100.0 - 0.01 = 99.99 -> True
        assert ok is True

    def test_long_fvg_clearance_rejected_after_actual_fill(self):
        """Fill sonrası SL FVG.sınırına eps'ten yakın → reddedilir.
        (BEFORE FIX: trigger_fvg parametresi yoktu, bu kontrol hiç yapılmıyordu.)"""
        fvg = _mock_fvg(top=105.0, bottom=103.0, direction="bullish")
        ok, msg = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=104.0,
            sl=102.99,
            tp=106.0,
            tick_size=Decimal("0.01"),
            epsilon_ticks=2,
            trigger_fvg=fvg,
        )
        # clearance = 103.0 - 102.99 = 0.01 < eps (0.02) → rejected
        assert ok is False
        assert "FVG.bottom" in msg

    def test_short_fvg_clearance_rejected_after_actual_fill(self):
        fvg = _mock_fvg(top=105.0, bottom=103.0, direction="bearish")
        ok, msg = EntryManager.validate_protection_with_actual_fill(
            "short",
            actual_fill=104.0,
            sl=105.01,
            tp=102.0,
            tick_size=Decimal("0.01"),
            epsilon_ticks=2,
            trigger_fvg=fvg,
        )
        # clearance = 105.01 - 105.0 = 0.01 < eps (0.02) → rejected
        assert ok is False
        assert "FVG.top" in msg

    def test_long_fvg_clearance_passes_after_actual_fill(self):
        """SL, FVG.sınırına eps'ten uzaksa geçer."""
        fvg = _mock_fvg(top=105.0, bottom=103.0, direction="bullish")
        ok, msg = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=104.0,
            sl=102.90,
            tp=106.0,
            tick_size=Decimal("0.01"),
            epsilon_ticks=2,
            trigger_fvg=fvg,
        )
        # clearance = 103.0 - 102.90 = 0.10 ≥ eps (0.02) → passes
        assert ok is True
        assert msg == ""

    def test_no_fvg_skip_clearance_check(self):
        """trigger_fvg None → sadece entry-eps kontrolü, FVG clearance atlanır."""
        ok, _ = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=100.0,
            sl=99.8,
            tp=102.0,
            tick_size=Decimal("0.01"),
            epsilon_ticks=2,
            trigger_fvg=None,
        )
        assert ok is True

    def test_fvg_invalid_skips_clearance_check(self):
        """FVG height <= 0 → clearance kontrolü atlanır."""
        fvg = _mock_fvg(top=103.0, bottom=103.0, direction="bullish")  # height=0
        ok, _ = EntryManager.validate_protection_with_actual_fill(
            "long",
            actual_fill=100.0,
            sl=99.0,
            tp=105.0,
            tick_size=Decimal("0.01"),
            epsilon_ticks=2,
            trigger_fvg=fvg,
        )
        assert ok is True


# ═══════════════════════════════════════════════════════════════════
# validate_pre_entry_protection tests (GENEL pre-entry guard)
# ═══════════════════════════════════════════════════════════════════


class TestValidatePreEntryProtection:
    """Tek genel kural: SL, FVG sınırına (long: fvg.bottom, short: fvg.top)
    tick-bazlı epsilon'dan yakınsa sinyal reddedilir — sembole özel koşul yok."""

    TICK = Decimal("0.01")

    def test_long_fvg_clearance_reject_ena_ticks(self):
        """ENA senaryosu (tick=0.001, eps=2 tick=0.002): SL, FVG.bottom'a
        0.0015 mesafede (< eps) → reddedilir. OLD guard (entry-eps) bunu
        yakalayamazdı çünkü SL entry'ye 0.0098 (≈10 tick) uzakta."""
        fvg = _mock_fvg(top=0.600, bottom=0.590)
        ok, msg = EntryManager.validate_pre_entry_protection(
            "long",
            entry_price=0.592,
            sl=0.5885,
            tp=0.612,
            tick_size=0.001,
            trigger_fvg=fvg,
            epsilon_ticks=2,
        )
        assert ok is False
        assert "FVG.bottom" in msg

    def test_short_fvg_clearance_reject_ena_ticks(self):
        """ENA senaryosu (tick=0.001, eps=2 tick=0.002): short SL, FVG.top'a
        0.0018 mesafede (< eps) → reddedilir."""
        fvg = _mock_fvg(top=0.600, bottom=0.590)
        ok, msg = EntryManager.validate_pre_entry_protection(
            "short",
            entry_price=0.592,
            sl=0.6018,
            tp=0.5724,
            tick_size=0.001,
            trigger_fvg=fvg,
            epsilon_ticks=2,
        )
        assert ok is False
        assert "FVG.top" in msg

    def test_short_fvg_clearance_pass_when_buffer_above_eps(self):
        """SL, FVG.top'a 3 tick (0.003) mesafede ≥ eps (0.002) → geçer."""
        fvg = _mock_fvg(top=0.600, bottom=0.590)
        ok, msg = EntryManager.validate_pre_entry_protection(
            "short",
            entry_price=0.592,
            sl=0.603,
            tp=0.5724,
            tick_size=0.001,
            trigger_fvg=fvg,
            epsilon_ticks=2,
        )
        assert ok is True
        assert msg == ""

    def test_long_fvg_clearance_pass_when_buffer_above_eps(self):
        fvg = _mock_fvg(top=0.600, bottom=0.590)
        ok, msg = EntryManager.validate_pre_entry_protection(
            "long",
            entry_price=0.592,
            sl=0.587,
            tp=0.612,
            tick_size=0.001,
            trigger_fvg=fvg,
            epsilon_ticks=2,
        )
        assert ok is True
        assert msg == ""

    def test_entry_price_eps_reject_preserved(self):
        """Eski guard davranışı korunur: SL, giriş fiyatına eps'ten yakınsa
        FVG clearance geniş olsa bile reddedilir."""
        fvg = _mock_fvg(top=105.0, bottom=103.0)
        ok, msg = EntryManager.validate_pre_entry_protection(
            "long",
            entry_price=100.0,
            sl=99.99,
            tp=102.0,
            tick_size=self.TICK,
            trigger_fvg=fvg,
            epsilon_ticks=2,
        )
        assert ok is False
        assert "SL" in msg

    def test_no_fvg_ok(self):
        ok, msg = EntryManager.validate_pre_entry_protection(
            "long",
            entry_price=100.0,
            sl=99.8,
            tp=102.0,
            tick_size=self.TICK,
            epsilon_ticks=2,
        )
        assert ok is True
        assert msg == ""

    def test_tick_zero_skips_all_checks(self):
        """tick_size bilinmiyorsa epsilon hesaplanamaz — guard atlanır."""
        fvg = _mock_fvg(top=105.0, bottom=103.0)
        ok, msg = EntryManager.validate_pre_entry_protection(
            "long",
            entry_price=100.0,
            sl=99.99,
            tp=100.01,
            tick_size=0.0,
            trigger_fvg=fvg,
            epsilon_ticks=2,
        )
        assert ok is True
        assert msg == ""

    def test_clearance_exactly_epsilon_passes(self):
        """Sınır: clearance == eps (2 tick) ise geçer (yalnızca altı reddedilir)."""
        fvg = _mock_fvg(top=0.600, bottom=0.590)
        ok, msg = EntryManager.validate_pre_entry_protection(
            "short",
            entry_price=0.592,
            sl=0.602,
            tp=0.5724,
            tick_size=0.001,
            trigger_fvg=fvg,
            epsilon_ticks=2,
        )
        assert ok is True
        assert msg == ""


# ═══════════════════════════════════════════════════════════════════
# _emergency_close tests
# ═══════════════════════════════════════════════════════════════════


class TestEmergencyClose:
    @pytest.mark.asyncio
    async def test_emergency_close_success(self):
        mock_rest = MagicMock()
        mock_rest.place_market_order = AsyncMock(return_value={"orderId": 999})
        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr._emergency_close("BTCUSDT", "BUY", 0.5, "reason test")
        assert result.success is True
        assert result.error == ""
        mock_rest.place_market_order.assert_called_once()
        call_kwargs = mock_rest.place_market_order.call_args.kwargs
        assert call_kwargs.get("reduce_only") is True

    @pytest.mark.asyncio
    async def test_emergency_close_failure(self):
        mock_rest = MagicMock()
        mock_rest.place_market_order = AsyncMock(side_effect=Exception("API timeout"))
        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr._emergency_close("BTCUSDT", "SELL", 0.5, "api error")
        assert result.success is False
        assert "BASARISIZ" in result.error

    @pytest.mark.asyncio
    async def test_emergency_close_invalid_side_raises(self):
        """BUG-7: mkt_side 'BUY'/'SELL' disinda ise ValueError."""
        mock_rest = MagicMock()
        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        with pytest.raises(ValueError):
            await mgr._emergency_close("BTCUSDT", "long", 0.5, "test")

    @pytest.mark.asyncio
    async def test_emergency_close_opposite_side_from_buy(self):
        mock_rest = MagicMock()
        mock_rest.place_market_order = AsyncMock(return_value={"orderId": 999})
        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        await mgr._emergency_close("BTCUSDT", "BUY", 0.5, "test")
        call_args = mock_rest.place_market_order.call_args.args
        assert call_args[1] == "SELL"

    @pytest.mark.asyncio
    async def test_emergency_close_opposite_side_from_sell(self):
        mock_rest = MagicMock()
        mock_rest.place_market_order = AsyncMock(return_value={"orderId": 999})
        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        await mgr._emergency_close("BTCUSDT", "SELL", 0.3, "test")
        call_args = mock_rest.place_market_order.call_args.args
        assert call_args[1] == "BUY"


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
        mock_rest.get_tick_size = AsyncMock(return_value=0.01)
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
    async def test_order_qty_precision_applied_to_sl_tp(self):
        """BUG-21: borsadan precision-uyumsuz actual_qty gelirse, SL/TP
        emirleri normalize edilmis order_qty ile gonderilir."""
        mock_rest = await self._entry_mock_base()
        # actual_qty 0.567 geliyor — apply_amount_precision 0.57'ye ceker
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "0.567",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "56.7",
            }
        )
        mock_rest.apply_amount_precision = AsyncMock(return_value=0.57)
        mock_rest.validate_min_amount = AsyncMock(return_value=0.57)
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_001"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_001"})

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is True
        sl_args = mock_rest.place_stop_order.call_args.args
        tp_args = mock_rest.place_tp_order.call_args.args
        assert sl_args[2] == 0.57  # SL normalize edilmis qty ile
        assert tp_args[2] == 0.57  # TP normalize edilmis qty ile

    @pytest.mark.asyncio
    async def test_order_qty_precision_below_min_falls_back_to_valid(self):
        """BUG-21: precision sonrasi min altina duserse valid_qty'ye donulur."""
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "0.567",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "56.7",
            }
        )
        mock_rest.apply_amount_precision = AsyncMock(return_value=0.0001)
        # ilk cagri entry valid_qty dogrulamasi (0.5 gecerli), ikinci cagri
        # order_qty dogrulamasi (min alti -> 0.0) — fallback valid_qty'ye donmeli
        mock_rest.validate_min_amount = AsyncMock(side_effect=[0.5, 0.0])
        mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_001"})
        mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_001"})

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is True
        sl_args = mock_rest.place_stop_order.call_args.args
        tp_args = mock_rest.place_tp_order.call_args.args
        assert sl_args[2] == 0.5  # valid_qty'ye geri donuldu
        assert tp_args[2] == 0.5

    @pytest.mark.asyncio
    async def test_market_order_failure(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(return_value={})  # No orderId → fail
        # 408/timeout gibi belirsiz durum: pozisyon kontrolü yapılır, pozisyon
        # yoksa (emir hiç gitmemiş) MARKET BASARISIZ döner.
        mock_rest.get_positions = AsyncMock(return_value=[])

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is False
        assert "MARKET BASARISIZ" in result.error

    @pytest.mark.asyncio
    async def test_market_empty_response_pos_open_emergency_close(self):
        """HTTP 408 / execution status unknown senaryosu: emir cevabı boş ama
        pozisyon aslında açılmış. Bot pozisyonu bulup emergency close ile
        güvenle kapatmalı — korumasız pozisyon kayıp edilmemeli."""
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(return_value={})  # empty_response
        mock_rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.5"}]
        )
        # emergency close market emri (opposite side)
        mock_rest.place_market_order.side_effect = [
            {},  # entry denemesi
            {"orderId": 999},  # emergency close
        ]

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is False
        assert "pozisyon acik" in result.error
        assert "pozisyon guvenle kapatildi" in result.error
        # entry + emergency close = 2 market emri
        assert mock_rest.place_market_order.call_count == 2
        close_side = mock_rest.place_market_order.call_args.args[1]
        assert close_side == "SELL"  # long girdi → SELL ile kapat

    @pytest.mark.asyncio
    async def test_market_qty_no_price_pos_open_emergency_close(self):
        """Köşe durumu: fill qty var ama avgPrice/quote yok (executedQty geldi,
        avgPrice/cumQuote gelmedi) → parse_market_fill=(qty, 0, 0), orderId de yok.
        Pozisyon aslında açılmışsa emergency close ile kapatılmalı — Blok A
        (price>0 şartı) ve eski Blok B (qty<=0 şartı) bu durumu kaçırıyordu."""
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(return_value={"executedQty": "0.5"})
        mock_rest.get_positions = AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "positionAmt": "0.5"}]
        )
        # emergency close market emri (opposite side)
        mock_rest.place_market_order.side_effect = [
            {"executedQty": "0.5"},  # entry denemesi — qty var, price yok
            {"orderId": 999},  # emergency close
        ]

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is False
        assert "pozisyon acik" in result.error
        assert "pozisyon guvenle kapatildi" in result.error
        # entry + emergency close = 2 market emri
        assert mock_rest.place_market_order.call_count == 2
        close_side = mock_rest.place_market_order.call_args.args[1]
        assert close_side == "SELL"  # long girdi → SELL ile kapat

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
        assert "SL FAIL" in result.error
        assert "pozisyon guvenle kapatildi" in result.error
        # Emergency close should have been called (opposite side)
        # place_market_order called for entry + emergency close
        assert mock_rest.place_market_order.call_count == 2

    @pytest.mark.asyncio
    async def test_tp_failure_returns_failure_with_emergency_close(self):
        """TP failure → success=False her zaman (emergency close sonucundan bağımsız).

        BUG-1 deseni: emergency close başarılı olsa bile entry başarısız sayılır;
        aksi halde bot.py:796 guard'ı atlanır ve acil kapatılmış pozisyon
        aktif trade olarak kaydedilir.
        """
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

        # TP başarısız → success her zaman False (entry başarısız oldu)
        assert result.success is False
        assert result.sl_order_id == "sl_001"
        assert result.tp_order_id is None  # TP failed
        assert "TP FAIL" in result.error

        # place_market_order called for entry + emergency close
        assert mock_rest.place_market_order.call_count == 2

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

    @pytest.mark.asyncio
    async def test_live_recalc_with_actual_fill(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.get_tick_size = AsyncMock(return_value=0.01)
        mock_rest.apply_price_precision = AsyncMock(side_effect=[96.0, 108.0])
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
        result = await mgr.execute_live_entry(
            "BTCUSDT",
            "long",
            0.5,
            100.0,
            110.0,
            risk_pts=2.0,
            fvg_buf=0.3,
            tp_rr=2.0,
        )

        assert result.success is True
        assert result.sl_order_id == "sl_001"
        assert result.tp_order_id == "tp_001"
        assert result.actual_price == 100.0

    @pytest.mark.asyncio
    async def test_live_no_recalc_path(self):
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
        result = await mgr.execute_live_entry(
            "BTCUSDT",
            "long",
            0.5,
            100.0,
            110.0,
            risk_pts=0.0,  # no recalc
        )
        assert result.success is True
        assert result.sl_order_id == "sl_001"
        assert result.tp_order_id == "tp_001"

    @pytest.mark.asyncio
    async def test_live_emergency_close_on_sl_fail(self):
        mock_rest = await self._entry_mock_base()
        mock_rest.place_market_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "executedQty": "0.5",
                "avgPrice": "100.0",
                "cummulativeQuoteQty": "50.0",
            }
        )
        mock_rest.place_stop_order = AsyncMock(return_value={"code": -2021})
        mock_rest.place_market_order.reset_mock()

        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr.execute_live_entry("BTCUSDT", "long", 0.5, 100.0, 110.0)

        assert result.success is False
        assert "SL FAIL" in result.error
        assert mock_rest.place_market_order.call_count == 2  # entry + emergency


# ═══════════════════════════════════════════════════════════════════
# _bump_to_min_notional tests (BUG-10)
# ═══════════════════════════════════════════════════════════════════


class TestBumpToMinNotional:
    async def _mgr(self, min_notional=5.0, step=0.01, max_qty=1000.0):
        mock_rest = MagicMock()
        mock_rest.get_min_notional = AsyncMock(return_value=min_notional)
        mock_rest.get_step_size = AsyncMock(return_value=step)
        return EntryManager(rest_client=mock_rest, is_live=True)

    @pytest.mark.asyncio
    async def test_already_valid_returns_qty(self):
        mgr = await self._mgr()
        result = await mgr._bump_to_min_notional("BTCUSDT", 0.1, 100.0, 1000.0, 10)
        assert result == 0.1

    @pytest.mark.asyncio
    async def test_edge_case_step_001_min_qty_1235(self):
        """BUG-10: step=0.01, min_qty_n=1.235 -> 1.24 (Decimal, doğru)."""
        # min_notional=5, price=4.049... -> min_qty_n=1.235
        mgr = await self._mgr(min_notional=5.0, step=0.01)
        result = await mgr._bump_to_min_notional("BTCUSDT", 1.0, 4.05, 1000.0, 10)
        assert result == 1.24

    @pytest.mark.asyncio
    async def test_float_floor_edge_avoided(self):
        """1.235/0.01 float'ta 123.49999 olabilir; Decimal ceil 124 verir."""
        mgr = await self._mgr(min_notional=5.0, step=0.01)
        result = await mgr._bump_to_min_notional("BTCUSDT", 1.0, 4.05, 1000.0, 10)
        assert result >= 1.235
        assert result == round(result / 0.01) * 0.01  # step uyumlu

    @pytest.mark.parametrize(
        "min_notional,price,step,expected",
        [
            (5.0, 100.0, 0.1, 0.1),  # notional 5.0 -> zaten gecerli
            (10.0, 100.0, 0.1, 0.1),  # 0.1*100=10 gecerli
            (10.0, 80.0, 0.1, 0.2),  # 0.1*80=8 < 10 -> 0.2*80=16
            (7.0, 55.0, 0.01, 0.13),  # 0.12*55=6.6 < 7 -> 0.13*55=7.15
        ],
    )
    @pytest.mark.asyncio
    async def test_parametrized_bump(self, min_notional, price, step, expected):
        mgr = await self._mgr(min_notional=min_notional, step=step)
        result = await mgr._bump_to_min_notional("BTCUSDT", 0.1, price, 1000.0, 10)
        assert result == expected
        assert result * price >= min_notional

    @pytest.mark.asyncio
    async def test_above_buying_power_returns_zero(self):
        mock_rest = MagicMock()
        mock_rest.get_min_notional = AsyncMock(return_value=5000.0)
        mock_rest.get_step_size = AsyncMock(return_value=1.0)
        mgr = EntryManager(rest_client=mock_rest, is_live=True)
        result = await mgr._bump_to_min_notional("BTCUSDT", 0.1, 100.0, 100.0, 1)
        assert result == 0.0
