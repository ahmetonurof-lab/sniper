import os
import time
from datetime import datetime, timezone

import ccxt

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT", "XRPUSDT"]
DATA = os.path.join(os.path.dirname(__file__), "data")

START_TS = int(datetime(2025, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_TS = int(datetime(2025, 8, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)

exchange = ccxt.binance({"enableRateLimit": True})

for sym in SYMS:
    out = os.path.join(DATA, f"{sym}_1m.csv")
    with open(out, "w") as f:
        f.write("open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_buy_base,taker_buy_quote\n")

    cur = START_TS
    total = 0
    fails = 0

    while cur < END_TS:
        try:
            data = exchange.fetch_ohlcv(sym, "1m", since=cur, limit=1000)
            if not data:
                fails += 1
                if fails > 30:
                    break
                cur += 60000000
                continue
        except Exception:
            fails += 1
            if fails > 30:
                break
            cur += 60000000
            time.sleep(2)
            continue

        fails = 0
        with open(out, "a") as f:
            for k in data:
                ot = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                ct = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{ot},{k[1]},{k[2]},{k[3]},{k[4]},{k[5]},{ct},0,0,0,0\n")

        total += len(data)
        cur = data[-1][0] + 60000

        if total % 50000 == 0:
            print(f"  {sym}: {total} bars", flush=True)

    sz = os.path.getsize(out) / 1024 / 1024
    print(f"  {sym}: DONE {total} bars ({sz:.1f} MB)", flush=True)

print("\nAll fresh data downloaded!", flush=True)
