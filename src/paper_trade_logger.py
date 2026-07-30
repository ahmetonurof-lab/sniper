"""
paper_trade_logger.py — Append-only JSONL for paper trade lifecycle events.

Output: output/paper_trade.log
Schema version: 1

No secrets, API keys, or full tracebacks are ever written to the log.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

TR_TZ = timezone(timedelta(hours=3))

_LOG_PATH: str | None = None
_RUN_ID: str = ""
_LOG = logging.getLogger("sniper.paper_trade_logger")


class EventType(str, Enum):
    ENTRY_FILLED = "entry_filled"
    INITIAL_SL_CALCULATED = "initial_sl_calculated"
    ENTRY_QTY_READY = "entry_qty_ready"
    PROTECTION_NORMALIZED = "protection_normalized"
    PROTECTION_VALIDATED = "protection_validated"
    SL_PLACED = "sl_placed"
    TP_PLACED = "tp_placed"
    SL_REJECTED = "sl_rejected"
    TP_REJECTED = "tp_rejected"
    RETRY_SUPPRESSED = "retry_suppressed"
    EMERGENCY_CLOSE_STARTED = "emergency_close_started"
    EMERGENCY_CLOSE_COMPLETED = "emergency_close_completed"
    EMERGENCY_CLOSE_FAILED = "emergency_close_failed"
    TRAIL_CANDIDATE = "trail_candidate"
    TRAIL_SKIPPED = "trail_skipped"
    TRADE_CLOSED = "trade_closed"


def configure(log_path: str, run_id: str) -> None:
    global _LOG_PATH, _RUN_ID
    _LOG_PATH = log_path
    _RUN_ID = run_id
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    except Exception:
        pass


def _event_id() -> str:
    return uuid.uuid4().hex[:12]


def _ensure_path() -> str:
    path = _LOG_PATH
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "output",
            "paper_trade.log",
        )
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass
    return path


def log_event(
    event_type: EventType,
    symbol: str,
    side: str,
    trade_id: str = "",
    *,
    entry: dict[str, Any] | None = None,
    protection: dict[str, Any] | None = None,
    fvg: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    result: str = "",
    reason: str = "",
    latency_ms: float | None = None,
    call_count: int | None = None,
    protected_state_before: bool | None = None,
    protected_state_after: bool | None = None,
    **extra: Any,
) -> None:
    global _RUN_ID
    if not _RUN_ID:
        date_str = datetime.now(TR_TZ).strftime("%Y%m%d-%H%M%S")
        _RUN_ID = f"paper-{date_str}"

    record: dict[str, Any] = {
        "schema_version": 1,
        "ts": int(datetime.now(TR_TZ).timestamp() * 1000),
        "event_id": _event_id(),
        "run_id": _RUN_ID,
        "trade_id": trade_id,
        "event_type": event_type.value,
        "symbol": symbol,
        "side": side,
    }
    if entry is not None:
        record["entry"] = entry
    if protection is not None:
        record["protection"] = protection
    if fvg is not None:
        record["fvg"] = fvg
    if validation is not None:
        record["validation"] = validation
    if error is not None:
        record["error"] = error
    if result:
        record["result"] = result
    if reason:
        record["reason"] = reason
    if latency_ms is not None:
        record["latency_ms"] = latency_ms
    if call_count is not None:
        record["call_count"] = call_count
    if protected_state_before is not None:
        record["protected_state_before"] = protected_state_before
    if protected_state_after is not None:
        record["protected_state_after"] = protected_state_after
    record.update(extra)

    path = _ensure_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        _LOG.warning("[PAPER_LOG] yazma hatasi: %s", e)
