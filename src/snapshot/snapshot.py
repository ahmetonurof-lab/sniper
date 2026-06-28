"""
snapshot.py — High-fidelity trading snapshot (Lightweight Charts + Playwright).

Retrospektif: trade kapandiktan sonra calisir, trading loop'a dokunmaz.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

import pandas as pd
import requests
from playwright.sync_api import sync_playwright

log = logging.getLogger("sniper.snapshot")

_SNAPSHOTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "output", "charts"
)
_BINANCE_BASE = "https://fapi.binance.com/fapi/v1/klines"
_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "chart_template.html"
)


def _fetch_ohlc(sym: str, limit: int = 80) -> list[dict] | None:
    try:
        r = requests.get(
            _BINANCE_BASE,
            params={"symbol": sym, "interval": "15m", "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("[SNAPSHOT] %s OHLC hatasi: %s", sym, e)
        return None

    df = pd.DataFrame(
        data,
        columns=[
            "ts",
            "Open",
            "High",
            "Low",
            "Close",
            "v",
            "_1",
            "_2",
            "_3",
            "_4",
            "_5",
            "_6",
        ],
    )
    df = df[["ts", "Open", "High", "Low", "Close"]].astype(
        {"Open": float, "High": float, "Low": float, "Close": float}
    )
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.dropna(inplace=True)
    if df.empty:
        return None

    return [
        {
            "time": int(row["ts"].timestamp()),
            "open": row["Open"],
            "high": row["High"],
            "low": row["Low"],
            "close": row["Close"],
        }
        for _, row in df.iterrows()
    ]


def _find_bar(candles: list[dict], price: float) -> int:
    for i, c in enumerate(candles):
        if c["low"] <= price <= c["high"]:
            return i
    return len(candles) - 1


def capture_snapshot(
    sym: str,
    trade: dict,
    pnl: float,
    session_state,
) -> str | None:
    candles = _fetch_ohlc(sym)
    if not candles:
        return None

    side = trade.get("side", "long")
    fvg = trade.get("trigger_fvg")
    entry_price = trade["entry_price"]
    exit_price = trade.get("exit_price", entry_price)
    sl_price = trade["sl"]
    tp_price = trade["tp"]

    ts_ms = trade.get("exit_timestamp") or trade.get("close_time", 0)

    entry_bar = _find_bar(candles, entry_price)
    exit_bar = _find_bar(candles, exit_price)

    # Trim candles to balanced window around trade
    PAD = 8
    start = max(0, entry_bar - PAD)
    end = min(len(candles), exit_bar + PAD + 1)
    candles = candles[start:end]
    entry_bar -= start
    exit_bar -= start

    # FVG direction & formation bar approx (after trim)
    fvg_direction = None
    fvg_bar_index = -1
    if fvg:
        fvg_direction = fvg.direction
        fvg_bar_index = max(0, entry_bar - 3)

    # Map trail step bar indices to trimmed window
    entry_bar_idx_abs = trade.get("entry_bar_index", 0)
    mapped_steps = []
    for step in trade.get("trail_steps", []):
        rel = entry_bar + (step.get("bar", 0) - entry_bar_idx_abs)
        if 0 <= rel < len(candles):
            mapped_steps.append({"bar": rel, "sl": step["sl"], "tp": step.get("tp", 0)})

    payload = json.dumps(
        {
            "candles": candles,
            "entryPrice": entry_price,
            "exitPrice": exit_price,
            "slPrice": sl_price,
            "tpPrice": tp_price,
            "initialSlPrice": trade.get("initial_sl"),
            "initialTpPrice": trade.get("initial_tp"),
            "side": side,
            "exitReason": trade.get("result"),
            "cbdrHigh": getattr(session_state, "cbdr_body_high", None),
            "cbdrLow": getattr(session_state, "cbdr_body_low", None),
            "fvgTop": fvg.top if fvg else None,
            "fvgBottom": fvg.bottom if fvg else None,
            "fvgDirection": fvg_direction,
            "fvgBarIndex": fvg_bar_index,
            "entryBar": entry_bar,
            "exitBar": exit_bar,
            "trailSteps": mapped_steps,
        }
    )

    if not os.path.exists(_TEMPLATE_PATH):
        log.warning("[SNAPSHOT] template bulunamadi: %s", _TEMPLATE_PATH)
        return None

    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read().replace("__DATA__", payload)

    os.makedirs(_SNAPSHOTS_DIR, exist_ok=True)

    dt = (
        datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        if ts_ms
        else datetime.now(timezone.utc)
    )
    filename = f"{sym}_{dt.strftime('%Y-%m-%d_%H%M%S')}.png"
    outpath = os.path.join(_SNAPSHOTS_DIR, filename)

    try:
        _render_png(html, outpath)
        log.info("[SNAPSHOT] %s -> %s", sym, filename)
        return filename
    except Exception as e:
        log.warning("[SNAPSHOT] %s render hatasi: %s", sym, e)
        return None


def _render_png(html: str, outpath: str) -> None:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    )
    tmp.write(html)
    tmp_path = tmp.name
    tmp.close()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=2,
            )
            file_url = f"file:///{tmp_path.replace(os.sep, '/')}"
            page.goto(file_url, wait_until="networkidle")
            page.wait_for_selector('[data-rendered="1"]', timeout=15000)
            page.wait_for_timeout(500)
            page.screenshot(path=outpath, full_page=False)
            browser.close()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
