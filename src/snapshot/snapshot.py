"""
snapshot.py — Trading snapshot, HTML only.

Düzeltmeler (v3):
  1. _fetch_ohlc: endTime + limit=120 → entry barı her zaman pencerede
  2. _find_bar_by_time: timestamp'e göre kesin eşleşme, fiyat aralığı fallback
  3. SL/TP: addLineSeries ile sadece entry→exit arası bar aralığında çizgi
  4. FVG marker: fvgBarIndex barına özel işaret
  5. Price scale: sweep/FVG/CBDR dahil tüm seviyeleri kapsayan padding
"""

import json
import logging
import os
from datetime import datetime, timezone

import pandas as pd
import requests

log = logging.getLogger("sniper.snapshot")

_SNAPSHOTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "output", "charts"
)
_BINANCE_BASE = "https://fapi.binance.com/fapi/v1/klines"
_TEMPLATE_PATH = os.environ.get(
    "HTML_TEMPLATE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_template.html"),
)

_INTERVAL_MS = 15 * 60 * 1000  # 15 dakika
_PAD_BARS = 8
_FETCH_LIMIT = 120  # 80 yetersizdi


# ─────────────────────────────────────────────
# OHLC fetch  — FIX #1: endTime + limit=120
# ─────────────────────────────────────────────


def _fetch_ohlc(sym: str, anchor_ms: int) -> list[dict] | None:
    """
    anchor_ms: trade entry veya exit timestamp (ms).
    anchor'dan geriye doğru _FETCH_LIMIT bar, ileriye PAD_BARS bar çeker.
    Bu sayede entry barı her zaman pencerenin ortasında olur.
    """
    end_ms = anchor_ms + _PAD_BARS * _INTERVAL_MS
    try:
        r = requests.get(
            _BINANCE_BASE,
            params={
                "symbol": sym,
                "interval": "15m",
                "endTime": end_ms,
                "limit": _FETCH_LIMIT,
            },
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


# ─────────────────────────────────────────────
# Bar bulma  — FIX #2: önce timestamp, fallback fiyat
# ─────────────────────────────────────────────


def _find_bar(candles: list[dict], price: float, ts_ms: int | None = None) -> int:
    """
    1. ts_ms verilmişse: o timestamp'in düştüğü 15m barını bul (kesin)
    2. Fallback: fiyatın low-high aralığına düştüğü ilk barı bul
    3. Hiçbiri yoksa: son bar
    """
    if ts_ms:
        bar_ts = (ts_ms // 1000 // 900) * 900  # 15m başına hizala
        for i, c in enumerate(candles):
            if c["time"] == bar_ts:
                return i

    for i, c in enumerate(candles):
        if c["low"] <= price <= c["high"]:
            return i

    return len(candles) - 1


# ─────────────────────────────────────────────
# Trade normalizer
# ─────────────────────────────────────────────


def normalize_trade(trade: dict) -> dict:
    n = dict(trade)
    aliases = {
        "entry": "entry_price",
        "exit": "exit_price",
        "result": "exit_reason",
        "close_time": "exit_timestamp",
    }
    for src, dst in aliases.items():
        if src in n and dst not in n:
            n[dst] = n[src]
    if "entry_price" not in n:
        n["entry_price"] = trade.get("entry", 0)
    if "exit_price" not in n:
        n["exit_price"] = trade.get("exit", trade.get("entry_price", 0))
    return n


# ─────────────────────────────────────────────
# Ana fonksiyon
# ─────────────────────────────────────────────


def capture_snapshot(
    sym: str,
    trade: dict,
    pnl: float | None = None,
    session_state=None,
) -> str | None:
    """
    Trade kapandıktan sonra çağrılır.
    HTML dosyasını output/charts/ altına kaydeder.

    Döndürür:
        Kaydedilen HTML dosya adı — örn. "BTCUSDT_2026-06-28_072600.html"
        Hata durumunda None.
    """
    trade = normalize_trade(trade)

    entry_price = trade["entry_price"]
    exit_price = trade.get("exit_price", entry_price)
    sl_price = trade["sl"]
    tp_price = trade["tp"]
    side = trade.get("side", "long")
    if pnl is None:
        pnl = trade.get("pnl", 0)

    # Timestamp — entry timestamp tercih edilir, yoksa exit
    entry_ts_ms = trade.get("timestamp") or trade.get("entry_timestamp", 0)
    exit_ts_ms = trade.get("exit_timestamp") or trade.get("close_time", 0)
    anchor_ms = entry_ts_ms or exit_ts_ms or 0

    # FIX #1: anchor_ms ile endTime'lı fetch
    candles = _fetch_ohlc(sym, anchor_ms)
    if not candles:
        return None

    # FIX #2: timestamp'e göre bar bul
    entry_bar = _find_bar(candles, entry_price, entry_ts_ms or None)
    exit_bar = _find_bar(candles, exit_price, exit_ts_ms or None)

    # Pencereyi kırp: entry-PAD … exit+PAD
    start = max(0, entry_bar - _PAD_BARS)
    end = min(len(candles), exit_bar + _PAD_BARS + 1)
    candles = candles[start:end]
    entry_bar -= start
    exit_bar -= start

    # FVG
    fvg = trade.get("trigger_fvg")
    fvg_direction = None
    fvg_top = None
    fvg_bottom = None
    fvg_bar_index = -1

    if fvg is not None:
        fvg_direction = getattr(fvg, "direction", None)
        fvg_top = getattr(fvg, "top", None)
        fvg_bottom = getattr(fvg, "bottom", None)
        fvg_bar_index = max(0, entry_bar - 3)

    fvg_top = fvg_top or trade.get("fvg_top")
    fvg_bottom = fvg_bottom or trade.get("fvg_bottom")
    fvg_direction = (
        fvg_direction or trade.get("fvg_direction") or trade.get("sweep_direction")
    )

    # fvg_bar_index: eğer trade'den gelen mutlak indeks varsa relative'e çevir
    raw_fvg_bar = trade.get("fvg_bar_index")
    if raw_fvg_bar is not None:
        rel = entry_bar + (raw_fvg_bar - trade.get("entry_bar_index", raw_fvg_bar))
        if 0 <= rel < len(candles):
            fvg_bar_index = rel
    elif fvg_bar_index == -1 and entry_bar >= 2:
        fvg_bar_index = max(0, entry_bar - 2)  # varsayılan: entry'den 2 bar önce

    # CBDR
    cbdr_high = (
        trade.get("cbdr_high") or trade.get("cbdrHigh") or trade.get("cbdr_body_high")
    )
    if cbdr_high is None and session_state is not None:
        cbdr_high = getattr(session_state, "cbdr_body_high", None)

    cbdr_low = (
        trade.get("cbdr_low") or trade.get("cbdrLow") or trade.get("cbdr_body_low")
    )
    if cbdr_low is None and session_state is not None:
        cbdr_low = getattr(session_state, "cbdr_body_low", None)

    sweep_level = trade.get("sweep_level")

    # Trail steps
    entry_bar_idx_abs = trade.get("entry_bar_index", 0)
    mapped_steps = []
    for step in trade.get("trail_steps", []):
        rel = entry_bar + (step.get("bar", 0) - entry_bar_idx_abs)
        if 0 <= rel < len(candles):
            mapped_steps.append(
                {
                    "bar": rel,
                    "sl": step["sl"],
                    "tp": step.get("tp", 0),
                }
            )

    # FIX #5: price scale — tüm seviyeleri kapsayan min/max
    all_prices = (
        [c["high"] for c in candles]
        + [c["low"] for c in candles]
        + [
            v
            for v in [
                entry_price,
                exit_price,
                sl_price,
                cbdr_high,
                cbdr_low,
                fvg_top,
                fvg_bottom,
                sweep_level,
            ]
            if v is not None
        ]
    )
    price_min = min(all_prices)
    price_max = max(all_prices)
    # TP chart dışında tutulacak (çok uzakta olabilir), sadece axis label'da göster
    price_pad = (price_max - price_min) * 0.15

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
            "exitReason": trade.get("exit_reason") or trade.get("result"),
            "cbdrHigh": cbdr_high,
            "cbdrLow": cbdr_low,
            "fvgTop": fvg_top,
            "fvgBottom": fvg_bottom,
            "fvgDirection": fvg_direction,
            "fvgBarIndex": fvg_bar_index,
            "sweepLevel": sweep_level,
            "entryBar": entry_bar,
            "exitBar": exit_bar,
            "trailSteps": mapped_steps,
            "pnl": pnl,
            "sym": sym,
            "trailingCount": trade.get("trailing_count", 0),
            # FIX #5: JS'e hazır scale sınırları
            "priceMin": price_min - price_pad,
            "priceMax": price_max + price_pad,
        }
    )

    if not os.path.exists(_TEMPLATE_PATH):
        log.warning("[SNAPSHOT] template bulunamadi: %s", _TEMPLATE_PATH)
        return None

    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read().replace("__DATA__", payload)

    os.makedirs(_SNAPSHOTS_DIR, exist_ok=True)

    dt = (
        datetime.fromtimestamp(exit_ts_ms / 1000, tz=timezone.utc)
        if exit_ts_ms
        else datetime.now(timezone.utc)
    )
    filename = f"{sym}_{dt.strftime('%Y-%m-%d_%H%M%S')}.html"
    outpath = os.path.join(_SNAPSHOTS_DIR, filename)

    try:
        with open(outpath, "w", encoding="utf-8") as f:
            f.write(html)
        log.info("[SNAPSHOT] %s -> %s", sym, filename)
        return filename
    except Exception as e:
        log.warning("[SNAPSHOT] %s HTML kayit hatasi: %s", sym, e)
        return None
