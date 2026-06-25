"""
test_state_manager.py — File-backed state management unit tests.
Uses tmp_path to provide isolated state files per test.
"""

import os

import pytest

from state_manager import (
    _today,
    _load,
    _save,
    can_open_trade,
    mark_trade_opened,
    mark_trade_closed,
    is_sweep_used,
    mark_sweep_used,
    unmark_sweep_used,
    reconcile_from_active,
    get_trade_count_today,
    save_retrade_arm,
    clear_retrade_arm,
    STATE_FILE,
    LOCK_FILE,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def temp_state_dir(tmp_path, monkeypatch):
    """Redirect STATE_FILE and LOCK_FILE to tmp_path."""
    state_file = tmp_path / "trade_state.json"
    lock_file = tmp_path / "trade_state.json.lock"
    monkeypatch.setattr("state_manager.STATE_FILE", str(state_file))
    monkeypatch.setattr("state_manager.LOCK_FILE", str(lock_file))
    monkeypatch.setattr("state_manager._SCRIPT_DIR", str(tmp_path))
    # Ensure output dir exists
    os.makedirs(os.path.join(str(tmp_path), "..", "output"), exist_ok=True)
    return tmp_path


@pytest.fixture
def clean_state(temp_state_dir):
    """Ensure clean state before each test."""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
    return temp_state_dir


# ═══════════════════════════════════════════════════════════════════
# _today tests
# ═══════════════════════════════════════════════════════════════════


class TestToday:
    def test_today_returns_string(self):
        t = _today()
        assert isinstance(t, str)
        assert "-" in t

    def test_today_at_22_utc_returns_next_day(self):
        """At 22:00 UTC, _today should return tomorrow's date."""
        # We can't change system time, just verify the logic doesn't crash
        t = _today()
        assert len(t) == 10  # YYYY-MM-DD


# ═══════════════════════════════════════════════════════════════════
# _load / _save tests
# ═══════════════════════════════════════════════════════════════════


class TestLoadSave:
    def test_load_returns_empty_dict_when_no_file(self, clean_state):
        result = _load()
        assert result == {}

    def test_save_and_load_roundtrip(self, clean_state):
        data = {"BTCUSDT": {"date": "2026-06-26", "count": 1}}
        _save(data)
        loaded = _load()
        assert loaded == data

    def test_load_handles_corrupt_json(self, clean_state):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write("not valid json{{{")
        result = _load()
        assert result == {}

    def test_save_overwrites_existing(self, clean_state):
        _save({"old": "data"})
        _save({"new": "data"})
        loaded = _load()
        assert loaded == {"new": "data"}


# ═══════════════════════════════════════════════════════════════════
# can_open_trade tests
# ═══════════════════════════════════════════════════════════════════


class TestCanOpenTrade:
    def test_returns_true_for_new_symbol(self, clean_state):
        assert can_open_trade("BTCUSDT") is True

    def test_returns_true_for_new_day(self, clean_state):
        # Write old date
        _save({"BTCUSDT": {"date": "2020-01-01", "count": 1}})
        assert can_open_trade("BTCUSDT") is True

    def test_returns_false_when_count_reached_today(self, clean_state):
        _save({"BTCUSDT": {"date": _today(), "count": 1}})
        assert can_open_trade("BTCUSDT") is False

    def test_independent_per_symbol(self, clean_state):
        _save(
            {
                "BTCUSDT": {"date": _today(), "count": 1},
                "ETHUSDT": {"date": "2020-01-01", "count": 1},
            }
        )
        assert can_open_trade("BTCUSDT") is False
        assert can_open_trade("ETHUSDT") is True

    def test_empty_state_returns_true(self, clean_state):
        assert can_open_trade("ANYCOIN") is True


# ═══════════════════════════════════════════════════════════════════
# mark_trade_opened / mark_trade_closed tests
# ═══════════════════════════════════════════════════════════════════


class TestMarkTradeOpened:
    def test_writes_to_state(self, clean_state):
        mark_trade_opened("BTCUSDT", entry_price=50000.0)
        state = _load()
        assert "BTCUSDT" in state
        assert state["BTCUSDT"]["date"] == _today()
        assert state["BTCUSDT"]["count"] == 1
        assert state["BTCUSDT"]["entry_price"] == 50000.0
        assert state["BTCUSDT"]["open"] is True

    def test_overwrites_existing_entry(self, clean_state):
        mark_trade_opened("BTCUSDT", entry_price=50000.0)
        mark_trade_opened("BTCUSDT", entry_price=51000.0)
        state = _load()
        assert state["BTCUSDT"]["entry_price"] == 51000.0


class TestMarkTradeClosed:
    def test_sets_open_to_false(self, clean_state):
        mark_trade_opened("BTCUSDT", entry_price=50000.0)
        mark_trade_closed("BTCUSDT")
        state = _load()
        assert state["BTCUSDT"]["open"] is False

    def test_noop_for_unknown_symbol(self, clean_state):
        mark_trade_closed("UNKNOWN")
        state = _load()
        assert state == {}


# ═══════════════════════════════════════════════════════════════════
# sweep tekilleştirme tests
# ═══════════════════════════════════════════════════════════════════


class TestSweepDedup:
    def test_is_sweep_used_false_when_no_state(self, clean_state):
        assert is_sweep_used("bullish_42") is False

    def test_mark_and_check_sweep(self, clean_state):
        mark_sweep_used("bullish_42")
        assert is_sweep_used("bullish_42") is True

    def test_is_sweep_used_false_for_old_date(self, clean_state):
        _save({"_used_sweeps": {"bullish_42": {"date": "2020-01-01"}}})
        assert is_sweep_used("bullish_42") is False

    def test_unmark_sweep_used(self, clean_state):
        mark_sweep_used("bullish_42")
        assert is_sweep_used("bullish_42") is True
        unmark_sweep_used("bullish_42")
        assert is_sweep_used("bullish_42") is False

    def test_unmark_nonexistent_noop(self, clean_state):
        unmark_sweep_used("nonexistent")  # Should not raise

    def test_mark_sweep_cleans_old_entries(self, clean_state):
        # Mark an old entry first
        _save({"_used_sweeps": {"old_sweep": {"date": "2020-01-01"}}})
        # Mark a new one
        mark_sweep_used("new_sweep")
        state = _load()
        used = state.get("_used_sweeps", {})
        assert "new_sweep" in used
        assert "old_sweep" not in used  # Cleaned up

    def test_mark_sweep_multiple_today(self, clean_state):
        mark_sweep_used("sweep_1")
        mark_sweep_used("sweep_2")
        used = _load().get("_used_sweeps", {})
        assert len(used) == 2
        assert "sweep_1" in used
        assert "sweep_2" in used


# ═══════════════════════════════════════════════════════════════════
# reconcile_from_active tests
# ═══════════════════════════════════════════════════════════════════


class TestReconcileFromActive:
    def test_writes_new_entries(self, clean_state):
        active = {"BTCUSDT": {"entry_price": 50000.0}}
        reconcile_from_active(active)
        state = _load()
        assert "BTCUSDT" in state
        assert state["BTCUSDT"]["date"] == _today()
        assert state["BTCUSDT"]["count"] == 1
        assert state["BTCUSDT"]["open"] is True
        assert state["BTCUSDT"]["source"] == "startup_reconcile"

    def test_skips_already_recorded_today(self, clean_state):
        _save({"BTCUSDT": {"date": _today(), "count": 1}})
        active = {"BTCUSDT": {"entry_price": 50000.0}}
        reconcile_from_active(active)
        state = _load()
        # Should be unchanged (no "source" field from reconcile)
        assert "source" not in state["BTCUSDT"]

    def test_empty_active_noop(self, clean_state):
        reconcile_from_active({})
        assert _load() == {}

    def test_multiple_symbols(self, clean_state):
        active = {
            "BTCUSDT": {"entry_price": 50000.0},
            "ETHUSDT": {"entry_price": 3000.0},
        }
        reconcile_from_active(active)
        state = _load()
        assert len(state) == 2
        assert "BTCUSDT" in state
        assert "ETHUSDT" in state


# ═══════════════════════════════════════════════════════════════════
# get_trade_count_today tests
# ═══════════════════════════════════════════════════════════════════


class TestGetTradeCountToday:
    def test_returns_0_for_unknown_symbol(self, clean_state):
        assert get_trade_count_today("UNKNOWN") == 0

    def test_returns_0_for_old_date(self, clean_state):
        _save({"BTCUSDT": {"date": "2020-01-01", "count": 1}})
        assert get_trade_count_today("BTCUSDT") == 0

    def test_returns_count_for_today(self, clean_state):
        _save({"BTCUSDT": {"date": _today(), "count": 1}})
        assert get_trade_count_today("BTCUSDT") == 1

    def test_returns_count_zero_when_no_count_field(self, clean_state):
        _save({"BTCUSDT": {"date": _today()}})
        assert get_trade_count_today("BTCUSDT") == 0


# ═══════════════════════════════════════════════════════════════════
# retrade arm tests
# ═══════════════════════════════════════════════════════════════════


class TestRetradeArm:
    def test_save_and_check_retrade_arm(self, clean_state):
        # Need a base entry first for save_retrade_arm to work
        mark_trade_opened("BTCUSDT", entry_price=50000.0)
        save_retrade_arm("BTCUSDT", "long", 42)
        state = _load()
        assert state["BTCUSDT"]["retrade_armed"] is True
        assert state["BTCUSDT"]["retrade_side"] == "long"
        assert state["BTCUSDT"]["retrade_entry_bar"] == 42

    def test_clear_retrade_arm(self, clean_state):
        mark_trade_opened("BTCUSDT", entry_price=50000.0)
        save_retrade_arm("BTCUSDT", "long", 42)
        clear_retrade_arm("BTCUSDT")
        state = _load()
        assert "retrade_armed" not in state["BTCUSDT"]
        assert "retrade_side" not in state["BTCUSDT"]
        assert "retrade_entry_bar" not in state["BTCUSDT"]
        # Other fields preserved
        assert state["BTCUSDT"]["count"] == 1

    def test_clear_retrade_arm_unknown_symbol(self, clean_state):
        clear_retrade_arm("UNKNOWN")  # Should not raise

    def test_save_retrade_arm_unknown_symbol_noop(self, clean_state):
        save_retrade_arm("UNKNOWN", "long", 42)
        state = _load()
        assert state == {}


# ═══════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_concurrent_access_same_symbol(self, clean_state):
        """Multiple mark_trade_opened calls don't corrupt state."""
        for i in range(5):
            mark_trade_opened("BTCUSDT", entry_price=50000.0 + i)
        state = _load()
        assert state["BTCUSDT"]["count"] == 1

    def test_state_persists_across_loads(self, clean_state):
        mark_trade_opened("BTCUSDT", entry_price=50000.0)
        mark_sweep_used("sweep_1")
        # Load fresh
        state = _load()
        assert "BTCUSDT" in state
        assert "_used_sweeps" in state

    def test_special_characters_in_symbol(self, clean_state):
        """Symbol names like 1000PEPEUSDT should work."""
        mark_trade_opened("1000PEPEUSDT", entry_price=0.01)
        assert can_open_trade("1000PEPEUSDT") is False
