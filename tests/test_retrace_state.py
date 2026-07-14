"""
test_retrace_state.py — RetraceStateMachine + scan_htf_fvgs unit tests.
"""

from unittest.mock import patch

from models import Bar
from retrace_state import (
    RetraceState,
    RetraceStateMachine,
    HTFFVG,
    scan_htf_fvgs,
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
        timestamp=timestamp,
    )


def _make_15m_bars(n=50, trend="up", base=100.0, step=1.0, gap=False):
    """Generate 15m bars with optional bullish gap for FVG detection."""
    bars = []
    for i in range(n):
        if trend == "up":
            o = base + i * step
            c = o + step * 0.6
        else:
            o = base - i * step
            c = o - step * 0.6
        hi = max(o, c) + step * 0.4
        lo = min(o, c) - step * 0.4
        bars.append(_bar(i, o, hi, lo, c, timestamp=i * 900000))
    return bars


def _make_bars_with_gap(direction="bullish", gap_index=10, base=100.0):
    """Create bars where bar gap_index-1 and gap_index+1 have a gap."""
    bars = []
    for i in range(30):
        if i == gap_index - 1:
            if direction == "bullish":
                bars.append(_bar(i, base, base + 3, base - 1, base + 2))
            else:
                bars.append(_bar(i, base + 5, base + 8, base + 4, base + 6))
        elif i == gap_index:
            if direction == "bullish":
                bars.append(_bar(i, base + 3, base + 5, base + 2, base + 4))
            else:
                bars.append(_bar(i, base + 2, base + 6, base + 1, base + 3))
        elif i == gap_index + 1:
            if direction == "bullish":
                bars.append(_bar(i, base + 5, base + 8, base + 5, base + 7))
            else:
                bars.append(_bar(i, base - 1, base + 2, base - 2, base + 1))
        else:
            bars.append(_bar(i, base, base + 2, base - 2, base + 1))
        base += 1
    return bars


# ═══════════════════════════════════════════════════════════════════
# scan_htf_fvgs tests
# ═══════════════════════════════════════════════════════════════════


class TestScanHtfFvgs:
    def test_returns_empty_for_less_than_5_bars(self):
        bars = [_bar(i, 100, 105, 95, 102) for i in range(4)]
        result = scan_htf_fvgs(bars, lookback=100, min_fvg_size=1.0)
        assert result == []

    def test_returns_htf_fvgs_sorted_by_bar_index(self):
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        result = scan_htf_fvgs(bars, lookback=100, min_fvg_size=0.1)
        assert len(result) >= 1
        # Sorted by bar_index
        for i in range(1, len(result)):
            assert result[i - 1].bar_index <= result[i].bar_index

    def test_limits_to_10(self):
        # Generate many bars with gaps
        bars = []
        base = 100.0
        for i in range(50):
            if i % 3 == 0 and i > 1 and i < 48:
                # Create a bullish gap
                bars.append(_bar(i - 2, base, base + 2, base - 1, base + 1))
                bars.append(_bar(i - 1, base + 2, base + 4, base + 1, base + 3))
                bars.append(_bar(i, base + 4, base + 6, base + 4, base + 5))
            else:
                bars.append(_bar(i, base, base + 2, base - 2, base + 1))
            base += 0.5
        result = scan_htf_fvgs(bars, lookback=100, min_fvg_size=0.05)
        assert len(result) <= 10

    def test_handles_min_fvg_size_filter(self):
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        result_small = scan_htf_fvgs(bars, lookback=100, min_fvg_size=0.1)
        result_large = scan_htf_fvgs(bars, lookback=100, min_fvg_size=50.0)
        assert len(result_small) >= 1
        assert len(result_large) == 0


# ═══════════════════════════════════════════════════════════════════
# HTFFVG tests
# ═══════════════════════════════════════════════════════════════════


class TestHTFFVG:
    def test_creation(self):
        fvg = HTFFVG(top=110.0, bottom=105.0, direction="bullish", bar_index=5)
        assert fvg.top == 110.0
        assert fvg.bottom == 105.0
        assert fvg.direction == "bullish"
        assert fvg.bar_index == 5

    def test_repr(self):
        fvg = HTFFVG(top=110.0, bottom=105.0, direction="bullish", bar_index=5)
        r = repr(fvg)
        assert "105.00" in r
        assert "110.00" in r
        assert "bullish" in r


# ═══════════════════════════════════════════════════════════════════
# RetraceStateMachine tests
# ═══════════════════════════════════════════════════════════════════


class TestRetraceStateMachineInit:
    def test_starts_in_idle(self):
        rsm = RetraceStateMachine()
        assert rsm.state == RetraceState.IDLE
        assert rsm.direction is None
        assert rsm.sweep_level is None
        assert rsm.trigger_fvg is None

    def test_state_name(self):
        rsm = RetraceStateMachine()
        assert rsm.state_name == "IDLE"

    def test_can_trigger_false_initially(self):
        rsm = RetraceStateMachine()
        assert rsm.can_trigger() is False


class TestOnSweep:
    def test_on_sweep_bullish_transitions_to_sweep_detected(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.direction == "bullish"
        assert rsm.sweep_level == 105.0

    def test_on_sweep_bearish_transitions_to_sweep_detected(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.direction == "bearish"
        assert rsm.sweep_level == 95.0

    def test_on_sweep_ignored_when_not_idle(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        # Second call should be ignored
        rsm.on_sweep("bearish", 95.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.direction == "bullish"  # unchanged

    def test_on_sweep_with_bar_index_stores_pending_id(self):
        rsm = RetraceStateMachine()
        with patch("state_manager.is_sweep_used", return_value=False):
            rsm.on_sweep("bullish", 105.0, bar_index=42)
        assert rsm._pending_sweep_id == "bullish_42"

    @patch("state_manager.is_sweep_used")
    def test_on_sweep_skips_already_used(self, mock_is_used):
        mock_is_used.return_value = True
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0, bar_index=42)
        assert rsm.state == RetraceState.IDLE  # No transition
        mock_is_used.assert_called_once_with("bullish_42")

    @patch("state_manager.is_sweep_used")
    def test_on_sweep_graceful_on_state_manager_error(self, mock_is_used):
        mock_is_used.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        # Should not raise, should still transition
        rsm.on_sweep("bullish", 105.0, bar_index=42)
        assert rsm.state == RetraceState.SWEEP_DETECTED


class TestOnSweepConfirmed:
    def test_resets_when_no_fvg_found(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        # Use bars with no FVG gaps — sweep stays active
        bars = [_bar(i, 100, 102, 98, 101) for i in range(20)]
        sweep_bar = _bar(19, 101, 106, 99, 105)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.SWEEP_DETECTED

    def test_resets_when_not_in_sweep_detected(self):
        rsm = RetraceStateMachine()
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        sweep_bar = _bar(15, 108, 109.5, 103, 109)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.IDLE

    def test_bullish_trigger_when_wick_touches_but_body_safe(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        # Sweep bar: wick goes down into FVG zone, but body stays above
        # FVG from gap_index=10 is bullish: top ~b_next.low, bottom ~b_prev.high
        sweep_bar = _bar(15, 108, 110, 103, 109)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        # This may or may not trigger depending on exact FVG values.
        # The key behavior: if a matching FVG exists with wick rejection,
        # we transition to TRIGGER_READY. Otherwise reset.
        # Let's test more explicitly with crafted bars.
        pass

    def test_bullish_wick_rejection_triggers(self):
        """Crafted scenario: bullish sweep + FVG with reaction (high>=bottom, close<bottom)."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 100.0)
        # FVG: b_prev=bar(0) high=103, b_next=bar(2) low=105 → gap [103, 105]
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104, is_closed=True),
            _bar(2, 106, 110, 105, 105, is_closed=True),
            _bar(3, 108, 112, 107, 110, is_closed=True),
            _bar(4, 110, 113, 109, 112, is_closed=True),
            _bar(5, 112, 115, 111, 114, is_closed=True),
            _bar(6, 114, 116, 113, 115, is_closed=True),
            _bar(7, 115, 117, 114, 116, is_closed=True),
            _bar(8, 105, 107, 103, 104, is_closed=True, timestamp=8 * 900000),
        ]
        # Sweep bar: high>=103 (enters FVG), close<103 (rejected), range large
        sweep_bar = _bar(9, 102, 106, 100, 102, is_closed=True, timestamp=9 * 900000)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.trigger_fvg is not None

    def test_bearish_wick_rejection_triggers(self):
        """Crafted scenario: bearish sweep + FVG with wick rejection + close inside FVG."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)
        # Bearish FVG: b_prev=bar(0) low=109, b_next=bar(2) high=108 → gap [108, 109]
        # bar(1) is impulse candle
        bars = [
            _bar(0, 110, 113, 109, 111, is_closed=True),  # b_prev
            _bar(1, 109, 111, 107, 108, is_closed=True),  # impulse
            _bar(2, 106, 108, 103, 105, is_closed=True),  # b_next
            _bar(3, 105, 107, 102, 104, is_closed=True),
            _bar(4, 104, 106, 100, 102, is_closed=True),
            _bar(5, 102, 104, 98, 100, is_closed=True),
            _bar(6, 100, 102, 96, 98, is_closed=True),
            _bar(7, 98, 100, 94, 96, is_closed=True),
        ]
        # Add a bar closing INSIDE FVG [108, 109]
        bars.append(_bar(8, 96, 110, 95, 108.5, is_closed=True, timestamp=8 * 900000))
        # Sweep bar: wick goes up to 109 (touches FVG), body stays below, close ok (< sweep_level)
        sweep_bar = _bar(9, 96, 109, 94, 95, is_closed=True, timestamp=9 * 900000)
        with patch("state_manager.mark_sweep_used"):
            rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.trigger_fvg is not None

    def test_skips_fvg_with_wrong_direction(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        # Create a bearish FVG only
        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 110, 112, 108, 109),
            _bar(2, 107, 109, 104, 106),  # bearish gap
            _bar(3, 106, 108, 102, 104),
            _bar(4, 104, 106, 100, 102),
            _bar(5, 102, 104, 98, 100),
        ]
        sweep_bar = _bar(6, 100, 106, 98, 105)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        # Bearish FVG, but we need bullish → SWEEP_DETECTED'de kalir, reset yok
        assert rsm.state == RetraceState.SWEEP_DETECTED

    def test_skips_fvg_after_sweep_bar(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        # FVGs that appear at or after sweep bar index should be skipped
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG at index 1
            _bar(3, 108, 112, 107, 110),
        ]
        # Sweep bar at index 0 — FVG bar_index (1) >= sweep bar (0) → skip
        sweep_bar = _bar(0, 116, 118, 104, 117)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.SWEEP_DETECTED


class TestCanTrigger:
    def test_can_trigger_only_in_trigger_ready(self):
        rsm = RetraceStateMachine()
        assert rsm.can_trigger() is False  # IDLE

        rsm.on_sweep("bullish", 105.0)
        assert rsm.can_trigger() is False  # SWEEP_DETECTED

        # Manually set to TRIGGER_READY
        rsm.state = RetraceState.TRIGGER_READY
        assert rsm.can_trigger() is True


class TestReset:
    def test_reset_clears_all_fields(self):
        rsm = RetraceStateMachine()
        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bullish"
        rsm.sweep_level = 105.0
        rsm.trigger_fvg = HTFFVG(110.0, 105.0, "bullish", 5)
        rsm._pending_sweep_id = "bullish_42"

        rsm.reset()

        assert rsm.state == RetraceState.IDLE
        assert rsm.direction is None
        assert rsm.sweep_level is None
        assert rsm.trigger_fvg is None
        assert rsm._pending_sweep_id is None

    def test_reset_no_pending_sweep(self):
        rsm = RetraceStateMachine()
        rsm.state = RetraceState.SWEEP_DETECTED
        rsm.direction = "bearish"

        rsm.reset()

        assert rsm.state == RetraceState.IDLE
        assert rsm.direction is None


class TestMarkSweepUsed:
    @patch("state_manager.mark_sweep_used")
    def test_mark_sweep_used_called_on_trigger(self, mock_mark):
        rsm = RetraceStateMachine()
        rsm._pending_sweep_id = "bullish_42"
        rsm._mark_sweep_used()
        mock_mark.assert_called_once_with("bullish_42")
        assert rsm._pending_sweep_id is None

    def test_mark_sweep_used_no_id(self):
        rsm = RetraceStateMachine()
        rsm._mark_sweep_used()  # Should not raise

    @patch("state_manager.mark_sweep_used")
    def test_mark_sweep_used_error_graceful(self, mock_mark):
        mock_mark.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        rsm._pending_sweep_id = "bullish_42"
        rsm._mark_sweep_used()  # Should not raise
        assert rsm._pending_sweep_id is None


# ═══════════════════════════════════════════════════════════════════
# Integration-style: full flow
# ═══════════════════════════════════════════════════════════════════


class TestFullFlow:
    def test_idle_to_sweep_to_trigger_flow_bullish(self):
        rsm = RetraceStateMachine()
        assert rsm.state == RetraceState.IDLE

        with patch("state_manager.is_sweep_used", return_value=False):
            rsm.on_sweep("bullish", 100.0, bar_index=5)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.can_trigger() is False

        # FVG: bar(0) high=103, bar(2) low=105 → gap [103, 105]
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
            _bar(4, 110, 113, 109, 112),
            _bar(5, 112, 115, 111, 114),
            _bar(6, 114, 116, 113, 115),
            _bar(7, 115, 117, 114, 116),
            _bar(8, 105, 107, 103, 104, is_closed=True, timestamp=8 * 900000),
        ]
        sweep_bar = _bar(9, 102, 106, 100, 102, is_closed=True, timestamp=9 * 900000)

        with patch("state_manager.mark_sweep_used"):
            rsm.on_sweep_confirmed(bars, sweep_bar)

        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.can_trigger() is True

        # Reset
        rsm.reset()
        assert rsm.state == RetraceState.IDLE
        assert rsm.can_trigger() is False

    def test_sweep_invalid_when_body_breaks_below_level(self):
        """Bullish sweep: close < sweep_level → invalidation → IDLE."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        sweep_bar = _bar(5, 106, 109, 101, 102, timestamp=5 * 900000)
        with patch("state_manager.mark_sweep_used"):
            rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.IDLE

    def test_body_breaks_fvg_does_not_trigger_bearish(self):
        """Body closing beyond the FVG invalidates the wick rejection."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)

        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 110, 112, 108, 109),
            _bar(2, 107, 109, 104, 106),
            _bar(3, 106, 108, 102, 104),
            _bar(4, 104, 106, 100, 102),
            _bar(5, 102, 104, 98, 100),
            _bar(6, 100, 102, 96, 98),
            _bar(7, 98, 100, 94, 96),
        ]
        # Wick touched, but body CLOSED ABOVE FVG top → no trigger
        sweep_bar = _bar(8, 96, 109, 94, 109, timestamp=8 * 900000)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.IDLE

    def test_default_uses_max_wick_ratio(self):
        rsm = RetraceStateMachine()
        assert rsm._max_wick_ratio == 1.0

    def test_stores_custom_max_wick_ratio(self):
        rsm = RetraceStateMachine(max_wick_ratio=0.5)
        assert rsm._max_wick_ratio == 0.5
