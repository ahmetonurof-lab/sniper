"""
trade_exporter.py — Kapanan tradeleri trades_history.jsonl'a yazar.
Bot OKUMAZ, sadece append yapar. Dashboard/chart_export okur.
"""

import json
import os
import logging

log = logging.getLogger("sniper.trade_exporter")

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
_HISTORY_FILE = os.path.join(_OUTPUT_DIR, "trades_history.jsonl")


def export_trade(sym: str, trade: dict, pnl: float, ss) -> None:
    """Kapanan trade'i trades_history.jsonl'a append eder."""
    fvg = trade.get("trigger_fvg")
    record = {
        "sym": sym,
        "side": trade["side"],
        "entry": round(trade["entry_price"], 6),
        "exit": round(trade["exit_price"], 6),
        "sl": round(trade["sl"], 6),
        "tp": round(trade["tp"], 6),
        "exit_reason": trade["result"],
        "trailing_count": trade.get("trailing_count", 0),
        "pnl": round(pnl, 2),
        "cbdr_high": round(ss.cbdr_body_high, 6),
        "cbdr_low": round(ss.cbdr_body_low, 6),
        "sweep_level": round(ss.sweep_level, 6) if ss.sweep_level else None,
        "sweep_direction": ss.sweep_direction,
        "fvg_top": round(fvg.top, 6) if fvg else None,
        "fvg_bottom": round(fvg.bottom, 6) if fvg else None,
        "is_retrade": trade.get("is_retrade", False),
        "chart_file": trade.get("chart_file"),
        "timestamp": trade.get("exit_timestamp") or trade.get("close_time", 0),
    }
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    try:
        with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("[EXPORT] trade yazma hatasi: %s", e)
