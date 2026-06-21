import os
import json
import time
from datetime import datetime, timezone

DATA = "data"
SYMS = ["BNBUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT"]
START = 1738368000000  # 2026-02-01
END = 1750636799000  # 2026-06-22

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
        os.system(f'curl -s --max-time 15 "{url}" > _tmp2.json')

        try:
            with open("_tmp2.json") as f:
                raw = f.read().strip()
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

    sz = os.path.getsize(out) / 1024 / 1024
    print(f"  DONE: {total} bars ({sz:.1f} MB)")

os.remove("_tmp2.json") if os.path.exists("_tmp2.json") else None
print("\nAll done!")
