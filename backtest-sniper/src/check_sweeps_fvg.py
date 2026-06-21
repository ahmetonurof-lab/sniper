"""
check_sweeps_fvg.py — Her sweep sonrası FVG var mı kontrol eder
"""

import csv
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(__file__))
from fvg import detect_fvgs
from models import Bar


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
CSV_FILE = os.path.join(DATA_DIR, f"{SYMBOL}_1m.csv")

# ── Veri yükle ──────────────────────────────────────────────
bars_1m = []
with open(CSV_FILE, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        ts = int(
            datetime.strptime(row["open_time"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000
        )
        bars_1m.append(
            {
                "index": i,
                "ts": ts,
                "dt": datetime.fromtimestamp(ts / 1000, tz=UTC),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        )

# ── 15m resample ────────────────────────────────────────────
bars_15m = []
for i in range(0, len(bars_1m), 15):
    chunk = bars_1m[i : i + 15]
    if len(chunk) < 15:
        break
    bars_15m.append(
        Bar(
            index=len(bars_15m),
            open=chunk[0]["open"],
            high=max(b["high"] for b in chunk),
            low=min(b["low"] for b in chunk),
            close=chunk[-1]["close"],
            volume=sum(b["volume"] for b in chunk) if "volume" in chunk[0] else 0,
            timestamp=chunk[0]["ts"],
            is_closed=True,
        )
    )

# Bar dict listesi (session tracking için)
bar_dicts = []
for i in range(0, len(bars_1m), 15):
    chunk = bars_1m[i : i + 15]
    if len(chunk) < 15:
        break
    bar_dicts.append(
        {
            "index": len(bar_dicts),
            "ts": chunk[0]["ts"],
            "dt": chunk[0]["dt"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
        }
    )

print(f"Toplam: {len(bars_15m)} 15m bar | FVG_SIZE: 10.0\n")

# ── Tüm sweep'leri bul + FVG kontrolü ───────────────────────
cbdr_high = 0.0
cbdr_low = float("inf")
cbdr_locked = False
sweeps = []

MIN_FVG_SIZE = 10.0

for bar_idx, (bar, bar_d) in enumerate(zip(bars_15m, bar_dicts)):
    dt = bar_d["dt"]
    h = dt.hour
    atr = max(bar.high - bar.low, bar.close * 0.0001)

    # CBDR body tracking (22:00-02:00)
    if h >= 22 or h < 2:
        body_high = max(bar.open, bar.close)
        body_low = min(bar.open, bar.close)
        if body_high > cbdr_high:
            cbdr_high = body_high
        if body_low < cbdr_low:
            cbdr_low = body_low

    # CBDR lock
    if 2 <= h < 22 and not cbdr_locked and cbdr_high > 0:
        cbdr_locked = True

    # Sweep check
    if cbdr_locked:
        tolerance = atr * 0.5 if atr > 0 else 10.0
        sweep_found = False
        direction = None

        if bar.high > cbdr_high + tolerance and bar.close < cbdr_high:
            sweep_found = True
            direction = "bullish"
        elif bar.low < cbdr_low - tolerance and bar.close > cbdr_low:
            sweep_found = True
            direction = "bearish"

        if sweep_found:
            # FVG tarama: sweep bar'ından önceki 100 bar
            lookback = 100
            start = max(0, bar_idx - lookback)
            segment = bars_15m[start : bar_idx + 1]

            fvgs = detect_fvgs(
                segment,
                lookback=len(segment),
                timeframe="15m",
                min_fvg_size=MIN_FVG_SIZE,
            )

            # Yöne uygun FVG var mı?
            matching_fvgs = [f for f in fvgs if f.direction == direction]

            sweeps.append(
                {
                    "bar": bar_idx,
                    "dt": dt,
                    "dir": direction,
                    "level": cbdr_high if direction == "bullish" else cbdr_low,
                    "cbdr_high": cbdr_high,
                    "cbdr_low": cbdr_low,
                    "fvg_count": len(matching_fvgs),
                    "total_fvgs": len(fvgs),
                }
            )

            # Reset
            cbdr_high = 0.0
            cbdr_low = float("inf")
            cbdr_locked = False

# ── Sonuç ───────────────────────────────────────────────────
fvg_yes = [s for s in sweeps if s["fvg_count"] > 0]
fvg_no = [s for s in sweeps if s["fvg_count"] == 0]

print(f"Toplam {len(sweeps)} sweep:")
print(f"  FVG var : {len(fvg_yes)} ({len(fvg_yes)/len(sweeps)*100:.1f}%)")
print(f"  FVG yok : {len(fvg_no)} ({len(fvg_no)/len(sweeps)*100:.1f}%)")
print()

print("=== FVG YOK (kayıp sweep'ler) ===")
for s in fvg_no:
    print(
        f"  Bar {s['bar']:4d} | {s['dt'].strftime('%Y-%m-%d %H:%M')} UTC | {s['dir'].upper():8s} | CBDR: [{s['cbdr_low']:.2f} - {s['cbdr_high']:.2f}]"
    )

print()
print("=== FVG VAR (kullanılabilir sweep'ler) ===")
for s in fvg_yes[:10]:
    print(
        f"  Bar {s['bar']:4d} | {s['dt'].strftime('%Y-%m-%d %H:%M')} UTC | {s['dir'].upper():8s} | FVG: {s['fvg_count']} adet"
    )
