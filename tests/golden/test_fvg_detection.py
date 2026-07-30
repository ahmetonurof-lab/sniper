from __future__ import annotations

import pytest

from golden.fixtures import FVG_FIXTURES
from golden.runners import (
    run_fvg_backtest,
    run_fvg_signal,
    run_fvg_trailing,
    normalize_fvgs,
)


FVG_IDS = [
    "F01",
    "F02",
    "F03",
    "F04",
    "F05",
    "F06",
    "F07",
    "F08",
    "F09",
    "F10",
    "F11",
    "F12",
    "F13",
    "F14",
    "F15",
    "F16",
    "F17",
    "F18",
    "F19",
    "F20",
]


class TestFVGGolden:
    @pytest.mark.parametrize("fixture_id", FVG_IDS)
    def test_fvg_golden(self, fixture_id):
        """Run all consumers on the fixture and compare to expected."""
        fx = dict(FVG_FIXTURES[fixture_id])
        expected = fx["expected"]

        bt = normalize_fvgs(run_fvg_backtest(fx))
        signal = normalize_fvgs(run_fvg_signal(fx))

        expected_count = expected.get("count", len(expected.get("fvgs", [])))
        assert (
            len(bt) == expected_count
        ), f"[{fixture_id}] backtest: expected {expected_count} FVGs, got {len(bt)}: {bt}"
        assert (
            len(signal) == expected_count
        ), f"[{fixture_id}] signal: expected {expected_count} FVGs, got {len(signal)}: {signal}"

        if bt and signal:
            assert (
                bt == signal
            ), f"[{fixture_id}] backtest != signal\n bt={bt}\n signal={signal}"

    # ─── F01-F02: basic valid FVGs ────────────────────────────

    def test_F01_valid_bullish_fvg(self):
        """Three-bar bullish gap produces one FVG"""
        fx = dict(FVG_FIXTURES["F01"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) == 1
        assert bt[0]["direction"] == "bullish"
        assert bt[0]["top"] == 107.0
        assert bt[0]["bottom"] == 105.0
        assert bt[0]["real_index"] == 1
        assert bt[0]["size"] == 2.0

    def test_F02_valid_bearish_fvg(self):
        """Three-bar bearish gap produces one FVG"""
        fx = dict(FVG_FIXTURES["F02"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) == 1
        assert bt[0]["direction"] == "bearish"
        assert bt[0]["top"] == 105.0
        assert bt[0]["bottom"] == 104.0
        assert bt[0]["real_index"] == 1

    # ─── F03-F06: size boundaries ─────────────────────────────

    def test_F03_gap_at_min_size(self):
        """Gap height == min_size -> included (boundary)"""
        fx = dict(FVG_FIXTURES["F03"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        # gap=2.0, min_size=2.0 -> included (size >= min_size)
        assert len(bt) == 1

    def test_F04_gap_below_min_size(self):
        """Gap height < min_size -> no FVG"""
        fx = dict(FVG_FIXTURES["F04"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        # gap=2.0, min_size=3.0 -> excluded
        assert len(bt) == 0

    def test_F05_zero_height(self):
        """No gap (overlap) -> no FVG"""
        fx = dict(FVG_FIXTURES["F05"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) == 0

    def test_F06_no_gap(self):
        """Overlapping bars -> no FVG"""
        fx = dict(FVG_FIXTURES["F06"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) == 0

    # ─── F07-F08: ATR edge cases ──────────────────────────────

    def test_F07_atr_zero(self):
        """min_fvg_size=0 means no filter, all gaps pass"""
        fx = dict(FVG_FIXTURES["F07"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        # min_size=0 means every gap passes size check
        # but detect_fvgs also checks is_closed on next bar
        assert len(bt) == 1, f"F07: expected 1 FVG (size=0 filter), got {len(bt)}"

    def test_F08_atr_nan(self):
        """min_fvg_size=0 passes everything through"""
        fx = dict(FVG_FIXTURES["F08"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) == 1, f"F08: expected 1 FVG (size=0 filter), got {len(bt)}"

    # ─── F09-F10: ordering and selection ──────────────────────

    def test_F09_multiple_fvgs_order(self):
        """Multiple FVGs are returned in chronological order"""
        fx = dict(FVG_FIXTURES["F09"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) >= 2
        # Check chronological order
        indices = [f["real_index"] for f in bt]
        assert indices == sorted(indices), f"FVGs not in chronological order: {indices}"

    def test_F10_last_fvg_selected(self):
        """Last (newest) FVG is selected by find_latest_unfilled_fvg"""
        fx = dict(FVG_FIXTURES["F10"])
        from fvg import find_latest_unfilled_fvg

        fvgs = run_fvg_backtest(fx)
        assert len(fvgs) >= 2
        latest = find_latest_unfilled_fvg(fvgs, "bullish")
        assert latest is not None
        # Latest by real_index
        max_idx = max(f.real_index for f in fvgs if f.direction == "bullish")
        assert (
            latest.real_index == max_idx
        ), f"latest FVG index {latest.real_index} != max {max_idx}"

    # ─── F11-F12: scope ──────────────────────────────────────

    def test_F11_entry_scope(self):
        """since_index filter limits FVGs to those after entry"""
        fx = dict(FVG_FIXTURES["F11"])
        bt_all = normalize_fvgs(run_fvg_backtest({**fx, "since_index": None}))
        bt_filtered = normalize_fvgs(run_fvg_backtest(fx))
        assert (
            len(bt_all) > len(bt_filtered)
        ), f"since_index filter should reduce count: {len(bt_all)} -> {len(bt_filtered)}"
        for f in bt_filtered:
            assert (
                f["real_index"] >= fx["since_index"]
            ), f"FVG at index {f['real_index']} is before since_index={fx['since_index']}"

    def test_F12_trailing_scope_empty(self):
        """since_index past all bars -> empty result"""
        fx = dict(FVG_FIXTURES["F12"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) == 0

    # ─── F13: timeframe consistency ───────────────────────────

    def test_F13_timeframe_consistency(self):
        """detect_fvgs with different timeframe labels produces same result"""
        fx = dict(FVG_FIXTURES["F13"])
        bt_15m = normalize_fvgs(run_fvg_backtest(fx))
        bt_5m = normalize_fvgs(run_fvg_backtest({**fx, "timeframe": "5m"}))
        # The label is just metadata, detection logic is the same
        assert len(bt_15m) == len(bt_5m)
        assert bt_15m[0]["direction"] == bt_5m[0]["direction"]
        assert bt_15m[0]["top"] == bt_5m[0]["top"]

    # ─── F14: open bar ────────────────────────────────────────

    def test_F14_unclosed_bar(self):
        """Last bar not closed -> its FVG (if any) is skipped"""
        fx = dict(FVG_FIXTURES["F14"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        # detect_fvgs skips the trio if the next bar (index 2) is not closed
        assert (
            len(bt) == 0
        ), f"F14: expected 0 FVGs with unclosed last bar, got {len(bt)}"

    # ─── F15-F16: event integrity ─────────────────────────────

    def test_F15_duplicate_bars(self):
        """Same bars processed twice -> same result, no duplicates"""
        fx = dict(FVG_FIXTURES["F15"])
        first = normalize_fvgs(run_fvg_backtest(fx))
        second = normalize_fvgs(run_fvg_backtest(fx))
        assert first == second, "repeat call should produce identical result"

    def test_F16_out_of_order(self):
        """Bars arriving out of order -> detect_fvgs processes by list order"""
        fx = dict(FVG_FIXTURES["F16"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        # detect_fvgs works on list order; out-of-order timestamps
        # won't change detection (it uses index, not timestamp)
        assert len(bt) > 0, "F16: expected FVGs from reordered bars"

    # ─── F17: symbol agnostic ─────────────────────────────────

    def test_F17_symbol_agnostic(self):
        """detect_fvgs is symbol-agnostic (no symbol param)"""
        fx = dict(FVG_FIXTURES["F17"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        assert len(bt) == 1, "same bars should produce same result regardless of symbol"

    # ─── F18: direction filter ────────────────────────────────

    def test_F18_direction_filter(self):
        """All FVGs, then filtered by direction"""
        fx = dict(FVG_FIXTURES["F18"])
        all_fvgs = run_fvg_backtest(fx)
        total = len(all_fvgs)
        assert total == 2, f"F18: expected 2 total FVGs, got {total}"

        bullish = [f for f in all_fvgs if f.direction == "bullish"]
        bearish = [f for f in all_fvgs if f.direction == "bearish"]
        assert len(bullish) == 1
        assert len(bearish) == 1

    # ─── F19-F20: cross-consumer ──────────────────────────────

    def test_F19_analyzer_vs_retrace(self):
        """Same bars -> same FVG result regardless of caller"""
        fx = dict(FVG_FIXTURES["F19"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        signal = normalize_fvgs(run_fvg_signal(fx))
        assert bt == signal, f"F19: backtest != signal\n  bt={bt}\n  signal={signal}"

    def test_F20_analyzer_vs_trailing(self):
        """Same bars -> trailing with same params gives same FVGs"""
        fx = dict(FVG_FIXTURES["F20"])
        bt = normalize_fvgs(run_fvg_backtest(fx))
        trailing = normalize_fvgs(run_fvg_trailing(fx))
        # Trailing uses lookback=50, backtest uses 100
        # Both should find the same FVGs for small bar sets
        assert (
            bt == trailing
        ), f"F20: backtest != trailing\n  bt={bt}\n  trailing={trailing}"
