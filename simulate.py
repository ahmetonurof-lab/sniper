"""
simulate.py — Canli bot koduyla paper trade simulasyonu (multiprocess).
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["BINANCE_API_KEY"] = ""
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
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "be": 0,
            "total_pnl": 0,
            "total_fee": 0,
            "trail_count": 0,
            "bars": 0,
            "elapsed": 0,
        }

    async def _loop():
        from datetime import datetime, timezone

        t0 = time.time()
        bot = PaperTrader(symbols=list(bar_cache.keys()))

        for sym in bar_cache:
            if sym in bar_15m_cache and bar_15m_cache[sym]:
                bot.hub.prefill_bars(sym, "15m", bar_15m_cache[sym])

        total_bars = 0
        c15m = 0
        max_len = max(len(b) for b in bar_cache.values())
        progress_every = max(1, max_len // 20)

        for step in range(max_len):
            if step % progress_every == 0:
                pct = step / max_len * 100
                sym_label = ",".join(bar_cache.keys())
                print(
                    f"  [{sym_label}] %{pct:.0f} step={step}/{max_len} "
                    f"bars={total_bars} trades={len(bot.trades)} c15m={c15m}",
                    flush=True,
                )
            for sym in bar_cache:
                bars_1m = bar_cache[sym]
                if step >= len(bars_1m):
                    continue
                chunk = bars_1m[max(0, step - 1) : step + 1]
                await bot.on_1m(sym, chunk)
                total_bars += 1

            if step > 0 and step % 15 == 0:
                c15m += 1
                for sym in bar_cache:
                    bars_15m = bar_15m_cache[sym]
                    idx = step // 15
                    if idx >= len(bars_15m):
                        continue
                    chunk = bars_15m[max(0, idx - 4) : idx + 1]
                    if len(chunk) >= 2:
                        await bot.on_15m(sym, chunk)

        for sym in bar_cache:
            ss = bot.states.get(sym)
            if ss:
                print(
                    f"  [{sym}] END CBDR={ss.cbdr_locked} "
                    f"sweep={ss.sweep_confirmed} dir={ss.sweep_direction} "
                    f"fvg={ss.fvg_ready} "
                    f"start={ss.cbdr_start} end={ss.cbdr_end} "
                    f"high={ss._cbdr.body_high:.4f} low={ss._cbdr.body_low:.4f}",
                    flush=True,
                )
            # Sample bar timestamps
            bars_1m = bar_cache.get(sym)
            if bars_1m:
                first_dt = datetime.fromtimestamp(
                    bars_1m[0].timestamp / 1000, tz=timezone.utc
                )
                last_dt = datetime.fromtimestamp(
                    bars_1m[-1].timestamp / 1000, tz=timezone.utc
                )
                print(
                    f"  [{sym}] data: {first_dt} → {last_dt} "
                    f"(~{len(bars_1m)} bars)",
                    flush=True,
                )

        elapsed = time.time() - t0
        history = getattr(bot, "trades", [])
        wins = sum(1 for t in history if t.get("pnl", 0) > 0)
        losses = sum(1 for t in history if t.get("pnl", 0) < 0)
        be = sum(1 for t in history if t.get("pnl", 0) == 0)
        total_pnl = sum(t.get("pnl", 0) for t in history)
        total_fee = sum(t.get("fee", 0) for t in history)
        trail_count = sum(t.get("trailing_count", 0) for t in history)
        return {
            "trades": len(history),
            "wins": wins,
            "losses": losses,
            "be": be,
            "total_pnl": round(total_pnl, 2),
            "total_fee": round(total_fee, 2),
            "trail_count": trail_count,
            "bars": total_bars,
            "elapsed": elapsed,
        }

    try:
        return asyncio.run(_loop())
    except Exception as e:
        print(f"  [{','.join(syms)}] HATA: {e}", flush=True)
        import traceback

        traceback.print_exc()
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "be": 0,
            "total_pnl": 0,
            "total_fee": 0,
            "trail_count": 0,
            "bars": 0,
            "elapsed": 0,
        }


def _print_table(results: dict[str, dict], elapsed: float, workers: int):
    grand_t = sum(r["trades"] for r in results.values())
    grand_pnl = sum(r["total_pnl"] for r in results.values())
    grand_fee = sum(r["total_fee"] for r in results.values())
    grand_w = sum(r["wins"] for r in results.values())
    grand_l = sum(r["losses"] for r in results.values())

    print(
        f"\n{'Symbol':<12} {'Trades':>7} {'Win':>6} {'Loss':>6} "
        f"{'TP%':>7} {'Trail':>6} {'Fee':>10} {'NetPnL':>12}"
    )
    print("-" * 76)
    for sym in sorted(results):
        r = results[sym]
        w = r["wins"]
        lo = r["losses"]
        t = r["trades"]
        tp_pct = w / t * 100 if t else 0
        trail = r.get("trail_count", 0)
        print(
            f"{sym:<12} {t:>7} {w:>6} {lo:>6} {tp_pct:>6.1f}% "
            f"{trail:>6} {r['total_fee']:>10.0f} {r['total_pnl']:>12.0f}"
        )

    print("-" * 76)
    grand_rate = grand_w / (grand_w + grand_l) * 100 if (grand_w + grand_l) else 0
    print(
        f"{'TOPLAM':<12} {grand_t:>7} {grand_w:>6} {grand_l:>6} "
        f"{grand_rate:>6.1f}% {'':>6} {grand_fee:>10.0f} {grand_pnl:>12.0f}"
    )
    print(f"\n  Sure: {elapsed:.1f}s | Workers: {workers} | Sembol: {len(results)}")


def run_simulation(symbols: list[str], days: int | None, workers: int = 1):
    print("=" * 60)
    print("  SIMULASYON — sniper bot canli kodu (multiprocess)")
    print(f"  Semboller: {len(symbols)} | Workers: {workers}")
    if days:
        print(f"  Gun araligi: son {days} gun")
    flag_str = (
        f"Exit={os.environ['EXIT_LIFECYCLE_SERVICE_ENABLED']} "
        f"Protection={os.environ['PROTECTION_LIFECYCLE_SERVICE_ENABLED']}"
    )
    print(f"  Flags: {flag_str}")
    print("=" * 60)

    valid = [
        s
        for s in symbols
        if os.path.isfile(os.path.join(DATA_DIR, f"{s}_1m_raw.feather"))
    ]

    results: dict[str, dict] = {}
    t0 = time.time()

    if workers <= 1:
        for sym in valid:
            r = _run_worker([sym], days)
            results[sym] = r
            w = r["wins"]
            lo = r["losses"]
            t = r["trades"]
            rate = w / (w + lo) * 100 if (w + lo) else 0
            print(
                f"  [{sym}] trades={t} win={w} loss={lo} "
                f"rate={rate:.0f}% bars={r['bars']} time={r['elapsed']:.1f}s",
                flush=True,
            )
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_worker, [s], days): s for s in valid}
            for fut in as_completed(futures):
                sym = futures[fut]
                r = fut.result()
                results[sym] = r
                w = r["wins"]
                lo = r["losses"]
                t = r["trades"]
                rate = w / (w + lo) * 100 if (w + lo) else 0
                print(
                    f"  [{sym}] trades={t} win={w} loss={lo} "
                    f"rate={rate:.0f}% bars={r['bars']} time={r['elapsed']:.1f}s",
                    flush=True,
                )

    elapsed = time.time() - t0
    if results:
        _print_table(results, elapsed, workers)
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
