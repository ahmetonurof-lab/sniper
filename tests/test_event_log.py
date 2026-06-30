import json
import os
from datetime import datetime
from unittest.mock import patch


from event_log import _event_path, _EVENT_PREFIX, log_event, cleanup_old_event_logs


class TestLogEvent:
    def test_writes_jsonl_line(self):
        sym = "BTCUSDT"
        log_event("entry", sym, side="long", entry_price=50000.0)
        path = _event_path()
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
        record = json.loads(line)
        assert record["event_type"] == "entry"
        assert record["symbol"] == "BTCUSDT"
        assert record["side"] == "long"
        assert record["entry_price"] == 50000.0
        assert isinstance(record["ts"], int)

    def test_appends_multiple_events(self):
        log_event("entry", "BTCUSDT", side="long")
        log_event("exit", "ETHUSDT", side="short", pnl=50.0)
        path = _event_path()
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 2
        last = json.loads(lines[-1])
        assert last["event_type"] == "exit"
        assert last["pnl"] == 50.0

    def test_different_event_types(self):
        log_event("entry", "BTCUSDT")
        log_event("exit", "BTCUSDT")
        log_event("sl_reject", "BTCUSDT")
        log_event("tp_reject", "BTCUSDT")
        log_event("orphan_cleaned", "BTCUSDT")
        log_event("ghost_cleaned", "BTCUSDT")
        log_event("force_close", "BTCUSDT", success=True)
        path = _event_path()
        with open(path, "r", encoding="utf-8") as f:
            types = [json.loads(line)["event_type"] for line in f]
        assert "entry" in types
        assert "exit" in types
        assert "sl_reject" in types
        assert "tp_reject" in types
        assert "orphan_cleaned" in types
        assert "ghost_cleaned" in types
        assert "force_close" in types


class TestCleanupOld:
    def test_removes_old_files(self, tmp_path):
        old = tmp_path / f"{_EVENT_PREFIX}2020-01-01.jsonl"
        old.write_text("{}\n")
        new = tmp_path / f"{_EVENT_PREFIX}{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        new.write_text("{}\n")
        with patch("event_log._OUTPUT_DIR", str(tmp_path)):
            cleanup_old_event_logs()
        assert not old.exists()
        assert new.exists()

    def test_keeps_recent_files(self, tmp_path):
        recent = (
            tmp_path / f"{_EVENT_PREFIX}{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        )
        recent.write_text("{}\n")
        with patch("event_log._OUTPUT_DIR", str(tmp_path)):
            cleanup_old_event_logs()
        assert recent.exists()

    def test_non_event_files_untouched(self, tmp_path):
        other = tmp_path / "paper_trade.log"
        other.write_text("log data\n")
        with patch("event_log._OUTPUT_DIR", str(tmp_path)):
            cleanup_old_event_logs()
        assert other.exists()

    def test_missing_directory_no_error(self):
        with patch("event_log._OUTPUT_DIR", "/nonexistent/path/xyz"):
            cleanup_old_event_logs()
