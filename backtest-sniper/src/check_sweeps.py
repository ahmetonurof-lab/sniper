"""
check_sweeps.py — Backtest verisinde kaç sweep oluştuğunu kontrol eder
Sweep sonrası reset yaparak 2., 3. sweep'leri de bulur.
"""

import csv
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(__file__))


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
        {
            "index": len(bars_15m),
            "ts": chunk[0]["ts"],
            "dt": chunk[0]["dt"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
        }
    )

print(f"Toplam: {len(bars_1m)} 1m bar | {len(bars_15m)} 15m bar\n")

# ── Tüm sweep'leri bul (sweep sonrası reset ile) ────────────
cbdr_high = 0.0
cbdr_low = float("inf")
cbdr_locked = False
sweeps = []

for bar in bars_15m:
    dt = bar["dt"]
    h = dt.hour
    atr = max(bar["high"] - bar["low"], bar["close"] * 0.0001)

    # CBDR body tracking (22:00-02:00)
    if h >= 22 or h < 2:
        body_high = max(bar["open"], bar["close"])
        body_low = min(bar["open"], bar["close"])
        if body_high > cbdr_high:
            cbdr_high = body_high
        if body_low < cbdr_low:
            cbdr_low = body_low

    # CBDR lock (02:00 sonra)
    if 2 <= h < 22 and not cbdr_locked and cbdr_high > 0:
        cbdr_locked = True

    # Sweep check
    if cbdr_locked:
        tolerance = atr * 0.5 if atr > 0 else 10.0

        sweep_found = False
        direction = None

        # Bullish sweep: high > cbdr_high + tol, close < cbdr_high
        if bar["high"] > cbdr_high + tolerance and bar["close"] < cbdr_high:
            sweep_found = True
            direction = "bullish"

        # Bearish sweep: low < cbdr_low - tol, close > cbdr_low
        elif bar["low"] < cbdr_low - tolerance and bar["close"] > cbdr_low:
            sweep_found = True
            direction = "bearish"

        if sweep_found:
            sweeps.append(
                {
                    "bar": bar["index"],
                    "dt": dt,
                    "dir": direction,
                    "level": cbdr_high if direction == "bullish" else cbdr_low,
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "cbdr_high": cbdr_high,
                    "cbdr_low": cbdr_low,
                }
            )
            # Reset for next sweep cycle
            cbdr_high = 0.0
            cbdr_low = float("inf")
            cbdr_locked = False

print(f"Toplam {len(sweeps)} sweep bulundu:\n")
for i, sw in enumerate(sweeps):
    print(
        f"  #{i+1:2d} | Bar {sw['bar']:4d} | {sw['dt'].strftime('%Y-%m-%d %H:%M')} UTC | {sw['dir'].upper():8s}"
    )
    print(
        f"       Level: {sw['level']:.2f} | CBDR: [{sw['cbdr_low']:.2f} - {sw['cbdr_high']:.2f}]"
    )
    print(f"       H={sw['high']:.2f} L={sw['low']:.2f} C={sw['close']:.2f}")
    print()
