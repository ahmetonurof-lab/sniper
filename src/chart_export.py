"""
chart_export.py — Kapanan trade için Plotly HTML candlestick chart basar.
CBDR box, sweep mum, FVG+CE, trail adimlari, session zamani hepsi isaretli.
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


def _find_sweep_bar(df: pd.DataFrame, cbdr_h: float, cbdr_l: float) -> int | None:
    """CBDR high/low disina tasan ilk mumun indeksini bul."""
    if cbdr_h in (float("inf"), 0) or cbdr_l in (float("-inf"), 0):
        return None
    for idx, row in df.iterrows():
        if row["High"] > cbdr_h or row["Low"] < cbdr_l:
            return idx
    return None


def export_chart(sym: str, trade: dict, pnl: float, ss) -> str | None:
    ts_ms = trade.get("exit_timestamp") or trade.get("close_time", 0)
    if not ts_ms:
        return None

    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    filename = f"{sym}_{dt.strftime('%Y-%m-%d_%H%M')}.html"
    filepath = os.path.join(_CHARTS_DIR, filename)
    if os.path.exists(filepath):
        return filename

    # OHLC
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
    dir_label = "SHORT" if side == "short" else "LONG"

    cbdr_h = ss.cbdr_body_high
    cbdr_l = ss.cbdr_body_low
    fvg = trade.get("trigger_fvg")

    fig = go.Figure()

    # ── Mumlar ────────────────────────────────────────────────
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

    # ── CBDR Range Box ───────────────────────────────────────
    if cbdr_h and cbdr_l and cbdr_h > cbdr_l:
        fig.add_hrect(
            y0=cbdr_l,
            y1=cbdr_h,
            fillcolor="#c9a84c",
            opacity=0.06,
            line_width=1,
            line_color="#c9a84c",
            line_dash="dash",
            annotation_text=f"CBDR {cbdr_h}-{cbdr_l}",
            annotation_position="top right",
        )

    # ── Sweep Mumu (CBDR kirilimi) ───────────────────────────
    sweep_idx = _find_sweep_bar(df, cbdr_h, cbdr_l)
    if sweep_idx is not None:
        row = df.loc[sweep_idx]
        fig.add_shape(
            type="rect",
            x0=row["ts"] - pd.Timedelta(minutes=7),
            x1=row["ts"] + pd.Timedelta(minutes=7),
            y0=row["Low"],
            y1=row["High"],
            line=dict(color="#b088cc", width=2, dash="dot"),
            fillcolor="#b088cc",
            opacity=0.1,
        )
        fig.add_annotation(
            x=row["ts"],
            y=row["High"],
            text=f"SFP @ {row['High']:.3f}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowcolor="#b088cc",
            font=dict(color="#b088cc", size=10),
            bgcolor="#111",
            bordercolor="#b088cc",
            borderwidth=1,
        )

    # ── Trigger FVG Zone ─────────────────────────────────────
    fvg_t = fvg.top if fvg else None
    fvg_b = fvg.bottom if fvg else None
    if fvg_t and fvg_b:
        col_fvg = "#7ecfb0"
        fig.add_hrect(
            y0=fvg_b,
            y1=fvg_t,
            fillcolor=col_fvg,
            opacity=0.12,
            line_width=1,
            line_color=col_fvg,
            line_dash="dash",
            annotation_text=f"FVG {fvg_t}-{fvg_b}",
            annotation_position="bottom right",
        )
        # Entry'nin FVG'ye gore konumu
        if entry_p >= fvg_t:
            ce_label = "UST"
        elif entry_p <= fvg_b:
            ce_label = "ALT"
        else:
            ce_label = "CE"
        fig.add_annotation(
            x=df["ts"].iloc[-1],
            y=(fvg_t + fvg_b) / 2,
            text=f"FVG {fvg_t}-{fvg_b} | Entry {ce_label}",
            showarrow=False,
            font=dict(color=col_fvg, size=9),
        )

    # ── Entry ────────────────────────────────────────────────
    entry_color = "#c97a7a" if side == "short" else "#7ecfb0"
    fig.add_hline(
        y=entry_p,
        line_color=entry_color,
        line_width=2,
        annotation_text=f"ENTRY {dir_label} @ {entry_p}",
        annotation_position="left",
    )

    # ── Initial SL/TP vs Final ───────────────────────────────
    init_sl = trade.get("initial_sl", sl_p)
    init_tp = trade.get("initial_tp", tp_p)
    trail_cnt = trade.get("trailing_count", 0)
    trail_steps = trade.get("trail_steps", [])

    # Initial SL/TP (ince cizgi)
    if init_sl != sl_p:
        fig.add_hline(
            y=init_sl,
            line_color="#ff6666",
            line_width=1,
            line_dash="dot",
            annotation_text=f"SL baslangic {init_sl}",
            annotation_position="right",
        )
    if init_tp != tp_p:
        fig.add_hline(
            y=init_tp,
            line_color="#66bb66",
            line_width=1,
            line_dash="dot",
            annotation_text=f"TP baslangic {init_tp}",
            annotation_position="right",
        )

    # Final SL
    fig.add_hline(
        y=sl_p,
        line_color="#ff4444",
        line_width=1.5,
        annotation_text=f"SL {sl_p}",
        annotation_position="right",
    )
    # Final TP
    fig.add_hline(
        y=tp_p,
        line_color="#44bb44",
        line_width=1.5,
        annotation_text=f"TP {tp_p}",
        annotation_position="right",
    )

    # ── Trail adimlari ───────────────────────────────────────
    for step in trail_steps:
        step_sl = step["sl"]
        step_tp = step["tp"]
        fvg_top_s = step.get("fvg_top")
        fvg_bot_s = step.get("fvg_bot")
        step_bar = step.get("bar")
        # Bar indeksini timestamp'e cevir
        if step_bar is not None and 0 <= step_bar < len(df):
            step_ts = df["ts"].iloc[step_bar]
            fig.add_vline(
                x=step_ts,
                line_color="#c9a84c",
                line_width=1,
                line_dash="dot",
                opacity=0.5,
            )
            tooltip = f"Trail#{trail_steps.index(step) + 1}: SL={step_sl}, TP={step_tp}"
            if fvg_top_s and fvg_bot_s:
                tooltip += f" (FVG {fvg_top_s}-{fvg_bot_s})"
            fig.add_annotation(
                x=step_ts,
                y=step_sl,
                text=tooltip,
                showarrow=True,
                arrowhead=1,
                arrowsize=1,
                arrowcolor="#c9a84c",
                font=dict(color="#c9a84c", size=8),
                bgcolor="#111",
                bordercolor="#c9a84c",
                borderwidth=1,
            )

    # ── Exit ─────────────────────────────────────────────────
    if exit_p != entry_p:
        exit_color = "#ff4444" if trade["result"] == "SL" else "#44bb44"
        fig.add_hline(
            y=exit_p,
            line_color=exit_color,
            line_width=1.8,
            line_dash="dash",
            annotation_text=f"EXIT @ {exit_p} ({trade['result']})",
            annotation_position="bottom right",
        )

    # ── Session damgasi ──────────────────────────────────────
    entry_dt = dt  # use exit time as proxy (entry time not stored)
    session = "ASIA"
    h = entry_dt.hour
    if 7 <= h < 15:
        session = "LONDON"
    elif 15 <= h < 23:
        session = "NEWYORK"
    elif h >= 23 or h < 2:
        session = "ASIA"

    pnl_str = f"{pnl:+.2f}" if pnl else "?"
    trail_str = f" · Trail {trail_cnt}x" if trail_cnt else ""
    title = (
        f"{sym} · {dir_label} · {session} {entry_dt.strftime('%H:%M')}UTC"
        f" · PnL: {pnl_str} USDT · R:R 1:2{trail_str}"
    )

    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=550,
        margin=dict(l=50, r=50, t=70, b=30),
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
