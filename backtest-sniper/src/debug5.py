import csv
from datetime import datetime, timezone
from models import Bar
from session import SessionState

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
events = []

for scan_bar in range(WINDOW, len(m15), 5):
    current = m15[scan_bar]
    atr_val = max(current.range, current.close * 0.0001)
    try:
        entry_dt = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
    except:
        continue

    had_wait = ss.waiting_for_retrade
    had_trades = ss.trades_today
    cbdr_key_before = ss.cbdr_day

    ss.update(entry_dt, current.open, current.high, current.low, current.close, atr_val)

    cbdr_key_after = ss.cbdr_day
    ts = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc).strftime(
        "%m-%d %H:%M"
    )

    if cbdr_key_before != cbdr_key_after:
        events.append(
            f"CBDR RESET at {ts}  trades_today: {had_trades} -> {ss.trades_today}  waiting: {had_wait} -> {ss.waiting_for_retrade}  cbdr_high: {ss.cbdr_high:.2f}  cbdr_low: {ss.cbdr_low:.2f}"
        )

    if had_wait and not ss.waiting_for_retrade:
        events.append(
            f"WAIT CLEARED at {ts}  trades: {ss.trades_today}  cbdr_key: {ss.cbdr_day}  date: {ss.last_date}"
        )

for e in events[:30]:
    print(e)
print(f"\nTotal events: {len(events)}")
