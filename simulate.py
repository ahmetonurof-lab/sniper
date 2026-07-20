"""
simulate.py — Canli bot koduyla paper trade simulasyonu (multiprocess).

2.5 yillik 1m feather verisini sniper bot'un on_1m/on_15m
callback'lerine feed eder, paper mode'da calistirir.

Kullanim:
  python simulate.py                      # tum semboller, 1 worker
  python simulate.py --workers 6          # 6 parallel process
  python simulate.py --symbols BNBUSDT    # tek sembol
  python simulate.py --days 30            # son 30 gun
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["BINANCE_API_KEY"] = ""  # paper mode
os.environ["EXIT_LIFECYCLE_SERVICE_ENABLED"] = "true"
os.environ["PROTECTION_LIFECYCLE_SERVICE_ENABLED"] = "true"
os.environ["WS_EVENT_NORMALIZATION_ENABLED"] = "true"

import pandas as pd  # noqa: E402

pd.set_option("future.no_silent_downcasting", True)

from models import Bar  # noqa: E402

SYMBOLS = [
    "BNBUSDT",
    "SOLUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "XRPUSDT",
    "ATOMUSDT",
    "ADAUSDT",
    "APTUSDT",
    "DOTUSDT",
    "NEARUSDT",
    "SUIUSDT",
    "OPUSDT",
    "ARBUSDT",
    "INJUSDT",
    "ALGOUSDT",
    "AAVEUSDT",
    "UNIUSDT",
    "DOGEUSDT",
    "TIAUSDT",
    "SEIUSDT",
    "ONDOUSDT",
    "PYTHUSDT",
    "RENDERUSDT",
    "ENAUSDT",
    "STRKUSDT",
    "GMXUSDT",
    "DYDXUSDT",
    "LDOUSDT",
]

DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "backtest-sniper", "src", "data", "daily"
)


def load_bars(symbol: str, max_bars: int | None = None) -> list[Bar]:
    path = os.path.join(DATA_DIR, f"{symbol}_1m_raw.feather")
    if not os.path.isfile(path):
        return []
    df = pd.read_feather(path)
    ts_ms = (
        pd.to_datetime(df["open_time"], format="%Y-%m-%d %H:%M:%S")
        .values.astype("datetime64[ms]")
        .astype("int64")
    )
    n = min(len(df), max_bars or len(df))
    bars = []
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    for i in range(n):
        bars.append(
            Bar(
                index=i,
                open=float(o[i]),
                high=float(h[i]),
                low=float(lo[i]),
                close=float(c[i]),
                volume=float(v[i]),
                is_closed=True,
                timestamp=int(ts_ms[i]),
            )
        )
    return bars


def build_15m_bars(bars_1m: list[Bar]) -> list[Bar]:
    if not bars_1m:
        return []
    grouped: dict[int, list[Bar]] = {}
    for b in bars_1m:
        bucket = b.timestamp // (15 * 60 * 1000)
        grouped.setdefault(bucket, []).append(b)
    result = []
    for bucket in sorted(grouped):
        chunk = grouped[bucket]
        result.append(
            Bar(
                index=len(result),
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low for b in chunk),
                close=chunk[-1].close,
                volume=sum(b.volume for b in chunk),
                is_closed=True,
                timestamp=chunk[-1].timestamp,
            )
        )
    return result


def _run_worker(syms: list[str], days: int | None) -> dict:
    """Tek process'te bir grup sembol calistir."""
    import asyncio
    from bot import PaperTrader

    bar_cache: dict[str, list[Bar]] = {}
    bar_15m_cache: dict[str, list[Bar]] = {}

    for sym in syms:
        max_1m = days * 24 * 60 if days else None
        bars = load_bars(sym, max_1m)
        if bars:
            bar_cache[sym] = bars
            bar_15m_cache[sym] = build_15m_bars(bars)

    if not bar_cache:
        return {"syms": syms, "trades": 0, "wins": 0, "losses": 0, "elapsed": 0}

    async def _loop():
        t0 = time.time()
        bot = PaperTrader(symbols=list(bar_cache.keys()))

        # 15m barlari hub'a prefill et (trailing icin gerekli)
        for sym in bar_cache:
            if sym in bar_15m_cache and bar_15m_cache[sym]:
                await bot.hub.prefill_bars(sym, "15m", bar_15m_cache[sym])

        total_bars = 0
        max_len = max(len(b) for b in bar_cache.values())

        for step in range(max_len):
            for sym in bar_cache:
                bars_1m = bar_cache[sym]
                if step >= len(bars_1m):
                    continue
                chunk = bars_1m[max(0, step - 1) : step + 1]
                await bot.on_1m(sym, chunk)
                total_bars += 1

            if step > 0 and step % 15 == 0:
                for sym in bar_cache:
                    bars_15m = bar_15m_cache[sym]
                    idx = step // 15
                    if idx >= len(bars_15m):
                        continue
                    chunk = bars_15m[max(0, idx - 4) : idx + 1]
                    if len(chunk) >= 2:
                        await bot.on_15m(sym, chunk)

        elapsed = time.time() - t0
        history = getattr(bot, "trades", [])
        wins = sum(1 for t in history if t.get("pnl", 0) > 0)
        losses = sum(1 for t in history if t.get("pnl", 0) < 0)
        return {
            "syms": list(bar_cache.keys()),
            "trades": len(history),
            "wins": wins,
            "losses": losses,
            "bars": total_bars,
            "elapsed": elapsed,
        }

    return asyncio.run(_loop())


def run_simulation(symbols: list[str], days: int | None, workers: int = 1):
    print("=" * 60)
    print("  SIMULASYON — sniper bot canli kodu (multiprocess)")
    print(f"  Semboller: {len(symbols)}")
    print(f"  Workers: {workers}")
    if days:
        print(f"  Gun araligi: son {days} gun")
    print(f"  ExitLifecycleService: {os.environ.get('EXIT_LIFECYCLE_SERVICE_ENABLED')}")
    print(
        f"  ProtectionLifecycleService: {os.environ.get('PROTECTION_LIFECYCLE_SERVICE_ENABLED')}"
    )
    print("=" * 60)

    if workers <= 1:
        result = _run_worker(symbols, days)
        print(
            f"\n  [{','.join(result['syms'][:3])}...] "
            f"trades={result['trades']} win={result['wins']} loss={result['losses']} "
            f"bars={result['bars']} time={result['elapsed']:.1f}s"
        )
        print("=" * 60)
        return

    # Multiprocess: sembolleri worker'lara bol
    chunk_size = max(1, len(symbols) // workers)
    chunks = [symbols[i : i + chunk_size] for i in range(0, len(symbols), chunk_size)]
    chunks = chunks[:workers]  # limit to worker count

    t0 = time.time()
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_bars = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_worker, chunk, days): chunk for chunk in chunks}
        for fut in as_completed(futures):
            result = fut.result()
            ts = result["trades"]
            w = result["wins"]
            loss = result["losses"]
            total_trades += ts
            total_wins += w
            total_losses += loss
            total_bars += result["bars"]
            rate = w / (w + loss) * 100 if (w + loss) else 0
            syms_str = ",".join(result["syms"][:4])
            print(
                f"  [{syms_str}...] trades={ts} win={w} loss={loss} "
                f"rate={rate:.0f}% bars={result['bars']} time={result['elapsed']:.1f}s",
                flush=True,
            )

    elapsed = time.time() - t0
    rate = (
        total_wins / (total_wins + total_losses) * 100
        if (total_wins + total_losses)
        else 0
    )
    print(
        f"\n  TOPLAM: trades={total_trades} win={total_wins} loss={total_losses} "
        f"rate={rate:.1f}% bars={total_bars} time={elapsed:.1f}s"
    )
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    run_simulation(args.symbols, args.days, args.workers)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
