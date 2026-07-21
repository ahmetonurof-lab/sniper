"""
simulate.py — Fast paper-trade simulation (V5 engine, no PaperTrader).
15m loop, direct strategy, trailing + SL/TP exit. Multiprocess.
"""

from __future__ import annotations

import argparse
import math
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["BINANCE_API_KEY"] = ""
os.environ["BINANCE_API_SECRET"] = ""

import config as _cfg  # noqa: E402

_cfg.BINANCE_API_KEY = ""
_cfg.BINANCE_API_SECRET = ""

import logging  # noqa: E402

logging.getLogger().setLevel(logging.ERROR)
logging.getLogger("sniper").setLevel(logging.ERROR)

import pandas as pd  # noqa: E402

pd.set_option("future.no_silent_downcasting", True)

from models import Bar  # noqa: E402
from fvg import detect_fvgs  # noqa: E402
from indicators import calculate_true_range, update_atr  # noqa: E402
from retrace_state import RetraceStateMachine  # noqa: E402
from session import SessionState  # noqa: E402
from session_router import (  # noqa: E402
    get_cbdr_multiplier,
    get_session_hours,
    should_trade,
)
from trading.signal_engine import SignalEngine  # noqa: E402

COMMISSION_RATE = 0.0005

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


def load_bars(symbol: str, max_1m: int | None = None) -> tuple[list[Bar], list[Bar]]:
    path = os.path.join(DATA_DIR, f"{symbol}_1m_raw.feather")
    if not os.path.isfile(path):
        return [], []
    df = pd.read_feather(path)
    ts_ms = (
        pd.to_datetime(df["open_time"], format="%Y-%m-%d %H:%M:%S")
        .values.astype("datetime64[ms]")
        .astype("int64")
    )
    n = min(len(df), max_1m or len(df))
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    bars_1m = [
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
        for i in range(n)
    ]

    _15M_MS = 15 * 60 * 1000
    buckets: dict[int, list[Bar]] = {}
    for b in bars_1m:
        slot = (b.timestamp // _15M_MS) * _15M_MS
        buckets.setdefault(slot, []).append(b)

    m15 = []
    for slot in sorted(buckets):
        chunk = buckets[slot]
        if len(chunk) < 15:
            continue
        m15.append(
            Bar(
                index=len(m15),
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low for b in chunk),
                close=chunk[-1].close,
                volume=sum(b.volume for b in chunk),
                is_closed=True,
                timestamp=slot,
            )
        )
    return bars_1m, m15


def fvg_close_confirmed(fvg, all_bars):
    scan_from = fvg.real_index + 2
    for b in all_bars:
        if b.index < scan_from:
            continue
        if fvg.direction == "bullish":
            if b.close < fvg.bottom:
                return False
            if fvg.bottom <= b.close <= fvg.top:
                return True
        else:
            if b.close > fvg.top:
                return False
            if fvg.bottom <= b.close <= fvg.top:
                return True
    return False


def _run_worker(syms: list[str], days: int | None) -> list[dict]:
    results: list[dict] = []
    for sym in syms:
        max_1m = days * 24 * 60 if days else None
        bars_1m, b15 = load_bars(sym, max_1m)
        if not b15 or len(b15) < 10:
            results.append(
                {
                    "symbol": sym,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_pnl": 0,
                    "total_fee": 0,
                    "trail_count": 0,
                    "bars_15m": 0,
                    "elapsed": 0,
                }
            )
            continue

        t0 = time.time()
        sh_info = get_session_hours(sym)
        sh = sh_info["start"]
        eh = sh_info["end"]
        ss = SessionState(start_hour=sh, end_hour=eh)
        rsm = RetraceStateMachine(max_wick_ratio=_cfg.FVG_WICK_RATIO_MAX)
        engine = SignalEngine(rsm)

        atr_val = 0.0
        prev_close = b15[0].open
        for bar in b15[1:501]:
            tr = calculate_true_range(bar, prev_close)
            if atr_val == 0.0:
                atr_val = tr
            else:
                atr_val = update_atr(atr_val, tr)
            prev_close = bar.close

        total_bars = len(b15)
        active: list[dict] = []
        trade_records: list[dict] = []
        wins = 0
        losses = 0
        trail_count = 0

        for sb in range(500, total_bars):
            chunk = b15[sb - 500 : sb + 1]
            cur = b15[sb]
            tr = calculate_true_range(cur, prev_close)
            atr_val = update_atr(atr_val if atr_val > 0 else None, tr)
            prev_close = cur.close
            atr = atr_val

            try:
                edt = datetime.fromtimestamp(cur.timestamp / 1000, tz=timezone.utc)
            except Exception:
                continue

            # ── Session update ──
            ss.update(edt, cur.open, cur.high, cur.low, cur.close, atr)

            # ── RSM progression (IDLE → SWEEP_DETECTED → TRIGGER_READY) ──
            engine.progress_rsm(chunk, cur, ss, atr, sym)

            # ── Trigger check ──
            eval_result = engine.evaluate_trigger(cur, ss)

            if eval_result.decision == "TRIGGER" and not active:
                sd = eval_result.direction
                tf = eval_result.trigger_fvg

                # next-bar-open entry (no look-ahead)
                if sb + 1 >= total_bars:
                    rsm.reset()
                    continue
                next_bar = b15[sb + 1]
                side = "long" if sd == "bullish" else "short"
                ep = next_bar.open
                risk_pts = atr * _cfg.SL_ATR_MULT

                if side == "long":
                    if tf:
                        fh = tf.top - tf.bottom
                        if fh <= 0:
                            sl = ep - risk_pts * 2
                        else:
                            ab = max(
                                fh * _cfg.FVG_BUFFER_MIN_FACTOR,
                                max(
                                    risk_pts * 0.1,
                                    min(fh * 0.25, risk_pts * _cfg.FVG_BUFFER_MULT),
                                ),
                            )
                            sl = tf.bottom - ab
                    else:
                        sl = ep - risk_pts * 2
                    rd = abs(sl - ep)
                    if rd <= 0:
                        sl = ep - risk_pts * 2
                        rd = abs(sl - ep)
                    tp = ep + rd * _cfg.TP_RR
                else:
                    if tf:
                        fh = tf.top - tf.bottom
                        if fh <= 0:
                            sl = ep + risk_pts * 2
                        else:
                            ab = max(
                                fh * _cfg.FVG_BUFFER_MIN_FACTOR,
                                max(
                                    risk_pts * 0.1,
                                    min(fh * 0.25, risk_pts * _cfg.FVG_BUFFER_MULT),
                                ),
                            )
                            sl = tf.top + ab
                    else:
                        sl = ep + risk_pts * 2
                    rd = abs(sl - ep)
                    if rd <= 0:
                        sl = ep + risk_pts * 2
                        rd = abs(sl - ep)
                    tp = ep - rd * _cfg.TP_RR

                # ── Quality: min risk distance ──
                quality_mult = 1.0
                if rd < atr * _cfg.MIN_RISK_DIST_ATR_MULT:
                    quality_mult = 0.0

                # ── CBDR + Session Router ──
                cbdr_w = None
                if ss.cbdr_body_low > 0 and not math.isinf(ss.cbdr_body_low):
                    cbdr_w = (
                        (ss.cbdr_body_high - ss.cbdr_body_low) / ss.cbdr_body_low * 100
                    )
                cbdr_mult = (
                    get_cbdr_multiplier(sym, cbdr_w) if cbdr_w is not None else 1.0
                )
                if cbdr_mult == 0.0:
                    quality_mult = 0.0

                allowed, reason = should_trade(sym, cbdr_width_pct=cbdr_w)
                if not allowed:
                    quality_mult = 0.0

                h = edt.hour
                el_mult = _cfg.EARLY_LONDON_RISK_MULT if 2 <= h < 8 else 1.0
                final_mult = el_mult * cbdr_mult * quality_mult

                qty = (
                    (_cfg.INITIAL_BALANCE * _cfg.RISK_PER_TRADE * final_mult) / rd
                    if rd > 0
                    else 0
                )

                if qty > 0:
                    active.append(
                        {
                            "entry_bar": sb + 1,
                            "entry_price": ep,
                            "sl": sl,
                            "tp": tp,
                            "qty": qty,
                            "side": side,
                            "trigger_fvg": tf,
                            "initial_sl": sl,
                            "initial_tp": tp,
                            "trailing_count": 0,
                        }
                    )
                    rsm.reset()
                    continue

                rsm.reset()
                continue

            # ── Trailing (every 15m bar) ──
            if active and cur.is_closed:
                tc = chunk[:-1]
                min_mult = _cfg.FVG_SIZE_MAP.get(sym, _cfg.FVG_MIN_SIZE_ATR_MULT)
                min_fvg_size = max(atr * min_mult, 1e-8)
                cfvgs = detect_fvgs(
                    tc,
                    lookback=min(50, len(tc)),
                    timeframe="15m",
                    min_fvg_size=min_fvg_size,
                )
                for t in active:
                    if t.get("closed"):
                        continue
                    s2 = t["side"]
                    csl = t["sl"]
                    ctp = t["tp"]
                    rpt2 = abs(t["initial_sl"] - t["entry_price"])
                    upd = False
                    for fvg in cfvgs:
                        if s2 == "long" and fvg.direction != "bullish":
                            continue
                        if s2 == "short" and fvg.direction != "bearish":
                            continue
                        if not fvg_close_confirmed(fvg, tc):
                            continue
                        ab2 = atr * _cfg.ATR_TRAIL_MULT
                        if s2 == "long":
                            ns = fvg.bottom - ab2
                            if (
                                ns > csl
                                and (ns - csl) > rpt2 * _cfg.TRAIL_MIN_MOVE_MULT
                            ):
                                sd2 = ns - csl
                                csl = ns
                                ctp += sd2
                                upd = True
                        else:
                            ns = fvg.top + ab2
                            if (
                                ns < csl
                                and (csl - ns) > rpt2 * _cfg.TRAIL_MIN_MOVE_MULT
                            ):
                                sd2 = csl - ns
                                csl = ns
                                ctp -= sd2
                                upd = True
                    if upd:
                        t["sl"] = csl
                        t["tp"] = ctp
                        t["trailing_count"] = t.get("trailing_count", 0) + 1

            # ── Exit check ──
            remaining = []
            for t in active:
                if t.get("closed"):
                    continue
                ex = False
                if t["side"] == "long":
                    if cur.low <= t["sl"]:
                        t["exit_price"] = t["sl"]
                        t["exit_bar"] = sb
                        if (
                            t.get("trailing_count", 0) > 0
                            and t["sl"] > t["entry_price"]
                        ):
                            t["result"] = "PROFIT_TRAIL"
                        else:
                            t["result"] = "LOSS"
                        t["closed"] = True
                        ex = True
                    elif cur.high >= t["tp"]:
                        t["exit_price"] = t["tp"]
                        t["exit_bar"] = sb
                        t["result"] = "TP"
                        t["closed"] = True
                        ex = True
                else:
                    if cur.high >= t["sl"]:
                        t["exit_price"] = t["sl"]
                        t["exit_bar"] = sb
                        if (
                            t.get("trailing_count", 0) > 0
                            and t["sl"] < t["entry_price"]
                        ):
                            t["result"] = "PROFIT_TRAIL"
                        else:
                            t["result"] = "LOSS"
                        t["closed"] = True
                        ex = True
                    elif cur.low <= t["tp"]:
                        t["exit_price"] = t["tp"]
                        t["exit_bar"] = sb
                        t["result"] = "TP"
                        t["closed"] = True
                        ex = True

                if ex:
                    diff = (
                        (t["exit_price"] - t["entry_price"])
                        if t["side"] == "long"
                        else (t["entry_price"] - t["exit_price"])
                    )
                    entry_fee = t["entry_price"] * t["qty"] * COMMISSION_RATE
                    exit_fee = t["exit_price"] * t["qty"] * COMMISSION_RATE
                    total_fee = entry_fee + exit_fee
                    pnl = round(diff * t["qty"] - total_fee, 2)
                    trade_records.append(
                        {
                            "symbol": sym,
                            "entry_price": t["entry_price"],
                            "exit_price": t["exit_price"],
                            "side": t["side"],
                            "qty": t["qty"],
                            "pnl": pnl,
                            "fee": round(total_fee, 2),
                            "result": t["result"],
                            "trailing_count": t.get("trailing_count", 0),
                        }
                    )
                    trail_count += t.get("trailing_count", 0)
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                else:
                    remaining.append(t)
            active = remaining

        # Open trades at end
        if active and b15:
            lp = b15[-1].close
            for t in active:
                if t.get("closed"):
                    continue
                t["exit_price"] = lp
                t["exit_bar"] = total_bars - 1
                t["result"] = "OPEN"
                diff = (
                    (lp - t["entry_price"])
                    if t["side"] == "long"
                    else (t["entry_price"] - lp)
                )
                entry_fee = t["entry_price"] * t["qty"] * COMMISSION_RATE
                exit_fee = lp * t["qty"] * COMMISSION_RATE
                total_fee = entry_fee + exit_fee
                pnl = round(diff * t["qty"] - total_fee, 2)
                trade_records.append(
                    {
                        "symbol": sym,
                        "entry_price": t["entry_price"],
                        "exit_price": t["exit_price"],
                        "side": t["side"],
                        "qty": t["qty"],
                        "pnl": pnl,
                        "fee": round(total_fee, 2),
                        "result": "OPEN",
                        "trailing_count": t.get("trailing_count", 0),
                    }
                )
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1

        elapsed = time.time() - t0
        total_pnl = sum(r["pnl"] for r in trade_records)
        total_fee = sum(r.get("fee", 0) for r in trade_records)

        print(
            f"  [{sym}] trades={len(trade_records)} win={wins} loss={losses} "
            f"rate={wins / max(wins + losses, 1) * 100:.0f}% "
            f"fee={total_fee:.0f} pnl={total_pnl:.0f} "
            f"bars={total_bars} time={elapsed:.1f}s",
            flush=True,
        )

        results.append(
            {
                "symbol": sym,
                "trades": len(trade_records),
                "wins": wins,
                "losses": losses,
                "total_pnl": total_pnl,
                "total_fee": total_fee,
                "trail_count": trail_count,
                "bars_15m": total_bars,
                "elapsed": elapsed,
            }
        )
    return results


def _print_table(results: list[dict], elapsed: float, workers: int):
    grand_t = sum(r["trades"] for r in results)
    grand_pnl = sum(r["total_pnl"] for r in results)
    grand_fee = sum(r["total_fee"] for r in results)
    grand_w = sum(r["wins"] for r in results)
    grand_l = sum(r["losses"] for r in results)

    print(
        f"\n{'Symbol':<12} {'Trades':>7} {'Win':>6} {'Loss':>6} "
        f"{'TP%':>7} {'Trail':>6} {'Fee':>10} {'NetPnL':>12}"
    )
    print("-" * 76)
    for r in results:
        w = r["wins"]
        lo = r["losses"]
        t = r["trades"]
        tp_pct = w / t * 100 if t else 0
        trail = r.get("trail_count", 0)
        print(
            f"{r['symbol']:<12} {t:>7} {w:>6} {lo:>6} {tp_pct:>6.1f}% "
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
    print("  SIMULASYON — V5 engine (direct strategy, no PaperTrader)")
    print(f"  Semboller: {len(symbols)} | Workers: {workers}")
    if days:
        print(f"  Gun araligi: son {days} gun")
    print("=" * 60)

    valid = [
        s
        for s in symbols
        if os.path.isfile(os.path.join(DATA_DIR, f"{s}_1m_raw.feather"))
    ]

    all_results: list[dict] = []
    t0 = time.time()

    if workers <= 1:
        for sym in valid:
            r_list = _run_worker([sym], days)
            if r_list:
                all_results.append(r_list[0])
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_worker, [s], days): s for s in valid}
            for fut in as_completed(futures):
                r_list = fut.result()
                if r_list:
                    all_results.append(r_list[0])

    elapsed = time.time() - t0
    if all_results:
        _print_table(all_results, elapsed, workers)
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
