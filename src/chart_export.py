"""
chart_export.py — Kapanan trade için Plotly HTML candlestick chart basar.
Dashboard'dan link verilir, tarayıcıda zoom/hover destekli.
"""

import logging
import os
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import requests

log = logging.getLogger("sniper.chart_export")

_CHARTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "dashboard", "charts"
)
_BINANCE_BASE = "https://fapi.binance.com/fapi/v1/klines"


def export_chart(sym: str, trade: dict, pnl: float, ss) -> str | None:
    """Trade için Plotly chart basar, dosya yolunu döndürür."""
    ts_ms = trade.get("exit_timestamp") or trade.get("close_time", 0)
    if not ts_ms:
        return None

    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    filename = f"{sym}_{dt.strftime('%Y-%m-%d_%H%M')}.html"
    filepath = os.path.join(_CHARTS_DIR, filename)
    if os.path.exists(filepath):
        return filename

    # OHLC çek
    try:
        r = requests.get(
            _BINANCE_BASE,
            params={"symbol": sym, "interval": "15m", "limit": 80},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("[CHART] %s OHLC hatasi: %s", sym, e)
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

    entry_p = trade["entry_price"]
    sl_p = trade["sl"]
    tp_p = trade["tp"]
    exit_p = trade.get("exit_price", entry_p)
    side = trade["side"]

    fvg = trade.get("trigger_fvg")

    fig = go.Figure()

    # Candlesticks
    fig.add_trace(
        go.Candlestick(
            x=df["ts"],
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="15m",
            increasing_line_color="#7ecfb0",
            decreasing_line_color="#c97a7a",
        )
    )

    # CBDR zone
    cbdr_h = ss.cbdr_body_high
    cbdr_l = ss.cbdr_body_low
    if cbdr_h and cbdr_l and cbdr_h > cbdr_l:
        fig.add_hrect(
            y0=cbdr_l,
            y1=cbdr_h,
            fillcolor="#c9a84c",
            opacity=0.06,
            line_width=1,
            line_color="#c9a84c",
            line_dash="dash",
            name="CBDR",
        )

    # Sweep
    if ss.sweep_level:
        fig.add_hline(
            y=ss.sweep_level,
            line_color="#b088cc",
            line_dash="dot",
            line_width=1.5,
            name=f"Sweep @ {ss.sweep_level}",
        )

    # FVG zone
    fvg_top = fvg.top if fvg else None
    fvg_bot = fvg.bottom if fvg else None
    if fvg_top and fvg_bot:
        fig.add_hrect(
            y0=fvg_bot,
            y1=fvg_top,
            fillcolor="#7ecfb0",
            opacity=0.12,
            line_width=1,
            line_color="#7ecfb0",
            line_dash="dash",
            name="FVG",
        )

    # Entry
    fig.add_hline(
        y=entry_p,
        line_color="#c97a7a",
        line_width=2,
        annotation_text=f"ENTRY {side.upper()} @ {entry_p}",
        annotation_position="left",
    )

    # SL
    fig.add_hline(
        y=sl_p,
        line_color="#ff4444",
        line_width=1.2,
        annotation_text=f"SL {sl_p}",
        annotation_position="right",
    )

    # TP
    fig.add_hline(
        y=tp_p,
        line_color="#44bb44",
        line_width=1.2,
        annotation_text=f"TP {tp_p}",
        annotation_position="right",
    )

    # Exit (varsa ve entry'den farklıysa)
    if exit_p != entry_p:
        fig.add_hline(
            y=exit_p,
            line_color="white",
            line_width=1.8,
            line_dash="dash",
            annotation_text=f"EXIT @ {exit_p} ({trade['result']})",
            annotation_position="bottom right",
        )

    pnl_str = f"{pnl:+.2f}" if pnl else "?"
    fig.update_layout(
        title=f"{sym} · {side.upper()} · PnL: {pnl_str} USDT · R:R 1:2",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=500,
        margin=dict(l=50, r=50, t=60, b=30),
        hovermode="x unified",
    )

    os.makedirs(_CHARTS_DIR, exist_ok=True)
    try:
        fig.write_html(filepath, include_plotlyjs="cdn", full_html=False)
        log.info("[CHART] %s -> %s", sym, filename)
        return filename
    except Exception as e:
        log.warning("[CHART] %s yazma hatasi: %s", sym, e)
        return None
