"""
visualize_cbdr_zoom.py — Sweep barına zoom edilmiş CBDR görselleştirme
"""

import csv
import os
import sys
from datetime import UTC, datetime

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
except ImportError:
    print("matplotlib yok: pip install matplotlib")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(__file__))
from session import SessionState


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

# ── CBDR + Sweep simülasyonu ────────────────────────────────
ss = SessionState()
cbdr_lock_bar = None
sweep_bar = None
sweep_direction = None
sweep_level = None

for bar in bars_15m:
    dt = bar["dt"]
    atr = max(bar["high"] - bar["low"], bar["close"] * 0.0001)
    ss.update(dt, bar["open"], bar["high"], bar["low"], bar["close"], atr)

    if ss.cbdr_locked and cbdr_lock_bar is None:
        cbdr_lock_bar = bar["index"]
        cbdr_high = ss.cbdr_body_high
        cbdr_low = ss.cbdr_body_low

    if ss.sweep_confirmed and sweep_bar is None:
        sweep_bar = bar["index"]
        sweep_direction = ss.sweep_direction
        sweep_level = ss.sweep_level
        break

if sweep_bar is None:
    print("SWEEP BULUNAMADI")
    sys.exit(0)

print(f"CBDR lock: bar={cbdr_lock_bar} range=[{cbdr_low:.2f} - {cbdr_high:.2f}]")
print(f"Sweep: bar={sweep_bar} dir={sweep_direction} level={sweep_level:.2f}")

# ── Zoom: sweep_bar civarı ±30 bar ─────────────────────────
zoom = 30
start = max(0, sweep_bar - zoom)
end = min(len(bars_15m), sweep_bar + zoom + 1)

fig, ax = plt.subplots(1, 1, figsize=(16, 8))
fig.suptitle(
    f"{SYMBOL} — Sweep Barına Zoom (±{zoom} bar)", fontsize=14, fontweight="bold"
)

# Her barı çiz (OHLC)
for i in range(start, end):
    bar = bars_15m[i]
    x = i
    color = "lime" if bar["close"] >= bar["open"] else "red"

    # Wick
    ax.plot([x, x], [bar["low"], bar["high"]], color=color, linewidth=1.5, zorder=2)
    # Body
    body_top = max(bar["open"], bar["close"])
    body_bot = min(bar["open"], bar["close"])
    ax.bar(x, body_top - body_bot, bottom=body_bot, color=color, width=0.6, zorder=3)

# CBDR range
ax.axhspan(
    cbdr_low,
    cbdr_high,
    alpha=0.25,
    color="yellow",
    label=f"CBDR [{cbdr_low:.2f}-{cbdr_high:.2f}]",
)
ax.axhline(
    y=cbdr_high,
    color="gold",
    linestyle="--",
    linewidth=2,
    label=f"CBDR High={cbdr_high:.2f}",
)
ax.axhline(
    y=cbdr_low,
    color="gold",
    linestyle="--",
    linewidth=2,
    label=f"CBDR Low={cbdr_low:.2f}",
)

# Tolerance çizgileri
atr_at_sweep = max(
    bars_15m[sweep_bar]["high"] - bars_15m[sweep_bar]["low"],
    bars_15m[sweep_bar]["close"] * 0.0001,
)
tolerance = atr_at_sweep * 0.5
ax.axhline(
    y=cbdr_high + tolerance,
    color="orange",
    linestyle=":",
    linewidth=1,
    alpha=0.7,
    label=f"CBDR High+Tol={cbdr_high+tolerance:.2f}",
)
ax.axhline(
    y=cbdr_low - tolerance,
    color="orange",
    linestyle=":",
    linewidth=1,
    alpha=0.7,
    label=f"CBDR Low-Tol={cbdr_low-tolerance:.2f}",
)

# Sweep bar vurgusu
sweep_b = bars_15m[sweep_bar]
sweep_color = "lime" if sweep_direction == "bullish" else "red"
ax.axvline(x=sweep_bar, color=sweep_color, linewidth=3, alpha=0.5, zorder=1)
ax.scatter(
    [sweep_bar],
    [sweep_b["close"]],
    color=sweep_color,
    s=200,
    zorder=5,
    marker="*",
    edgecolors="black",
    linewidths=1,
)

# Sweep bar detay yazısı
detail = (
    f"SWEEP BAR #{sweep_bar}\n"
    f"Dir: {sweep_direction.upper()}\n"
    f"O={sweep_b['open']:.2f} H={sweep_b['high']:.2f}\n"
    f"L={sweep_b['low']:.2f} C={sweep_b['close']:.2f}\n"
    f"CBDR: [{cbdr_low:.2f}-{cbdr_high:.2f}]\n"
    f"Tol: ±{tolerance:.2f}"
)
ax.text(
    sweep_bar + 2,
    cbdr_high + (cbdr_high - cbdr_low) * 0.5,
    detail,
    fontsize=8,
    color=sweep_color,
    fontfamily="monospace",
    bbox=dict(boxstyle="round,pad=0.3", facecolor="black", alpha=0.7),
)

# CBDR lock bar
if cbdr_lock_bar is not None and start <= cbdr_lock_bar < end:
    ax.axvline(x=cbdr_lock_bar, color="orange", linewidth=2, linestyle="-.", alpha=0.7)
    ax.text(
        cbdr_lock_bar,
        cbdr_high + (cbdr_high - cbdr_low) * 0.3,
        "CBDR\nLOCK",
        color="orange",
        fontsize=8,
        ha="center",
    )

# Session arka plan
for i in range(start, end):
    bar = bars_15m[i]
    h = bar["dt"].hour
    if h >= 22 or h < 2:
        ax.axvspan(i - 0.5, i + 0.5, alpha=0.08, color="blue")
    elif 2 <= h < 13:
        ax.axvspan(i - 0.5, i + 0.5, alpha=0.05, color="green")
    else:
        ax.axvspan(i - 0.5, i + 0.5, alpha=0.05, color="red")

ax.set_xlabel("15m Bar Index")
ax.set_ylabel("Fiyat")
ax.legend(loc="upper left", fontsize=8)
ax.grid(True, alpha=0.15)

# Y eksenini CBDR civarına zoom
y_margin = (cbdr_high - cbdr_low) * 0.5
ax.set_ylim(cbdr_low - y_margin, cbdr_high + y_margin)

plt.tight_layout()
output_path = os.path.join(
    os.path.dirname(__file__), "..", "reports", f"{SYMBOL}_cbdr_zoom.png"
)
os.makedirs(os.path.dirname(output_path), exist_ok=True)
plt.savefig(output_path, dpi=150, bbox_inches="tight")
print(f"Grafik: {output_path}")
