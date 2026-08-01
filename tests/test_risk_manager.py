"""
risk_manager.py testleri — BUG-25: bozuk state dosyası fallback.
"""

import json

from risk_manager import RiskManager


class TestLoadState:
    def _write(self, path, content):
        with open(path, "w") as f:
            f.write(content)

    def test_corrupt_state_falls_back_to_initial_equity(self, tmp_path):
        state_file = str(tmp_path / "risk_state.json")
        self._write(state_file, "{bozuk-json!!")
        rm = RiskManager(state_file=state_file, initial_equity=10000.0)
        assert rm.peak_equity == 10000.0
        assert rm.is_circuit_broken is False

    def test_corrupt_state_keeps_circuit_breaker_armed(self, tmp_path):
        state_file = str(tmp_path / "risk_state.json")
        self._write(state_file, "not json at all")
        rm = RiskManager(state_file=state_file, initial_equity=5000.0, dd_trip=15.0)
        assert rm.peak_equity == 5000.0
        dd = rm.get_current_dd(4500.0)
        assert abs(dd - 10.0) < 1e-6

    def test_valid_state_loaded(self, tmp_path):
        state_file = str(tmp_path / "risk_state.json")
        self._write(
            state_file, json.dumps({"peak_equity": 8000.0, "is_circuit_broken": True})
        )
        rm = RiskManager(state_file=state_file, initial_equity=10000.0)
        assert rm.peak_equity == 8000.0
        assert rm.is_circuit_broken is True

    def test_missing_state_uses_defaults(self, tmp_path):
        state_file = str(tmp_path / "nope.json")
        rm = RiskManager(state_file=state_file, initial_equity=10000.0)
        assert rm.peak_equity == 0.0
        assert rm.is_circuit_broken is False
