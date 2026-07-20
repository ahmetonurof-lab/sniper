import json
import os
from datetime import UTC, datetime

import config as cfg
from models import (
    STATUS_EXIT_VERIFYING,
    STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED,
    STATUS_REPAIR_REQUIRED,
    UNRESTRICTED_STATUSES,
)

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
_STATE_FILE = os.path.join(_OUTPUT_DIR, "live_state.json")


def write_state(
    states: dict,
    active_trades: dict,
    available_balance: float,
    wallet_balance: float,
    symbols: list[str],
) -> None:
    out = {
        "updated_at": datetime.now(UTC).isoformat(),
        "balance": round(available_balance, 2),
        "available_balance": round(available_balance, 2),
        "wallet_balance": round(wallet_balance, 2),
        "total_upnl": round(
            sum(t.upnl for t in active_trades.values() if t.upnl is not None), 2
        )
        if any(t.upnl is not None for t in active_trades.values())
        else None,
        "symbols": {},
        # Patch Set 6: operator visibility — hangi kod yollari aktif?
        "feature_flags": {
            "exit_lifecycle_service": cfg.EXIT_LIFECYCLE_SERVICE_ENABLED,
            "protection_lifecycle_service": cfg.PROTECTION_LIFECYCLE_SERVICE_ENABLED,
            "ws_event_normalization": cfg.WS_EVENT_NORMALIZATION_ENABLED,
        },
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
            "active_trade": {
                "side": trade["side"],
                "entry": round(trade["entry_price"], 6),
                "sl": round(trade["sl"], 6),
                "tp": round(trade["tp"], 6),
                "qty": round(trade["qty"], 6),
                "fvg_top": trade.get("fvg_top"),
                "fvg_bottom": trade.get("fvg_bottom"),
                "trailing_count": trade.get("trailing_count", 0),
                "upnl": trade.get("upnl"),
                "status": trade.get("status", ""),
                "frozen": trade.get("status", "") not in UNRESTRICTED_STATUSES,
                "sl_order_id_present": bool(trade.get("sl_order_id")),
                "tp_order_id_present": bool(trade.get("tp_order_id")),
                "exit_unconfirmed": trade.get("status")
                in (
                    STATUS_EXIT_VERIFYING,
                    STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED,
                ),
                "repair_required": trade.get("status") == STATUS_REPAIR_REQUIRED,
            }
            if trade
            else None,
        }
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
