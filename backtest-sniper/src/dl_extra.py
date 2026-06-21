import os
import json
import time
import subprocess
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(__file__), "data")
SYMS = ["BNBUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT"]
# 2026-02-01 00:00:00 -> 2026-06-22 23:59:59 UTC
START = 1738368000000
END = 1750636799000

for sym in SYMS:
    print(f"\n=== {sym} ===")
    out = os.path.join(DATA, f"{sym}_1m.csv")
    with open(out, "w") as f:
        f.write(
            "open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_buy_base,taker_buy_quote\n"
        )

    cur = START
    total = 0
    chk = 0
    fails = 0

    while cur < END:
        chk += 1
        url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1m&startTime={cur}&limit=1000"
        try:
            res = subprocess.run(
                ["curl", "-s", "--max-time", "15", url],
                capture_output=True,
                text=True,
                timeout=20,
            )
            raw = res.stdout.strip()
            if not raw or raw == "[]":
                fails += 1
                if fails > 30:
                    break
                cur += 60000000
                continue
            data = json.loads(raw)
            if isinstance(data, dict):
                fails += 1
                cur += 60000000
                continue
        except:
            fails += 1
            if fails > 30:
                break
            cur += 60000000
            time.sleep(1)
            continue

        fails = 0
        with open(out, "a") as f:
            for k in data:
                ot = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                ct = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                f.write(
                    f"{ot},{k[1]},{k[2]},{k[3]},{k[4]},{k[5]},{ct},{k[7]},{k[8]},{k[9]},{k[10]}\n"
                )

        total += len(data)
        cur = data[-1][6] + 60000

        if chk % 50 == 0:
            print(f"  {chk} chunks, {total} bars")
        if chk % 15 == 0:
            time.sleep(0.3)

    print(f"  DONE: {total} bars ({os.path.getsize(out)/1024/1024:.1f} MB)")

print("\nAll done!")
