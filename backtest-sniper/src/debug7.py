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
cbdr_data = {}

for scan_bar in range(WINDOW, len(m15), 5):
    current = m15[scan_bar]
    atr_val = max(current.range, current.close * 0.0001)
    try:
        entry_dt = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
    except:
        continue
    ss.update(entry_dt, current.open, current.high, current.low, current.close, atr_val)

    phase = (
        SessionPhase.CBDR
        if (entry_dt.hour >= 22 or entry_dt.hour < 2)
        else (SessionPhase.LONDON if 2 <= entry_dt.hour < 13 else SessionPhase.NEWYORK)
    )
    cbdr_key = ss.cbdr_day

    if phase == SessionPhase.CBDR:
        if cbdr_key not in cbdr_data:
            cbdr_data[cbdr_key] = {
                "high": 0,
                "low": float("inf"),
                "body_high": 0,
                "body_low": float("inf"),
            }
        cbdr_data[cbdr_key]["high"] = max(cbdr_data[cbdr_key]["high"], current.high)
        cbdr_data[cbdr_key]["low"] = min(cbdr_data[cbdr_key]["low"], current.low)
        cbdr_data[cbdr_key]["body_high"] = max(
            cbdr_data[cbdr_key]["body_high"], max(current.open, current.close)
        )
        cbdr_data[cbdr_key]["body_low"] = min(
            cbdr_data[cbdr_key]["body_low"], min(current.open, current.close)
        )

# For each CBDR day, check if NY session prices approach CBDR high/low
for day in sorted(cbdr_data.keys())[:5]:
    d = cbdr_data[day]
    print(
        f"CBDR {day}: high={d['high']:.2f} low={d['low']:.2f} body_high={d['body_high']:.2f} body_low={d['body_low']:.2f}"
    )
