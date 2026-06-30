import json
import logging
import os
from datetime import datetime, timedelta, timezone

TR_TZ = timezone(timedelta(hours=3))

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "..", "output")
_EVENT_PREFIX = "events_"
_RETENTION_DAYS = 14

log = logging.getLogger("sniper.event_log")


def _event_path(dt: datetime | None = None) -> str:
    if dt is None:
        dt = datetime.now(TR_TZ)
    return os.path.join(_OUTPUT_DIR, f"{_EVENT_PREFIX}{dt.strftime('%Y-%m-%d')}.jsonl")


def log_event(event_type: str, symbol: str = "", **kwargs) -> None:
    record = {
        "ts": int(datetime.now(TR_TZ).timestamp() * 1000),
        "event_type": event_type,
        "symbol": symbol,
        **kwargs,
    }
    path = _event_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        log.warning("[EVENT_LOG] yazma hatasi: %s", e)


def cleanup_old_event_logs() -> None:
    now = datetime.now(TR_TZ)
    cutoff = now - timedelta(days=_RETENTION_DAYS)
    try:
        if not os.path.isdir(_OUTPUT_DIR):
            return
        for fname in os.listdir(_OUTPUT_DIR):
            if not fname.startswith(_EVENT_PREFIX) or not fname.endswith(".jsonl"):
                continue
            date_str = fname[len(_EVENT_PREFIX) : -len(".jsonl")]
            try:
                fdate = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TR_TZ)
                if fdate < cutoff:
                    os.remove(os.path.join(_OUTPUT_DIR, fname))
                    log.info("[HOUSEKEEPING] %s silindi (>14 gun)", fname)
            except ValueError:
                continue
    except Exception as e:
        log.warning("[HOUSEKEEPING] event log temizleme hatasi: %s", e)
