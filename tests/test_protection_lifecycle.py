"""
test_protection_lifecycle.py — ProtectionLifecycleService unit tests (Patch Set 3).

Saf policy servisi oldugu icin REST mock gerekmez. Tum testler sync.
"""

import pytest

from trading.protection_lifecycle import (
    ProtectionCheckResult,
    ProtectionLifecycleService,
    CleanupPlan,
)
from models import (
    ActiveTrade,
    STATUS_ACTIVE,
    STATUS_EXIT_VERIFYING,
    STATUS_REPAIR_REQUIRED,
    STATUS_TRAIL_REPLACING,
)


def _trade(**kw):
    base = dict(
        symbol="BTCUSDT",
        side="long",
        entry_price=50000.0,
        sl=49000.0,
        tp=52000.0,
        qty=0.1,
        sl_order_id="SL_ID_1",
        tp_order_id="TP_ID_1",
        sl_order_id_prev="",
        tp_order_id_prev="",
        sl_order_id_history=[],
        tp_order_id_history=[],
        pending_sl_order_id="",
        pending_tp_order_id="",
        status="ACTIVE",
    )
    base.update(kw)
    t = ActiveTrade(
        symbol=base["symbol"],
        side=base["side"],
        entry_price=base["entry_price"],
        sl=base["sl"],
        tp=base["tp"],
        qty=base["qty"],
        status=base.get("status", ""),
    )
    for k in (
        "sl_order_id",
        "tp_order_id",
        "sl_order_id_prev",
        "tp_order_id_prev",
        "pending_sl_order_id",
        "pending_tp_order_id",
    ):
        t[k] = base.get(k, "")
    t["sl_order_id_history"] = base.get("sl_order_id_history", [])
    t["tp_order_id_history"] = base.get("tp_order_id_history", [])
    return t


@pytest.fixture
def svc():
    return ProtectionLifecycleService()


# ═══════════════════════════════════════════════════════════════════
# known_ids() tests
# ═══════════════════════════════════════════════════════════════════


class TestKnownIds:
    def test_current_ids(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="TP_1")
        ids = svc.known_ids(t)
        assert "SL_1" in ids
        assert "TP_1" in ids

    def test_prev_ids(self, svc):
        t = _trade(
            sl_order_id="SL_CUR",
            tp_order_id="TP_CUR",
            sl_order_id_prev="SL_OLD",
            tp_order_id_prev="TP_OLD",
        )
        ids = svc.known_ids(t)
        assert ids == {"SL_CUR", "TP_CUR", "SL_OLD", "TP_OLD"}

    def test_pending_ids(self, svc):
        t = _trade(
            sl_order_id="SL_CUR",
            tp_order_id="TP_CUR",
            pending_sl_order_id="SL_PEND",
            pending_tp_order_id="TP_PEND",
        )
        ids = svc.known_ids(t)
        assert ids == {"SL_CUR", "TP_CUR", "SL_PEND", "TP_PEND"}

    def test_history_ids(self, svc):
        t = _trade(
            sl_order_id="SL_CUR",
            tp_order_id="TP_CUR",
            sl_order_id_history=["SL_H1", "SL_H2"],
            tp_order_id_history=["TP_H1"],
        )
        ids = svc.known_ids(t)
        assert ids == {"SL_CUR", "TP_CUR", "SL_H1", "SL_H2", "TP_H1"}

    def test_empty_strings_ignored(self, svc):
        t = _trade(sl_order_id="", tp_order_id="", pending_sl_order_id="")
        ids = svc.known_ids(t)
        assert ids == set()

    def test_none_treated_as_empty(self, svc):
        t = _trade()
        t["sl_order_id_prev"] = None
        t["pending_sl_order_id"] = None
        ids = svc.known_ids(t)
        assert ids == {"SL_ID_1", "TP_ID_1"}

    def test_all_sources_combined(self, svc):
        t = _trade()
        t["sl_order_id"] = "SL_C"
        t["tp_order_id"] = "TP_C"
        t["sl_order_id_prev"] = "SL_P"
        t["tp_order_id_prev"] = "TP_P"
        t["pending_sl_order_id"] = "SL_X"
        t["pending_tp_order_id"] = "TP_X"
        t["sl_order_id_history"] = ["SL_H1", "SL_H2"]
        t["tp_order_id_history"] = ["TP_H1"]
        ids = svc.known_ids(t)
        expected = {
            "SL_C",
            "TP_C",
            "SL_P",
            "TP_P",
            "SL_X",
            "TP_X",
            "SL_H1",
            "SL_H2",
            "TP_H1",
        }
        assert ids == expected


# ═══════════════════════════════════════════════════════════════════
# should_skip_reconcile() tests
# ═══════════════════════════════════════════════════════════════════


class TestShouldSkipReconcile:
    def test_active_does_not_skip(self, svc):
        t = _trade(status=STATUS_ACTIVE)
        assert svc.should_skip_reconcile(t) is False

    def test_empty_status_does_not_skip(self, svc):
        t = _trade(status="")
        assert svc.should_skip_reconcile(t) is False

    def test_exit_verifying_skips(self, svc):
        t = _trade(status=STATUS_EXIT_VERIFYING)
        assert svc.should_skip_reconcile(t) is True

    def test_repair_required_skips(self, svc):
        t = _trade(status=STATUS_REPAIR_REQUIRED)
        assert svc.should_skip_reconcile(t) is True

    def test_trail_replacing_skips(self, svc):
        t = _trade(status=STATUS_TRAIL_REPLACING)
        assert svc.should_skip_reconcile(t) is True


# ═══════════════════════════════════════════════════════════════════
# verify() tests
# ═══════════════════════════════════════════════════════════════════


class TestVerify:
    def test_both_present_and_healthy(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="TP_1", sl=49000, tp=52000)
        r = svc.verify(t, {"SL_1", "TP_1", "OTHER"})
        assert r.sl_present is True
        assert r.tp_present is True
        assert r.sl_healthy is True
        assert r.tp_healthy is True
        assert r.needs_repair is False
        assert r.all_healthy is True

    def test_both_missing(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="TP_1", sl=49000, tp=52000)
        r = svc.verify(t, {"OTHER"})
        assert r.sl_present is False
        assert r.tp_present is False
        assert r.sl_healthy is False
        assert r.tp_healthy is False
        assert r.needs_repair is True

    def test_sl_missing_tp_present(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="TP_1", sl=49000, tp=52000)
        r = svc.verify(t, {"TP_1"})
        assert r.sl_present is False
        assert r.tp_present is True
        assert r.needs_repair is True

    def test_sl_not_required_healthy(self, svc):
        t = _trade(sl_order_id="", tp_order_id="TP_1", sl=0, tp=52000)
        r = svc.verify(t, {"TP_1"})
        assert r.sl_present is False
        assert r.sl_healthy is True  # SL not expected
        assert r.tp_healthy is True
        assert r.needs_repair is False

    def test_tp_not_required_healthy(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="", sl=49000, tp=0)
        r = svc.verify(t, {"SL_1"})
        assert r.tp_present is False
        assert r.tp_healthy is True  # TP not expected
        assert r.sl_healthy is True
        assert r.needs_repair is False

    def test_empty_id_with_expectation_is_missing(self, svc):
        """A4: sl=49000 but sl_order_id="" → SL expected but missing."""
        t = _trade(sl_order_id="", tp_order_id="TP_1", sl=49000, tp=52000)
        r = svc.verify(t, {"TP_1"})
        assert r.sl_present is False
        assert r.sl_healthy is False
        assert r.needs_repair is True

    def test_both_not_required_both_healthy(self, svc):
        t = _trade(sl_order_id="", tp_order_id="", sl=0, tp=0)
        r = svc.verify(t, set())
        assert r.sl_healthy is True
        assert r.tp_healthy is True
        assert r.needs_repair is False


# ═══════════════════════════════════════════════════════════════════
# maybe_repair() tests
# ═══════════════════════════════════════════════════════════════════


class TestMaybeRepair:
    def test_no_repair_when_healthy(self, svc):
        check = ProtectionCheckResult(
            sl_present=True,
            tp_present=True,
            sl_healthy=True,
            tp_healthy=True,
            needs_repair=False,
            detail="healthy",
        )
        assert svc.maybe_repair(_trade(), check) is False

    def test_repair_when_needed(self, svc):
        check = ProtectionCheckResult(
            sl_present=False,
            tp_present=True,
            sl_healthy=False,
            tp_healthy=True,
            needs_repair=True,
            detail="SL eksik",
        )
        assert svc.maybe_repair(_trade(), check) is True

    def test_repair_when_tp_missing(self, svc):
        check = ProtectionCheckResult(
            sl_present=True,
            tp_present=False,
            sl_healthy=True,
            tp_healthy=False,
            needs_repair=True,
            detail="TP eksik",
        )
        assert svc.maybe_repair(_trade(), check) is True


# ═══════════════════════════════════════════════════════════════════
# cleanup_after_confirmed_exit() tests
# ═══════════════════════════════════════════════════════════════════


class TestCleanupAfterConfirmedExit:
    def test_sl_result_cancels_tp(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="TP_1")
        plan = svc.cleanup_after_confirmed_exit(t, "SL")
        assert plan.cancel_ids == ["TP_1"]
        assert plan.needs_emergency_close is False

    def test_tp_result_cancels_sl(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="TP_1")
        plan = svc.cleanup_after_confirmed_exit(t, "TP")
        assert plan.cancel_ids == ["SL_1"]
        assert plan.needs_emergency_close is False

    def test_synthetic_result_cancels_both(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="TP_1")
        for result in ("TRAIL_CLOSE", "WS_FALLBACK", "TIMEOUT", "MANUAL_CLOSE"):
            plan = svc.cleanup_after_confirmed_exit(t, result)
            assert set(plan.cancel_ids) == {"SL_1", "TP_1"}, f"Failed for {result}"
            assert plan.needs_emergency_close is False

    def test_sl_result_no_binance_id_triggers_emergency_close(self, svc):
        t = _trade(sl_order_id="", tp_order_id="TP_1")
        plan = svc.cleanup_after_confirmed_exit(t, "SL")
        assert plan.cancel_ids == ["TP_1"]
        assert plan.needs_emergency_close is True
        assert "SL" in plan.emergency_close_reason

    def test_tp_result_no_binance_id_triggers_emergency_close(self, svc):
        t = _trade(sl_order_id="SL_1", tp_order_id="")
        plan = svc.cleanup_after_confirmed_exit(t, "TP")
        assert plan.cancel_ids == ["SL_1"]
        assert plan.needs_emergency_close is True

    def test_synthetic_result_no_emergency_close(self, svc):
        t = _trade(sl_order_id="", tp_order_id="")
        plan = svc.cleanup_after_confirmed_exit(t, "TRAIL_CLOSE")
        assert plan.needs_emergency_close is False

    def test_empty_ids_filtered_out(self, svc):
        t = _trade(sl_order_id="", tp_order_id="")
        plan = svc.cleanup_after_confirmed_exit(t, "TRAIL_CLOSE")
        assert plan.cancel_ids == []


# ═══════════════════════════════════════════════════════════════════
# begin_replace / promote tests
# ═══════════════════════════════════════════════════════════════════


class TestReplaceAndPromote:
    def test_begin_replace_sl_sets_pending(self, svc):
        t = _trade(sl_order_id="SL_OLD")
        svc.begin_replace_sl(t, "SL_NEW")
        assert t["pending_sl_order_id"] == "SL_NEW"
        assert t["sl_order_id"] == "SL_OLD"  # not yet promoted

    def test_begin_replace_tp_sets_pending(self, svc):
        t = _trade(tp_order_id="TP_OLD")
        svc.begin_replace_tp(t, "TP_NEW")
        assert t["pending_tp_order_id"] == "TP_NEW"
        assert t["tp_order_id"] == "TP_OLD"

    def test_promote_sl_moves_pending_to_current(self, svc):
        t = _trade(sl_order_id="SL_OLD")
        t["pending_sl_order_id"] = "SL_NEW"
        svc.promote_sl(t)
        assert t["sl_order_id"] == "SL_NEW"
        assert t["sl_order_id_prev"] == "SL_OLD"
        assert t["pending_sl_order_id"] == ""
        assert "SL_OLD" in t["sl_order_id_history"]

    def test_promote_tp_moves_pending_to_current(self, svc):
        t = _trade(tp_order_id="TP_OLD")
        t["pending_tp_order_id"] = "TP_NEW"
        svc.promote_tp(t)
        assert t["tp_order_id"] == "TP_NEW"
        assert t["tp_order_id_prev"] == "TP_OLD"
        assert t["pending_tp_order_id"] == ""
        assert "TP_OLD" in t["tp_order_id_history"]

    def test_promote_sl_no_pending_is_noop(self, svc):
        t = _trade(sl_order_id="SL_CUR")
        svc.promote_sl(t)
        assert t["sl_order_id"] == "SL_CUR"
        assert t["sl_order_id_prev"] == ""

    def test_promote_tp_no_pending_is_noop(self, svc):
        t = _trade(tp_order_id="TP_CUR")
        svc.promote_tp(t)
        assert t["tp_order_id"] == "TP_CUR"
        assert t["tp_order_id_prev"] == ""

    def test_promote_sl_no_old_current(self, svc):
        t = _trade(sl_order_id="")
        t["pending_sl_order_id"] = "SL_NEW"
        svc.promote_sl(t)
        assert t["sl_order_id"] == "SL_NEW"
        assert t["sl_order_id_prev"] == ""
        assert t["pending_sl_order_id"] == ""

    def test_promote_tp_no_old_current(self, svc):
        t = _trade(tp_order_id="")
        t["pending_tp_order_id"] = "TP_NEW"
        svc.promote_tp(t)
        assert t["tp_order_id"] == "TP_NEW"
        assert t["tp_order_id_prev"] == ""
        assert t["pending_tp_order_id"] == ""

    def test_history_capped_at_5(self, svc):
        t = _trade(sl_order_id="SL_OLD")
        t["sl_order_id_history"] = ["H1", "H2", "H3", "H4", "H5"]
        t["pending_sl_order_id"] = "SL_NEW"
        svc.promote_sl(t)
        assert len(t["sl_order_id_history"]) == 5
        assert "SL_OLD" in t["sl_order_id_history"]
        assert "H1" not in t["sl_order_id_history"]

    def test_full_replace_flow(self, svc):
        """Simulate update_trail_orders flow: begin → promote."""
        t = _trade(sl_order_id="SL_CUR", tp_order_id="TP_CUR")
        svc.begin_replace_sl(t, "SL_PEND")
        svc.begin_replace_tp(t, "TP_PEND")
        assert t["pending_sl_order_id"] == "SL_PEND"
        assert t["pending_tp_order_id"] == "TP_PEND"
        # Promote
        svc.promote_sl(t)
        svc.promote_tp(t)
        assert t["sl_order_id"] == "SL_PEND"
        assert t["tp_order_id"] == "TP_PEND"
        assert t["sl_order_id_prev"] == "SL_CUR"
        assert t["tp_order_id_prev"] == "TP_CUR"
        assert "SL_CUR" in svc.known_ids(t)
        assert "TP_CUR" in svc.known_ids(t)
        assert "SL_PEND" in svc.known_ids(t)
        assert "TP_PEND" in svc.known_ids(t)


# ═══════════════════════════════════════════════════════════════════
# ProtectionCheckResult dataclass tests
# ═══════════════════════════════════════════════════════════════════


class TestProtectionCheckResult:
    def test_all_healthy_true(self):
        r = ProtectionCheckResult(
            sl_present=True,
            tp_present=True,
            sl_healthy=True,
            tp_healthy=True,
            needs_repair=False,
            detail="healthy",
        )
        assert r.all_healthy is True

    def test_all_healthy_false_when_sl_unhealthy(self):
        r = ProtectionCheckResult(
            sl_present=False,
            tp_present=True,
            sl_healthy=False,
            tp_healthy=True,
            needs_repair=True,
            detail="SL eksik",
        )
        assert r.all_healthy is False


# ═══════════════════════════════════════════════════════════════════
# CleanupPlan dataclass tests
# ═══════════════════════════════════════════════════════════════════


class TestCleanupPlan:
    def test_defaults(self):
        plan = CleanupPlan()
        assert plan.cancel_ids == []
        assert plan.needs_emergency_close is False
        assert plan.emergency_close_reason == ""

    def test_with_emergency(self):
        plan = CleanupPlan(
            cancel_ids=["ABC"],
            needs_emergency_close=True,
            emergency_close_reason="no binance id",
        )
        assert plan.cancel_ids == ["ABC"]
        assert plan.needs_emergency_close is True
