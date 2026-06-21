"""
visualize_cbdr.py — CBDR range + sweep detection görselleştirme
BTCUSDT 1m data kullanarak CBDR body range ve sweep oluşumunu çizer.
"""

import csv
import os
import sys
from datetime import UTC, datetime

# matplotlib yoksa uyar
try:
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D
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

print(f"Toplam {len(bars_1m)} bar yüklendi")

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

print(f"15m: {len(bars_15m)} bar")

# ── CBDR + Sweep simülasyonu ────────────────────────────────
ss = SessionState()
cbdr_body_high_history = []  # (bar_idx, value)
cbdr_body_low_history = []
cbdr_lock_bar = None
sweep_bar = None
sweep_direction = None

for bar in bars_15m:
    dt = bar["dt"]
    h = dt.hour
    atr = max(bar["high"] - bar["low"], bar["close"] * 0.0001)
    ss.update(dt, bar["open"], bar["high"], bar["low"], bar["close"], atr)

    cbdr_body_high_history.append(ss.cbdr_body_high if ss.cbdr_body_high > 0 else None)
    cbdr_body_low_history.append(
        ss.cbdr_body_low if ss.cbdr_body_low < float("inf") else None
    )

    if ss.cbdr_locked and cbdr_lock_bar is None:
        cbdr_lock_bar = bar["index"]

    if ss.sweep_confirmed and sweep_bar is None:
        sweep_bar = bar["index"]
        sweep_direction = ss.sweep_direction
        print(
            f"SWEEP DETECTED: bar={sweep_bar} dir={sweep_direction} level={ss.sweep_level:.2f}"
        )
        print(f"  CBDR range: [{ss.cbdr_body_low:.2f} - {ss.cbdr_body_high:.2f}]")
        print(
            f"  Sweep bar: high={bar['high']:.2f} low={bar['low']:.2f} close={bar['close']:.2f}"
        )
        break  # ilk sweep'ten sonra dur

if sweep_bar is None:
    print("SWEEP BULUNAMADI — tüm data tarandı")
    print(f"  CBDR locked: {ss.cbdr_locked}")
    if ss.cbdr_locked:
        print(f"  CBDR range: [{ss.cbdr_body_low:.2f} - {ss.cbdr_body_high:.2f}]")

# ── Çizim ──────────────────────────────────────────────────
# Son 500 barı çiz (sweep varsa sweep etrafında)
window = 500
start = max(0, (sweep_bar if sweep_bar else len(bars_15m)) - window)
end = min(len(bars_15m), start + window + 200)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 10), height_ratios=[3, 1])
fig.suptitle(f"{SYMBOL} — CBDR Range + Sweep Detection", fontsize=14, fontweight="bold")

# ── Üst panel: Fiyat + CBDR range ──────────────────────────
closes = [b["close"] for b in bars_15m[start:end]]
highs = [b["high"] for b in bars_15m[start:end]]
lows = [b["low"] for b in bars_15m[start:end]]
xs = list(range(start, end))

ax1.plot(xs, closes, color="#aaaaaa", linewidth=0.8, label="Close", zorder=1)
ax1.fill_between(xs, lows, highs, alpha=0.15, color="#666666", label="Range")

# CBDR body range
ch_hist = cbdr_body_high_history[start:end]
cl_hist = cbdr_body_low_history[start:end]

# CBDR range çizgileri (lock sonrası sabit)
if cbdr_lock_bar is not None and cbdr_lock_bar >= start:
    lock_x = cbdr_lock_bar
    lock_ch = cbdr_body_high_history[cbdr_lock_bar]
    lock_cl = cbdr_body_low_history[cbdr_lock_bar]

    # CBDR range bölgesi
    ax1.axhspan(
        lock_cl,
        lock_ch,
        xmin=(lock_x - start) / (end - start),
        alpha=0.2,
        color="yellow",
        label=f"CBDR Range [{lock_cl:.2f}-{lock_ch:.2f}]",
    )
    ax1.axhline(y=lock_ch, color="gold", linestyle="--", linewidth=1.5, alpha=0.8)
    ax1.axhline(y=lock_cl, color="gold", linestyle="--", linewidth=1.5, alpha=0.8)
    ax1.axvline(x=lock_x, color="orange", linestyle=":", linewidth=1, alpha=0.7)
    ax1.text(
        lock_x,
        lock_ch + (lock_ch - lock_cl) * 0.1,
        "CBDR LOCK",
        color="orange",
        fontsize=8,
        ha="center",
    )

# Sweep bar vurgusu
if sweep_bar is not None and start <= sweep_bar < end:
    sweep_b = bars_15m[sweep_bar]
    color = "lime" if sweep_direction == "bullish" else "red"
    ax1.axvline(x=sweep_bar, color=color, linewidth=2, alpha=0.8, zorder=3)
    ax1.scatter(
        [sweep_bar], [sweep_b["close"]], color=color, s=100, zorder=5, marker="*"
    )
    ax1.text(
        sweep_bar,
        sweep_b["high"] * 1.002,
        f"SWEEP\n{sweep_direction.upper()}",
        color=color,
        fontsize=9,
        ha="center",
        fontweight="bold",
    )

# Session arka planları
for i, bar in enumerate(bars_15m[start:end]):
    x = start + i
    h = bar["dt"].hour
    if h >= 22 or h < 2:
        ax1.axvspan(x - 0.5, x + 0.5, alpha=0.05, color="blue")  # CBDR/ASIA
    elif 2 <= h < 13:
        ax1.axvspan(x - 0.5, x + 0.5, alpha=0.03, color="green")  # LONDON
    else:
        ax1.axvspan(x - 0.5, x + 0.5, alpha=0.03, color="red")  # NEWYORK

ax1.set_ylabel("Fiyat")
ax1.legend(loc="upper left", fontsize=8)
ax1.set_title("Fiyat + CBDR Body Range (sarı bölge) + Sweep")
ax1.grid(True, alpha=0.2)

# ── Alt panel: Session timeline ────────────────────────────
session_colors = []
for bar in bars_15m[start:end]:
    h = bar["dt"].hour
    if h >= 22 or h < 2:
        session_colors.append("blue")
    elif 2 <= h < 13:
        session_colors.append("green")
    else:
        session_colors.append("red")

ax2.bar(xs, [1] * len(xs), color=session_colors, alpha=0.3, width=1)
ax2.set_ylabel("Session")
ax2.set_yticks([])
ax2.set_xlabel("15m Bar Index")
ax2.set_title("Session: Blue=CBDR/ASIA Green=LONDON Red=NEWYORK")
ax2.grid(True, alpha=0.2)

# Legend
legend_elements = [
    mpatches.Patch(color="blue", alpha=0.3, label="CBDR/ASIA (22-02 UTC)"),
    mpatches.Patch(color="green", alpha=0.3, label="LONDON (02-13 UTC)"),
    mpatches.Patch(color="red", alpha=0.3, label="NEWYORK (13-22 UTC)"),
    Line2D([0], [0], color="gold", linestyle="--", label="CBDR Body Range"),
    Line2D([0], [0], color="lime", linewidth=2, label="Bullish Sweep"),
    Line2D([0], [0], color="red", linewidth=2, label="Bearish Sweep"),
]
ax2.legend(handles=legend_elements, loc="upper right", fontsize=7)

plt.tight_layout()

output_path = os.path.join(
    os.path.dirname(__file__), "..", "reports", f"{SYMBOL}_cbdr_sweep.png"
)
os.makedirs(os.path.dirname(output_path), exist_ok=True)
plt.savefig(output_path, dpi=150, bbox_inches="tight")
print(f"\nGrafik kaydedildi: {output_path}")
