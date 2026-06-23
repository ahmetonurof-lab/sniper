"""
All 13 coins: find every retrade trade, check bias alignment.
"""

import csv
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\Administrator\Desktop\nexus-mcp\backtest-sniper\src")
from coins_config import get_config, COINS
from fvg import detect_fvgs
from models import Bar
from retrace_state import RetraceStateMachine
from session import DailyBias, SessionPhase, SessionState, detect_phase_from_timestamp

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = r"C:\Users\Administrator\Desktop\nexus-mcp\backtest-sniper\src\data"


def load_data(filepath):
    bars = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            ts = int(
                datetime.strptime(row["open_time"], "%Y-%m-%d %H:%M:%S").timestamp()
                * 1000
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
    return bars


def resample_15m(bars_1m):
    m15 = []
    for i in range(0, len(bars_1m), 15):
        c = bars_1m[i : i + 15]
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
    return m15


def run(symbol):
    cfg = get_config(symbol)
    min_fvg_size = cfg["min_fvg_size"]
    initial_capital = cfg["initial_capital"]
    risk_per_trade = cfg["risk_per_trade"]
    sl_atr_mult = cfg["sl_atr_mult"]
    tp_rr = cfg["tp_rr"]
    fvg_buffer_mult = cfg["fvg_buffer_mult"]

    fp = os.path.join(DATA, f"{symbol}_1m.csv")
    if not os.path.isfile(fp):
        return []
    bars_1m = load_data(fp)
    bars_15m = resample_15m(bars_1m)

    ss = SessionState()
    rsm = RetraceStateMachine(min_fvg_size=min_fvg_size)
    rsm_retrade = RetraceStateMachine(min_fvg_size=min_fvg_size * 0.3)
    trades = []
    active_trades = []
    WINDOW = 500
    retrade_records = []

    for scan_bar in range(WINDOW, len(bars_15m), 5):
        chunk = bars_15m[scan_bar - WINDOW : scan_bar + 1]
        current = bars_15m[scan_bar]
        atr_val = max(current.range, current.close * 0.0001)
        try:
            entry_dt = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        except:
            continue

        ss.update(
            entry_dt, current.open, current.high, current.low, current.close, atr_val
        )

        if ss.sweep_confirmed and rsm.state_name == "IDLE":
            rsm.on_sweep(
                direction=ss.sweep_direction or "bullish",
                level=ss.sweep_level or 0.0,
                bar_index=current.index,
            )
        if rsm.state_name == "SWEEP_DETECTED":
            rsm.on_sweep_confirmed(chunk, current)
        if rsm.can_trigger():
            sweep_dir = rsm.direction
            daily_bias = ss.daily_bias
            if sweep_dir == "bullish" and daily_bias == DailyBias.BEARISH:
                rsm.reset()
                continue
            if sweep_dir == "bearish" and daily_bias == DailyBias.BULLISH:
                rsm.reset()
                continue
            if daily_bias == DailyBias.NEUTRAL:
                rsm.reset()
                continue
            phase = detect_phase_from_timestamp(current.timestamp)
            if phase not in (SessionPhase.NEWYORK, SessionPhase.LONDON):
                rsm.reset()
                continue
            side = "long" if sweep_dir == "bullish" else "short"
            entry_price = current.close
            risk_pts = atr_val * sl_atr_mult
            trigger_fvg = rsm.trigger_fvg
            if side == "long":
                sl = (
                    trigger_fvg.bottom - (risk_pts * fvg_buffer_mult)
                    if trigger_fvg
                    else entry_price - risk_pts * 2
                )
                tp = (
                    ss.london_high
                    if ss.london_high > entry_price
                    else entry_price + risk_pts * tp_rr
                )
            else:
                sl = (
                    trigger_fvg.top + (risk_pts * fvg_buffer_mult)
                    if trigger_fvg
                    else entry_price + risk_pts * 2
                )
                tp = (
                    ss.london_low
                    if ss.london_low < entry_price
                    else entry_price - risk_pts * tp_rr
                )
            qty = (
                (initial_capital * risk_per_trade) / abs(sl - entry_price)
                if abs(sl - entry_price) > 0
                else 0
            )
            if qty <= 0:
                rsm.reset()
                continue
            active_trades.append(
                {
                    "entry_bar": scan_bar,
                    "entry_price": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "qty": qty,
                    "side": side,
                    "trigger_fvg": trigger_fvg,
                    "initial_sl": sl,
                    "initial_tp": tp,
                    "trailing_count": 0,
                    "is_retrade": False,
                    "entry_dt": entry_dt,
                    "daily_bias": ss.daily_bias.name,
                    "bias_dir": daily_bias.name,
                    "london_h": ss.london_high,
                    "london_l": ss.london_low,
                    "cbdr_low": ss.cbdr_body_low,
                    "cbdr_high": ss.cbdr_body_high,
                }
            )
            ss.trades_today += 1
            rsm.reset()

        if active_trades and current.is_closed:
            current_fvgs = detect_fvgs(
                chunk,
                lookback=min(50, len(chunk)),
                timeframe="15m",
                min_fvg_size=min_fvg_size,
            )
            for trade in active_trades:
                if trade.get("closed"):
                    continue
                for fvg in current_fvgs:
                    if trade["side"] == "long" and fvg.direction != "bullish":
                        continue
                    if trade["side"] == "short" and fvg.direction != "bearish":
                        continue
                    if fvg.filled or fvg.invalidated:
                        continue
                    buffer = (
                        abs(trade["initial_sl"] - trade["entry_price"])
                        * fvg_buffer_mult
                    )
                    if trade["side"] == "long":
                        new_sl = fvg.bottom - buffer
                        if new_sl > trade["sl"]:
                            diff = new_sl - trade["sl"]
                            trade["sl"] = new_sl
                            trade["tp"] += diff
                            trade["trailing_count"] += 1
                    else:
                        new_sl = fvg.top + buffer
                        if new_sl < trade["sl"]:
                            diff = trade["sl"] - new_sl
                            trade["sl"] = new_sl
                            trade["tp"] -= diff
                            trade["trailing_count"] += 1

        still_active = []
        for trade in active_trades:
            if trade.get("closed"):
                continue
            exited = False
            if trade["side"] == "long":
                if current.low <= trade["sl"]:
                    trade["exit_price"] = trade["sl"]
                    trade["exit_bar"] = scan_bar
                    trade["result"] = "SL"
                    trade["closed"] = True
                    exited = True
                elif current.high >= trade["tp"]:
                    trade["exit_price"] = trade["tp"]
                    trade["exit_bar"] = scan_bar
                    trade["result"] = "TP"
                    trade["closed"] = True
                    exited = True
            else:
                if current.high >= trade["sl"]:
                    trade["exit_price"] = trade["sl"]
                    trade["exit_bar"] = scan_bar
                    trade["result"] = "SL"
                    trade["closed"] = True
                    exited = True
                elif current.low <= trade["tp"]:
                    trade["exit_price"] = trade["tp"]
                    trade["exit_bar"] = scan_bar
                    trade["result"] = "TP"
                    trade["closed"] = True
                    exited = True
            if exited:
                diff = (
                    trade["exit_price"] - trade["entry_price"]
                    if trade["side"] == "long"
                    else trade["entry_price"] - trade["exit_price"]
                )
                trade["pnl"] = round(diff * trade["qty"], 2)
                trade["rr"] = round(
                    diff / abs(trade["initial_sl"] - trade["entry_price"])
                    if abs(trade["initial_sl"] - trade["entry_price"]) > 0
                    else 0,
                    2,
                )
                trades.append(trade)
                if (
                    not trade.get("is_retrade", False)
                    and ss.trades_today == 1
                    and not ss.retrade_armed
                ):
                    ss.retrade_armed = True
                    ss.retrade_side = "short" if trade["side"] == "long" else "long"
                    ss.retrade_sweep_level = 0.0
                    ss.retrade_entry_bar = trade["entry_bar"]
            else:
                still_active.append(trade)
        active_trades = still_active

        # Retrade
        if ss.retrade_armed and ss.trades_today == 1 and not active_trades:
            sweep_bar_idx = None
            sweep_found = False
            for check_idx in range(max(0, scan_bar - 4), scan_bar + 1):
                if check_idx < 0 or check_idx >= len(bars_15m):
                    continue
                cb = bars_15m[check_idx]
                if check_idx - 5 < 0:
                    continue
                recent_bars = bars_15m[check_idx - 5 : check_idx]
                if ss.retrade_side == "short":
                    recent_high = max(b.high for b in recent_bars)
                    if cb.high > recent_high and cb.close < recent_high:
                        sweep_found = True
                        sweep_bar_idx = check_idx
                        break
                else:
                    recent_low = min(b.low for b in recent_bars)
                    if cb.low < recent_low and cb.close > recent_low:
                        sweep_found = True
                        sweep_bar_idx = check_idx
                        break
            if sweep_found:
                sweep_dir = "bearish" if ss.retrade_side == "short" else "bullish"
                if rsm_retrade.state_name == "IDLE":
                    rsm_retrade.on_sweep(
                        direction=sweep_dir,
                        level=0.0,
                        bar_index=bars_15m[sweep_bar_idx].index,
                    )
                if rsm_retrade.state_name == "SWEEP_DETECTED":
                    sweep_bar = bars_15m[sweep_bar_idx]
                    sweep_chunk = (
                        bars_15m[sweep_bar_idx - WINDOW : sweep_bar_idx + 1]
                        if sweep_bar_idx >= WINDOW
                        else chunk
                    )
                    rsm_retrade.on_sweep_confirmed(sweep_chunk, sweep_bar)
                if rsm_retrade.can_trigger():
                    if sweep_bar_idx is not None and sweep_bar_idx <= (
                        ss.retrade_entry_bar or 0
                    ):
                        rsm_retrade.reset()
                    elif detect_phase_from_timestamp(current.timestamp) not in (
                        SessionPhase.NEWYORK,
                        SessionPhase.LONDON,
                    ):
                        rsm_retrade.reset()
                    else:
                        retrade_entry_price = current.close
                        retrade_risk_pts = atr_val * sl_atr_mult
                        retrade_fvg = rsm_retrade.trigger_fvg
                        if ss.retrade_side == "long":
                            retrade_sl = (
                                retrade_fvg.bottom
                                - (retrade_risk_pts * fvg_buffer_mult)
                                if retrade_fvg
                                else retrade_entry_price - retrade_risk_pts * 2
                            )
                            retrade_tp = (
                                ss.london_high
                                if ss.london_high > retrade_entry_price
                                else retrade_entry_price + retrade_risk_pts * tp_rr
                            )
                        else:
                            retrade_sl = (
                                retrade_fvg.top + (retrade_risk_pts * fvg_buffer_mult)
                                if retrade_fvg
                                else retrade_entry_price + retrade_risk_pts * 2
                            )
                            retrade_tp = (
                                ss.london_low
                                if ss.london_low < retrade_entry_price
                                else retrade_entry_price - retrade_risk_pts * tp_rr
                            )
                        retrade_qty = (
                            (initial_capital * risk_per_trade)
                            / abs(retrade_sl - retrade_entry_price)
                            if abs(retrade_sl - retrade_entry_price) > 0
                            else 0
                        )
                        if retrade_qty > 0:
                            active_trades.append(
                                {
                                    "entry_bar": scan_bar,
                                    "entry_price": retrade_entry_price,
                                    "sl": retrade_sl,
                                    "tp": retrade_tp,
                                    "qty": retrade_qty,
                                    "side": ss.retrade_side,
                                    "trigger_fvg": retrade_fvg,
                                    "initial_sl": retrade_sl,
                                    "initial_tp": retrade_tp,
                                    "trailing_count": 0,
                                    "is_retrade": True,
                                    "entry_dt": entry_dt,
                                }
                            )
                            ss.trades_today += 1
                        rsm_retrade.reset()
                        ss.retrade_armed = False

    # Close open trades
    if bars_15m:
        last_price = bars_15m[-1].close
        for trade in active_trades:
            if not trade.get("closed"):
                trade["exit_price"] = last_price
                trade["exit_bar"] = len(bars_15m) - 1
                trade["result"] = "OPEN"
                trade["closed"] = True
                diff = (
                    last_price - trade["entry_price"]
                    if trade["side"] == "long"
                    else trade["entry_price"] - last_price
                )
                trade["pnl"] = round(diff * trade["qty"], 2)
                trades.append(trade)

    # Find retrade-primary pairs
    retrade_list = [t for t in trades if t.get("is_retrade")]
    primary_list = [t for t in trades if not t.get("is_retrade")]

    # For each retrade, find the preceding primary on same day
    for rt in retrade_list:
        rt_date = (
            datetime.fromtimestamp(
                rt["entry_dt"].timestamp(), tz=timezone.utc
            ).strftime("%Y-%m-%d")
            if hasattr(rt["entry_dt"], "timestamp")
            else str(rt["entry_dt"])[:10]
        )
        # Find the primary that triggered this retrade (same day, entry_bar before retrade)
        primary = None
        for pt in reversed(primary_list):
            pt_entry = pt.get("entry_dt", pt.get("entry_bar", 0))
            pt_date = (
                datetime.fromtimestamp(pt_entry.timestamp(), tz=timezone.utc).strftime(
                    "%Y-%m-%d"
                )
                if hasattr(pt_entry, "timestamp")
                else str(pt_entry)[:10]
            )
            if pt_date == rt_date and not pt.get("is_retrade"):
                primary = pt
                break

        # Determine bias direction for this day by finding the ss state at entry time
        # We stored bias_dir and london levels on the primary trade
        bias_name = "UNKNOWN"
        if primary and "bias_dir" in primary:
            bias_name = primary["bias_dir"]

        # Retrade direction string
        rt_side = rt["side"]

        # Bias direction
        bias_dir_long = bias_name in ("BULLISH",)
        rt_is_long = rt_side == "long"

        aligned = (bias_dir_long and rt_is_long) or (
            not bias_dir_long and not rt_is_long
        )
        if bias_name == "NEUTRAL":
            aligned = False  # NEUTRAL is rejected by primary but retrade bypasses

        primary_result = primary["result"] if primary else "?"
        primary_side = primary["side"] if primary else "?"

        retrade_records.append(
            {
                "symbol": symbol,
                "date": rt_date,
                "primary_side": primary_side,
                "primary_result": primary_result,
                "primary_pnl": primary.get("pnl", 0),
                "retrade_side": rt_side,
                "retrade_pnl": rt.get("pnl", 0),
                "retrade_result": rt.get("result", "?"),
                "bias": bias_name,
                "aligned": aligned,
            }
        )

    return retrade_records


def main():
    all_records = []
    for sym in sorted(COINS.keys()):
        recs = run(sym)
        all_records.extend(recs)

    print(f"\nToplam retrade sayisi: {len(all_records)}\n")

    aligned = [r for r in all_records if r["aligned"]]
    conflicting = [r for r in all_records if not r["aligned"]]

    aligned_pnl = sum(r["retrade_pnl"] for r in aligned)
    conflicting_pnl = sum(r["retrade_pnl"] for r in conflicting)

    print("=== BIAS ILE UYUMLU RETRADELER ===")
    print(f"Adet: {len(aligned)} | Toplam PnL: {aligned_pnl:+.2f}")
    if aligned:
        print(
            f"{'Symbol':<10} {'Date':<12} {'Prim':<8} {'PrimExit':<6} {'Retr':<8} {'PnL':<10} {'Bias':<10}"
        )
        print("-" * 70)
        for r in aligned:
            print(
                f"{r['symbol']:<10} {r['date']:<12} {r['primary_side']:<8} {r['primary_result']:<6} {r['retrade_side']:<8} {r['retrade_pnl']:<+10.2f} {r['bias']:<10}"
            )

    print()
    print("=== BIAS ILE CELISEN RETRADELER ===")
    print(f"Adet: {len(conflicting)} | Toplam PnL: {conflicting_pnl:+.2f}")
    if conflicting:
        print(
            f"{'Symbol':<10} {'Date':<12} {'Prim':<8} {'PrimExit':<6} {'Retr':<8} {'PnL':<10} {'Bias':<10}"
        )
        print("-" * 70)
        for r in conflicting:
            print(
                f"{r['symbol']:<10} {r['date']:<12} {r['primary_side']:<8} {r['primary_result']:<6} {r['retrade_side']:<8} {r['retrade_pnl']:<+10.2f} {r['bias']:<10}"
            )

    print()
    print("=== PRIMARY EXIT TURUNE GORE RETRADE BASARISI ===")
    sl_prim = [r for r in all_records if r["primary_result"] == "SL"]
    tp_prim = [r for r in all_records if r["primary_result"] == "TP"]
    open_prim = [r for r in all_records if r["primary_result"] == "OPEN"]

    if sl_prim:
        sl_pnl = sum(r["retrade_pnl"] for r in sl_prim)
        sl_win = sum(1 for r in sl_prim if r["retrade_pnl"] > 0)
        print(
            f"Primary SL  ile kapandıktan sonra retrade: {len(sl_prim)} trade | PnL={sl_pnl:+.2f} | WR={sl_win/len(sl_prim)*100:.1f}%"
        )
    if tp_prim:
        tp_pnl = sum(r["retrade_pnl"] for r in tp_prim)
        tp_win = sum(1 for r in tp_prim if r["retrade_pnl"] > 0)
        print(
            f"Primary TP  ile kapandıktan sonra retrade: {len(tp_prim)} trade | PnL={tp_pnl:+.2f} | WR={tp_win/len(tp_prim)*100:.1f}%"
        )
    if open_prim:
        open_pnl = sum(r["retrade_pnl"] for r in open_prim)
        open_win = sum(1 for r in open_prim if r["retrade_pnl"] > 0)
        print(
            f"Primary OPEN ile kapandıktan sonra retrade: {len(open_prim)} trade | PnL={open_pnl:+.2f} | WR={open_win/len(open_prim)*100:.1f}%"
        )

    print()
    print("=== PRIMARY EXIT vs RETRADE YONU ILISKISI ===")
    # retrade always goes opposite to primary side regardless of how primary closed
    for r_type, label in [
        ("SL", "SL ile kapanan"),
        ("TP", "TP ile kapanan"),
        ("OPEN", "OPEN kalan"),
    ]:
        subset = [r for r in all_records if r["primary_result"] == r_type]
        if not subset:
            continue
        for r in subset[:3]:
            print(
                f"  [{label}] prim={r['primary_side']} -> retr={r['retrade_side']} (bias={r['bias']}, PnL={r['retrade_pnl']:+.2f})"
            )
        if len(subset) > 3:
            print(f"  ... ve {len(subset)-3} daha")


if __name__ == "__main__":
    main()
