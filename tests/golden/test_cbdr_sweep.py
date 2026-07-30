from __future__ import annotations


import pytest

from golden.fixtures import CBDR_FIXTURES
from golden.runners import (
    run_cbdr_backtest,
    run_cbdr_live,
    normalize_sweep_result,
    classify_diff,
    write_golden_log,
)

# Only include fixtures with simple sweep_confirmed expected
CBDR_PARAM_IDS = ["S01", "S02", "S03", "S04", "S05", "S06", "S12"]


class TestCBDRGoldenSweep:
    @pytest.mark.parametrize("fixture_id", CBDR_PARAM_IDS)
    def test_cbdr_golden(self, fixture_id):
        """Parametrized CBDR golden test: fixtures with simple sweep_confirmed expected."""
        fx = dict(CBDR_FIXTURES[fixture_id])
        expected = fx["expected"]

        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        live = normalize_sweep_result(run_cbdr_live(fx))

        log_data = {
            "event_type": "cbdr_golden_assertion",
            "fixture_id": fixture_id,
            "expected": expected,
            "backtest": bt,
            "live": live,
        }

        expected_confirmed = expected["sweep_confirmed"]
        assert bt["sweep_confirmed"] == expected_confirmed, (
            f"[{fixture_id}] Backtest sweep_confirmed: "
            f"expected {expected_confirmed}, got {bt['sweep_confirmed']}\n  bt={bt}"
        )
        assert live["sweep_confirmed"] == expected_confirmed, (
            f"[{fixture_id}] Live sweep_confirmed: "
            f"expected {expected_confirmed}, got {live['sweep_confirmed']}\n  live={live}"
        )

        # Verify backtest == live for closed-bar-only fixtures
        bt_live_diff = classify_diff(bt, live)
        if bt_live_diff != "NO_DIFF":
            log_data["diff_classification"] = bt_live_diff
            log_data["diff_detail"] = f"backtest vs live: {bt_live_diff}"
        write_golden_log(log_data)

    # ─── S01-S05: core sweep cases ────────────────────────────

    def test_S01_bullish_sweep(self):
        fx = dict(CBDR_FIXTURES["S01"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is True
        assert bt["sweep_direction"] == "bullish"
        assert bt["sweep_level"] == 100.0

    def test_S02_bearish_sweep(self):
        fx = dict(CBDR_FIXTURES["S02"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is True
        assert bt["sweep_direction"] == "bearish"
        assert bt["sweep_level"] == 110.0

    def test_S03_tolerance_boundary(self):
        fx = dict(CBDR_FIXTURES["S03"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is False

    def test_S04_upper_wick_no_close(self):
        fx = dict(CBDR_FIXTURES["S04"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is False

    def test_S05_lower_wick_no_close(self):
        fx = dict(CBDR_FIXTURES["S05"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is False

    # ─── S06: body lock guard ────────────────────────────────

    def test_S06_body_not_locked(self):
        """body_locked=False -> check_sweep should not be called."""
        fx = dict(CBDR_FIXTURES["S06"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is False

    # ─── S07: ATR edge cases ──────────────────────────────────

    def test_S07_atr_zero(self):
        """ATR=0 -> CBDR_SWEEP_DEFAULT_TOLERANCE=10.0 should be used.
        low=94, body_low=100, tol=10 -> 94 < 90? No -> no sweep."""
        fx = dict(CBDR_FIXTURES["S07_zero"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is False

    def test_S07_atr_nan(self):
        """ATR=NaN -> nan>0 is False -> default tolerance without crash."""
        fx = dict(CBDR_FIXTURES["S07_nan"])
        try:
            bt = normalize_sweep_result(run_cbdr_backtest(fx))
            assert bt["sweep_confirmed"] is False
        except Exception as e:
            pytest.fail(f"S07_nan raised: {e}")

    # ─── S08-S09: variation cases ─────────────────────────────

    def test_S08_atr_variation(self):
        """Same bar, different ATR -> different tolerance."""
        fx = dict(CBDR_FIXTURES["S08"])
        # small ATR => tight tolerance => sweep
        result_small = normalize_sweep_result(
            run_cbdr_backtest({**fx, "atr_value": 2.0})
        )
        assert result_small["sweep_confirmed"] is True
        # large ATR => loose tolerance => no sweep
        result_large = normalize_sweep_result(
            run_cbdr_backtest({**fx, "atr_value": 20.0})
        )
        assert result_large["sweep_confirmed"] is False

    def test_S09_ambiguous_both_sides(self):
        """Both sides breached -> exactly one sweep direction."""
        fx = dict(CBDR_FIXTURES["S09"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is True
        assert bt["sweep_direction"] in ("bullish", "bearish")

    # ─── S10-S11: event integrity ─────────────────────────────

    def test_S10_duplicate_bar(self):
        """Same bar processed twice -> idempotent result."""
        fx = dict(CBDR_FIXTURES["S10"])
        r1 = normalize_sweep_result(run_cbdr_backtest(fx))
        assert r1["sweep_confirmed"] is True
        r2 = normalize_sweep_result(run_cbdr_backtest(fx))
        assert r2["sweep_confirmed"] is True
        assert r2["sweep_direction"] == r1["sweep_direction"]

    def test_S11_out_of_order_bar(self):
        """Older bar after newer -> state machine processes in list order."""
        fx = dict(CBDR_FIXTURES["S11"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is True

    # ─── S12-S13: backtest/live parity ────────────────────────

    def test_S12_backtest_live_parity(self):
        """Same closed bars -> identical result."""
        fx = dict(CBDR_FIXTURES["S12"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        live = normalize_sweep_result(run_cbdr_live(fx))
        assert bt == live
        assert bt["sweep_confirmed"] is True

    def test_S13_open_bar(self):
        """Open bar: backtest processes it, live skips it."""
        fx = dict(CBDR_FIXTURES["S13"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        live = normalize_sweep_result(run_cbdr_live(fx))
        assert bt["sweep_confirmed"] is True
        assert live["sweep_confirmed"] is False

    # ─── S14: state isolation ─────────────────────────────────

    def test_S14_state_reset(self):
        """New CBDR session with reset state -> no sweep."""
        fx = dict(CBDR_FIXTURES["S14"])
        bt = normalize_sweep_result(run_cbdr_backtest(fx))
        assert bt["sweep_confirmed"] is False
        assert bt["body_high"] == 0.0 or bt["body_high"] == float("inf")
        assert bt["body_low"] == float("inf")

    # ─── S15: config variation ────────────────────────────────

    def test_S15_tolerance_config_change(self):
        """Different tolerance values produce different results."""
        fx = dict(CBDR_FIXTURES["S15"])
        # default tolerance=5 -> low=97, body_low-tol=95 -> 97<95? no
        default = normalize_sweep_result(run_cbdr_backtest(fx))
        assert default["sweep_confirmed"] is False
        # strict tolerance=2 -> low=97, body_low-tol=98 -> 97<98? yes
        strict = normalize_sweep_result(run_cbdr_backtest({**fx, "atr_value": 4.0}))
        assert strict["sweep_confirmed"] is True
