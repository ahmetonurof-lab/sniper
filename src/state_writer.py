"""
state_writer.py — Her 15m kapanışında live_state.json yazar.
Dashboard ve chart_export.py bu dosyayı okur.
"""

import json
import os
from datetime import UTC, datetime

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
_STATE_FILE = os.path.join(_OUTPUT_DIR, "live_state.json")


def write_state(
    states: dict,  # PaperTrader.states — sym → SessionState
    active_trades: dict,  # PaperTrader.active_trades — sym → ActiveTrade
    available_balance: float,
    wallet_balance: float,
    symbols: list[str],
) -> None:
    out = {
        "updated_at": datetime.now(UTC).isoformat(),
        "available_balance": round(available_balance, 2),
        "wallet_balance": round(wallet_balance, 2),
        "symbols": {},
    }
    for sym in symbols:
        ss = states.get(sym)
        trade = active_trades.get(sym)
        if ss is None:
            continue

        out["symbols"][sym] = {
            "cbdr_high": round(ss.cbdr_body_high, 6),
            "cbdr_low": round(ss.cbdr_body_low, 6)
            if ss.cbdr_body_low != float("inf")
            else 0,
            "cbdr_locked": ss.cbdr_locked,
            "sweep_confirmed": ss.sweep_confirmed,
            "sweep_direction": ss.sweep_direction,
            "sweep_level": round(ss.sweep_level, 6) if ss.sweep_level else None,
            "fvg_ready": ss.fvg_ready,
            "bias": ss.daily_bias.value,
            "range_type": ss.range_type,
            "london_high": round(ss.london_high, 6),
            "london_low": round(ss.london_low, 6)
            if ss.london_low != float("inf")
            else 0,
            "trades_today": ss.trades_today,
            "retrade_armed": ss.retrade_armed,
            "retrade_side": ss.retrade_side,
            "active_trade": {
                "side": trade["side"],
                "entry": round(trade["entry_price"], 6),
                "sl": round(trade["sl"], 6),
                "tp": round(trade["tp"], 6),
                "qty": round(trade["qty"], 6),
                "fvg_top": round(trade["trigger_fvg"].top, 6)
                if trade.get("trigger_fvg")
                else None,
                "fvg_bottom": round(trade["trigger_fvg"].bottom, 6)
                if trade.get("trigger_fvg")
                else None,
                "trailing_count": trade.get("trailing_count", 0),
                "is_retrade": trade.get("is_retrade", False),
                "upnl": trade.get("upnl"),
            }
            if trade
            else None,
        }

    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
