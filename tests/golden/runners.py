from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from session import CBDRState
from fvg import detect_fvgs
from models import Bar, FVG


# ─── Data snapshot hasher ──────────────────────────────────────────


def data_snapshot_hash(bars: list[Bar], extra: dict | None = None) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(
            f"{b.index},{b.open},{b.high},{b.low},{b.close},{b.is_closed},{b.timestamp}\n".encode()
        )
    if extra:
        h.update(json.dumps(extra, sort_keys=True).encode())
    return h.hexdigest()[:16]


# ─── CBDR sweep runners ────────────────────────────────────────────


def normalize_sweep_result(cbdr: CBDRState) -> dict[str, Any]:
    return {
        "sweep_confirmed": cbdr.sweep_confirmed,
        "sweep_direction": cbdr.sweep_direction,
        "sweep_level": cbdr.sweep_level,
        "daily_bias": cbdr.daily_bias.value if cbdr.daily_bias else None,
        "body_high": cbdr.body_high,
        "body_low": cbdr.body_low,
    }


def run_cbdr_snapshot(
    bars: list[Bar],
    body_high: float = 0.0,
    body_low: float = float("inf"),
    body_locked: bool = True,
    atr_value: float = 0.0,
    tolerance: float = 0.0,
    config: dict | None = None,
) -> CBDRState:
    cbdr = CBDRState()
    if body_high > 0 or body_low != float("inf"):
        cbdr.body_high = body_high
        cbdr.body_low = body_low
    cbdr.locked = body_locked

    for b in bars:
        if not cbdr.locked:
            break
        if cbdr.sweep_confirmed:
            break
        cbdr.check_sweep(
            high=b.high,
            low=b.low,
            close=b.close,
            atr=atr_value,
        )

    return cbdr


def run_cbdr_backtest(fx: dict) -> CBDRState:
    return run_cbdr_snapshot(
        bars=fx["bars"],
        body_high=fx.get("cbdr_body_high", 0.0),
        body_low=fx.get("cbdr_body_low", float("inf")),
        body_locked=fx.get("body_locked", True),
        atr_value=fx.get("atr_value", 0.0),
        tolerance=fx.get("tolerance", 0.0),
        config=fx.get("config"),
    )


def run_cbdr_live(fx: dict) -> CBDRState:
    closed_bars = [b for b in fx["bars"] if b.is_closed]
    return run_cbdr_snapshot(
        bars=closed_bars,
        body_high=fx.get("cbdr_body_high", 0.0),
        body_low=fx.get("cbdr_body_low", float("inf")),
        body_locked=fx.get("body_locked", True),
        atr_value=fx.get("atr_value", 0.0),
        tolerance=fx.get("tolerance", 0.0),
        config=fx.get("config"),
    )


# ─── FVG runners ────────────────────────────────────────────────────


def normalize_fvg(fvg: FVG) -> dict[str, Any]:
    return {
        "direction": fvg.direction,
        "top": fvg.top,
        "bottom": fvg.bottom,
        "real_index": fvg.real_index,
        "timeframe": fvg.timeframe,
        "size": fvg.size,
    }


def normalize_fvgs(fvgs: list[FVG]) -> list[dict[str, Any]]:
    return [normalize_fvg(f) for f in fvgs]


def run_fvg_snapshot(
    bars: list[Bar],
    lookback: int = 100,
    timeframe: str = "5m",
    min_fvg_size: float = 1e-8,
    since_index: int | None = None,
) -> list[FVG]:
    return detect_fvgs(
        bars,
        lookback=lookback,
        timeframe=timeframe,
        min_fvg_size=min_fvg_size,
        since_index=since_index,
    )


def run_fvg_backtest(fx: dict) -> list[FVG]:
    return run_fvg_snapshot(
        bars=fx["bars"],
        lookback=fx.get("lookback", 100),
        timeframe=fx.get("timeframe", "15m"),
        min_fvg_size=fx.get("min_fvg_size", 1e-8),
        since_index=fx.get("since_index"),
    )


def run_fvg_signal(fx: dict) -> list[FVG]:
    return run_fvg_snapshot(
        bars=[b for b in fx["bars"] if b.is_closed],
        lookback=fx.get("lookback", 100),
        timeframe=fx.get("timeframe", "15m"),
        min_fvg_size=fx.get("min_fvg_size", 1e-8),
        since_index=fx.get("since_index"),
    )


def run_fvg_trailing(fx: dict, entry_bar_index: int = 0) -> list[FVG]:
    bars = [b for b in fx["bars"] if b.is_closed and b.index >= entry_bar_index]
    return run_fvg_snapshot(
        bars=bars,
        lookback=fx.get("lookback", 50),
        timeframe=fx.get("timeframe", "15m"),
        min_fvg_size=fx.get("min_fvg_size", 1e-8),
        since_index=fx.get("since_index"),
    )


# ─── Diff classification ───────────────────────────────────────────


DIFF_CLASSIFICATION = {
    "NO_DIFF": "no difference detected",
    "DATA_WINDOW_DIFF": "input bar window differs between consumers",
    "TIMEFRAME_DIFF": "timeframe or resampling differs",
    "ATR_DIFF": "ATR value differs",
    "CONFIG_DIFF": "configuration (min_size, tolerance) differs",
    "DIRECTION_FILTER_DIFF": "direction filter differs between consumers",
    "SELECTION_DIFF": "same FVG list but different selection",
    "CLOSED_BAR_DIFF": "closed/open bar boundary differs",
    "DUPLICATE_EVENT_DIFF": "duplicate event handling differs",
    "REAL_ALGORITHM_DIFF": "real algorithmic divergence",
}


def classify_diff(expected: Any, actual: Any, context: dict | None = None) -> str:
    if expected == actual:
        return "NO_DIFF"
    if context:
        c = context
        if c.get("atr_diff"):
            return "ATR_DIFF"
        if c.get("config_diff"):
            return "CONFIG_DIFF"
        if c.get("timeframe_diff"):
            return "TIMEFRAME_DIFF"
        if c.get("direction_filter_diff"):
            return "DIRECTION_FILTER_DIFF"
        if c.get("closed_bar_diff"):
            return "CLOSED_BAR_DIFF"
        if c.get("data_window_diff"):
            return "DATA_WINDOW_DIFF"
    return "REAL_ALGORITHM_DIFF"


# ─── Log writer (JSONL) ─────────────────────────────────────────────


_GOLDEN_LOG_PATH: str | None = None


def configure_golden_log(path: str) -> None:
    global _GOLDEN_LOG_PATH
    _GOLDEN_LOG_PATH = path


def write_golden_log(record: dict[str, Any]) -> None:
    import os

    path = _GOLDEN_LOG_PATH
    if path is None:
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "output", "golden_test.log"
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record["ts"] = datetime.now(timezone.utc).isoformat()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
