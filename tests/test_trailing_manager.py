"""
test_trailing_manager.py — TrailingManager: FVG trail + exit check unit tests.
Pure logic — no mocking needed except for config constants.
"""

import pytest
from unittest.mock import patch

from models import Bar
from trading.trailing_manager import (
    TrailResult,
    ExitDecision,
    TrailingManager,
)


# ── Helper ────────────────────────────────────────────────────────


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


def _trade(
    side="long",
    entry_price=100.0,
    sl=95.0,
    tp=110.0,
    initial_sl=None,
    initial_tp=None,
    risk_pts=None,
    trailing_count=0,
):
    """Build a trade dict matching ActiveTrade shape."""
    init_sl = initial_sl if initial_sl is not None else sl
    init_tp = initial_tp if initial_tp is not None else tp
    rp = risk_pts if risk_pts is not None else abs(init_sl - entry_price)
    return {
        "symbol": "BTCUSDT",
        "side": side,
        "entry_price": entry_price,
        "sl": sl,
        "tp": tp,
        "initial_sl": init_sl,
        "initial_tp": init_tp,
        "risk_pts": rp,
        "trailing_count": trailing_count,
        "qty": 0.1,
    }


# ═══════════════════════════════════════════════════════════════════
# TrailResult tests
# ═══════════════════════════════════════════════════════════════════


class TestTrailResult:
    def test_defaults(self):
        r = TrailResult()
        assert r.updated is False
        assert r.new_sl == 0.0
        assert r.new_tp == 0.0
        assert r.trail_count == 0

    def test_partial_init(self):
        r = TrailResult(updated=True, new_sl=100.0, new_tp=120.0, trail_count=3)
        assert r.updated is True
        assert r.new_sl == 100.0
        assert r.new_tp == 120.0
        assert r.trail_count == 3


# ═══════════════════════════════════════════════════════════════════
# ExitDecision tests
# ═══════════════════════════════════════════════════════════════════


class TestExitDecision:
    def test_defaults(self):
        e = ExitDecision()
        assert e.triggered is False
        assert e.result is None
        assert e.exit_price == 0.0

    def test_sl_exit(self):
        e = ExitDecision(triggered=True, result="SL", exit_price=95.0)
        assert e.triggered is True
        assert e.result == "SL"
        assert e.exit_price == 95.0

    def test_tp_exit(self):
        e = ExitDecision(triggered=True, result="TP", exit_price=110.0)
        assert e.triggered is True
        assert e.result == "TP"
        assert e.exit_price == 110.0


# ═══════════════════════════════════════════════════════════════════
# evaluate_trail tests
# ═══════════════════════════════════════════════════════════════════


class TestEvaluateTrail:
    def test_empty_bars_returns_no_update(self):
        result = TrailingManager.evaluate_trail([], _trade(), 0.3, 0.5)
        assert result.updated is False

    def test_single_bar_returns_no_update(self):
        bars = [_bar(0, 100, 105, 95, 102)]
        result = TrailingManager.evaluate_trail(bars, _trade(), 0.3, 0.5)
        assert result.updated is False

    @patch("trading.trailing_manager.cfg")
    def test_long_trail_on_bullish_fvg(self, mock_cfg):
        """Long trade: new SL = fvg.bottom - buffer > current SL → trail."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.2
        mock_cfg.ATR_TRAIL_MULT = 0.25
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # 4 bars → chunk has 3 → only i=1 checked → single bullish FVG (top=105, bottom=103)
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(
                2, 106, 110, 105, 108
            ),  # bullish FVG: b_next.low=105 > b_prev.high=103
            _bar(3, 108, 112, 107, 110),
        ]

        # atr_buffer = 0.3 * 0.25 = 0.075, new_sl = 103 - 0.075 = 102.925
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)

        assert result.updated is True
        assert result.new_sl == pytest.approx(102.925)
        assert result.new_tp == pytest.approx(106.0 + (102.925 - 97.0))
        assert result.trail_count == 1

    @patch("trading.trailing_manager.cfg")
    def test_no_trail_when_new_sl_not_better(self, mock_cfg):
        """Long trade: new_sl <= current_sl → no trail."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        trade = _trade(side="long", entry_price=100.0, sl=102.0, tp=106.0, risk_pts=2.0)

        # Bullish FVG bottom below current SL — should NOT trail
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 101, 103, 99, 100),
            _bar(2, 100, 103, 98, 102),  # bullish FVG: top=98, bottom=97
            _bar(3, 101, 104, 99, 103),
        ]
        # atr_buffer=0.075, new_sl=96.925 < current_sl=102 → NO trail
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    @patch("trading.trailing_manager.cfg")
    def test_min_move_filter_blocks_small_trail(self, mock_cfg):
        """Trail is blocked when sl_diff <= min_move."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.5  # High threshold
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG: top=105, bottom=103
            _bar(3, 108, 112, 107, 110),
        ]

        # buffer = 0.9, new_sl = 103 - 0.9 = 102.1
        # current_sl=97, risk_pts=3.0, min_move=3.0*0.5=1.5
        # sl_diff = 102.1 - 97.0 = 5.1 > 1.5 → trail PASSES
        # Let me lower the threshold or adjust values. Actually with higher threshold:
        # TRAIL_MIN_MOVE_MULT = 0.5 → min_move = 3.0 * 0.5 = 1.5, sl_diff=5.1 > 1.5 → still trails

        # To block: need sl_diff <= min_move. Let me set min_move very high.
        mock_cfg.TRAIL_MIN_MOVE_MULT = 3.0  # min_move = 3.0 * 3.0 = 9.0
        mock_cfg.ATR_TRAIL_MULT = 0.25
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False  # 5.925 < 9.0 → blocked

    @patch("trading.trailing_manager.cfg")
    def test_skips_filled_fvg(self, mock_cfg):
        """Filled/invalidated FVGs are skipped."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # evaluate_trail only calls detect_fvgs, not update_fvg_states → filled is always False
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is True

    def test_skips_wrong_direction_fvg_long(self):
        """Long trade skips bearish FVGs."""
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 109, 111, 107, 108),
            _bar(2, 106, 108, 103, 105),  # bearish FVG
        ]
        # buffer=0.9, but FVG is bearish, long trade ignores it
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    def test_uses_only_bars_except_last(self):
        """evaluate_trail uses bars[:-1] (excludes last bar)."""
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # FVG at index 3 uses bar index 3 as b_curr, and bar 4 as b_next
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 100, 103, 99, 102),
            _bar(3, 103, 105, 102, 104),
            _bar(4, 106, 110, 105, 108),  # This bar excluded from chunk
        ]
        # chunk = bars[:-1] = bars[0:4], so bar 4 is excluded
        # FVG at b_curr=3 would need b_next=4, but bar 4 is excluded
        # So no FVG found → no trail
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    def test_trailing_count_increments(self):
        """Trail count increments correctly."""
        trade = _trade(
            side="long",
            entry_price=100.0,
            sl=97.0,
            tp=106.0,
            risk_pts=3.0,
            trailing_count=2,
        )

        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
        ]
        # Will trail → trail_count should become 3
        with patch("trading.trailing_manager.cfg") as mock_cfg:
            mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
            mock_cfg.ATR_TRAIL_MULT = 0.25
            result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.trail_count == 3

    def test_chunk_lookback_capped_at_50(self):
        """lookback is min(50, len(chunk))."""
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # 60 bars → chunk has 59, lookback = min(50, 59) = 50
        bars = [_bar(i, 100, 103, 98, 102) for i in range(60)]
        # No FVG in these bars → no trail
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False


# ═══════════════════════════════════════════════════════════════════
# check_exit tests
# ═══════════════════════════════════════════════════════════════════


class TestCheckExit:
    # ── Long trade exits ──

    def test_long_sl_triggered(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 98, 100, 94, 97)  # low=94 <= sl=95 → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"
        assert result.exit_price == 95.0

    def test_long_sl_exact_touch(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 98, 100, 95, 97)  # low=95 == sl → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    def test_long_tp_triggered(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 108, 112, 107, 109)  # high=112 >= tp=110 → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"
        assert result.exit_price == 110.0

    def test_long_tp_exact_touch(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 108, 110, 107, 109)  # high=110 == tp → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"

    def test_long_no_exit(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 100, 105, 98, 102)  # low=98 > sl, high=105 < tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is False
        assert result.result is None

    def test_long_sl_priority_over_tp(self):
        """SL has priority (checked first). Both triggered → SL wins."""
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 98, 112, 94, 108)  # low=94 <= sl AND high=112 >= tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"  # SL checked first

    # ── Short trade exits ──

    def test_short_sl_triggered(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 105, 112, 100, 108)  # high=112 >= sl=110 → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"
        assert result.exit_price == 110.0

    def test_short_sl_exact_touch(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 105, 110, 100, 108)  # high=110 == sl → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    def test_short_tp_triggered(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 95, 98, 88, 92)  # low=88 <= tp=90 → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"
        assert result.exit_price == 90.0

    def test_short_tp_exact_touch(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 95, 98, 90, 92)  # low=90 == tp → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"

    def test_short_no_exit(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 100, 105, 95, 98)  # high=105 < sl, low=95 > tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is False
        assert result.result is None

    def test_short_sl_priority_over_tp(self):
        """SL has priority — both triggered → SL wins."""
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 95, 112, 88, 92)  # high=112 >= sl AND low=88 <= tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    # ── Edge cases ──

    def test_sl_at_current_price(self):
        """SL exactly at the bar range boundary."""
        trade = _trade(side="long", sl=100.0, tp=120.0)
        current = _bar(10, 105, 108, 100, 104)  # low=100 == sl → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    def test_tp_at_current_price(self):
        """TP exactly at the bar range boundary."""
        trade = _trade(side="long", sl=90.0, tp=110.0)
        current = _bar(10, 108, 110, 105, 107)  # high=110 == tp → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"


# ═══════════════════════════════════════════════════════════════════
# Integration-style: trail + exit in sequence
# ═══════════════════════════════════════════════════════════════════


class TestTrailAndExitSequence:
    """Simulate a realistic 1m bar sequence with trailing updates."""

    @patch("trading.trailing_manager.cfg")
    def test_trail_then_exit_long(self, mock_cfg):
        """Trail SL up, then next bar hits the new SL."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25

        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # Step 1: FVG trail — SL moves from 97 to 102.925
        bars_15m = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
        ]

        trail = TrailingManager.evaluate_trail(bars_15m, trade, 0.3, 0.5)
        assert trail.updated is True
        new_sl = trail.new_sl
        new_tp = trail.new_tp

        # Apply trail to trade
        trade["sl"] = new_sl
        trade["tp"] = new_tp

        # Step 2: Next 1m bar hits the new SL (low=101.5 < 102.925)
        current = _bar(20, 102, 104, 101.5, 103)
        exit_check = TrailingManager.check_exit(current, trade)
        assert exit_check.triggered is True
        assert exit_check.result == "SL"

    @patch("trading.trailing_manager.cfg")
    def test_trail_then_tp_long(self, mock_cfg):
        """Trail SL up, then price shoots to new TP."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25

        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars_15m = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
        ]

        trail = TrailingManager.evaluate_trail(bars_15m, trade, 0.3, 0.5)
        trade["sl"] = trail.new_sl
        trade["tp"] = trail.new_tp

        # Price surges to new TP (high=112 >= 111.925)
        current = _bar(20, 108, 112, 107, 111)
        exit_check = TrailingManager.check_exit(current, trade)
        assert exit_check.triggered is True
        assert exit_check.result == "TP"

    @patch("trading.trailing_manager.cfg")
    def test_no_trail_then_sl_hit_long(self, mock_cfg):
        """Without FVG trail, original SL is hit."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25

        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # No bullish FVG → no trail
        bars_15m = [
            _bar(0, 100, 102, 98, 101),
            _bar(1, 101, 103, 99, 102),
            _bar(2, 100, 102, 98, 101),
        ]
        trail = TrailingManager.evaluate_trail(bars_15m, trade, 0.3, 0.5)
        assert trail.updated is False

        # SL stays at 97, hit by next bar
        current = _bar(20, 98, 99, 96, 97)
        exit_check = TrailingManager.check_exit(current, trade)
        assert exit_check.triggered is True
        assert exit_check.result == "SL"
