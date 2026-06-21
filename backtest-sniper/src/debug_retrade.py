import csv
from datetime import datetime, timezone
from models import Bar
from session import SessionState, SessionPhase

CSV_FILE = "data/BTCUSDT_1m.csv"
bars = []
with open(CSV_FILE, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        ts = int(
            datetime.strptime(row["open_time"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000
        )
        bars.append(
            Bar(
                index=i,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                is_closed=True,
                timestamp=ts,
            )
        )

m15 = []
for i in range(0, len(bars), 15):
    c = bars[i : i + 15]
    if len(c) < 15:
        break
    m15.append(
        Bar(
            index=c[0].index,
            open=c[0].open,
            high=max(b.high for b in c),
            low=min(b.low for b in c),
            close=c[-1].close,
            volume=sum(b.volume for b in c),
            is_closed=True,
            timestamp=c[0].timestamp,
        )
    )

ss = SessionState()
WINDOW = 500
retrade_candidates = []

for scan_bar in range(WINDOW, len(m15), 5):
    current = m15[scan_bar]
    atr_val = max(current.range, current.close * 0.0001)
    try:
        entry_dt = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
    except:
        continue
    ss.update(entry_dt, current.open, current.high, current.low, current.close, atr_val)

    if (
        ss.waiting_for_retrade
        and not ss.retrade_sweep_confirmed
        and ss.trades_today == 1
    ):
        wanted = ss.retrade_sweep_direction
        tol = atr_val * 2.0
        ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        phase = (
            SessionPhase.CBDR
            if (entry_dt.hour >= 22 or entry_dt.hour < 2)
            else (
                SessionPhase.LONDON if 2 <= entry_dt.hour < 13 else SessionPhase.NEWYORK
            )
        )

        if wanted == "bullish":
            diff = current.high - ss.cbdr_high
            if diff > 0:
                retrade_candidates.append(
                    (
                        ts,
                        phase.name,
                        wanted,
                        f"cbdr_high={ss.cbdr_high:.2f}",
                        f"high={current.high:.2f}",
                        f"diff={diff:.2f}",
                        f"tol={tol:.2f}",
                        f"close={current.close:.2f}",
                        f"close<cbdr_high={current.close < ss.cbdr_high}",
                    )
                )
        else:
            diff = ss.cbdr_low - current.low
            if diff > 0:
                retrade_candidates.append(
                    (
                        ts,
                        phase.name,
                        wanted,
                        f"cbdr_low={ss.cbdr_low:.2f}",
                        f"low={current.low:.2f}",
                        f"diff={diff:.2f}",
                        f"tol={tol:.2f}",
                        f"close={current.close:.2f}",
                        f"close>cbdr_low={current.close > ss.cbdr_low}",
                    )
                )

for r in retrade_candidates[:20]:
    print(r)
print(f"\nTotal candidates: {len(retrade_candidates)}")
