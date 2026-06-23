"""
NEARUSDT backtest günlük detay analizi.
Her trade için: tarih, yön, entry/exit bar, PnL, SL/TP, retrade durumu.
"""

import csv
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\Administrator\Desktop\nexus-mcp\backtest-sniper\src")
from coins_config import get_config
from fvg import detect_fvgs
from models import Bar
from retrace_state import RetraceStateMachine
from session import DailyBias, SessionPhase, SessionState, detect_phase_from_timestamp

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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


symbol = "NEARUSDT"
cfg = get_config(symbol)
min_fvg_size = cfg["min_fvg_size"]
initial_capital = cfg["initial_capital"]
risk_per_trade = cfg["risk_per_trade"]
sl_atr_mult = cfg["sl_atr_mult"]
tp_rr = cfg["tp_rr"]
fvg_buffer_mult = cfg["fvg_buffer_mult"]

csv_file = os.path.join(
    r"C:\Users\Administrator\Desktop\nexus-mcp\backtest-sniper\src\data",
    f"{symbol}_1m.csv",
)
bars_1m = load_data(csv_file)
bars_15m = resample_15m(bars_1m)
print(f"{symbol} | 1m: {len(bars_1m)} bars | 15m: {len(bars_15m)} bars")

ss = SessionState()
rsm = RetraceStateMachine(min_fvg_size=min_fvg_size)
rsm_retrade = RetraceStateMachine(min_fvg_size=min_fvg_size * 0.3)
trades = []
active_trades = []
WINDOW = 500

daily_log = {}  # date -> {trades: [], bias:, cbdr:, london_h:, london_l:}

for scan_bar in range(WINDOW, len(bars_15m), 5):
    chunk = bars_15m[scan_bar - WINDOW : scan_bar + 1]
    current = bars_15m[scan_bar]
    atr_val = max(current.range, current.close * 0.0001)
    try:
        entry_dt = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
    except:
        continue
    today = entry_dt.strftime("%Y-%m-%d")

    ss.update(entry_dt, current.open, current.high, current.low, current.close, atr_val)

    # Log daily CBDR/bias context
    if today not in daily_log:
        daily_log[today] = {
            "trades": [],
            "retrade": [],
            "cbdr": [ss.cbdr_body_low, ss.cbdr_body_high],
            "bias": ss.daily_bias.name,
            "london_h": ss.london_high,
            "london_l": ss.london_low,
        }
    else:
        dl = daily_log[today]
        if ss.cbdr_locked and dl["cbdr"] == [float("inf"), 0.0]:
            dl["cbdr"] = [ss.cbdr_body_low, ss.cbdr_body_high]
        if dl["bias"] == "NEUTRAL" and ss.daily_bias != DailyBias.NEUTRAL:
            dl["bias"] = ss.daily_bias.name
        if ss.london_high > dl["london_h"]:
            dl["london_h"] = ss.london_high
        if ss.london_low < dl["london_l"]:
            dl["london_l"] = ss.london_low

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
            }
        )
        ss.trades_today += 1
        rsm.reset()

    # Trailing
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
                    abs(trade["initial_sl"] - trade["entry_price"]) * fvg_buffer_mult
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

    # Exit
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
            trade["duration"] = trade["exit_bar"] - trade["entry_bar"]
            trades.append(trade)
            if today in daily_log:
                daily_log[today]["trades"].append(trade)
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
                            retrade_fvg.bottom - (retrade_risk_pts * fvg_buffer_mult)
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
                        rt_trade = {
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
                        active_trades.append(rt_trade)
                        ss.trades_today += 1
                        if today in daily_log:
                            daily_log[today]["retrade"].append(rt_trade)
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
            trade["duration"] = trade["exit_bar"] - trade["entry_bar"]
            trades.append(trade)

# Print report: days with retrade
print("\n=== GUNLERDE RETRADE OLANLAR ===\n")
for date, dl in sorted(daily_log.items()):
    if not dl["retrade"]:
        continue
    for rt in dl["retrade"]:
        primary = dl["trades"][0] if dl["trades"] else None
        cbdr_low, cbdr_high = dl["cbdr"]
        print(f"DATE: {date}")
        print(
            f"  CBDR: [{cbdr_low:.4f}-{cbdr_high:.4f}] | BIAS: {dl['bias']} | London H/L: {dl['london_h']:.4f}/{dl['london_l']:.4f}"
        )
        if primary:
            print(
                f"  PRIMARY: {primary['side'].upper()} | entry={primary['entry_price']:.4f} (bar#{primary['entry_bar']}) | exit={primary.get('exit_price',0):.4f} (bar#{primary.get('exit_bar','?')}) | PnL={primary.get('pnl',0):+.2f} | result={primary.get('result','?')}"
            )
        print(
            f"  RETRADE: {rt['side'].upper()} | entry={rt['entry_price']:.4f} (bar#{rt['entry_bar']}) | sl={rt['sl']:.4f} | tp={rt['tp']:.4f} | PnL={rt.get('pnl','?'):.2f} | result={rt.get('result','?')}"
        )
        print(
            f"  SORUN: Primary {primary['side'].upper()} -> Retrade {rt['side'].upper()} | Bias={dl['bias']}"
        )
        bias_direction = (
            "LONG"
            if dl["bias"] == "BULLISH"
            else "SHORT"
            if dl["bias"] == "BEARISH"
            else "NEUTRAL"
        )
        if bias_direction != "NEUTRAL" and rt["side"].upper() != bias_direction:
            print("  !!! RETRADE YONU BIAS ILE CELISIYOR !!!")
            print(f"  !!! retrade={rt['side'].upper()} vs bias={bias_direction}")
        print(
            f"  !!! SEBEP: retrade_side = {rt['side'].upper()} (primary'in tersi), bias filtresi retrade'de yok"
        )
        print()

# Also print summary of all trades
print("\n=== TUM PRIMARY TRADES (retrade olanlar isaretli) ===\n")
dates_with_retrade = {d for d, dl in daily_log.items() if dl["retrade"]}
for date, dl in sorted(daily_log.items()):
    for t in dl["trades"]:
        marker = " <<< RETRADE" if date in dates_with_retrade else ""
        pnl = t.get("pnl", 0)
        dur = t.get("duration", "?")
        print(
            f"{date} | {t['side']:>5} | entry={t['entry_price']:.4f} | exit={t.get('exit_price',0):.4f} | PnL={pnl:+.2f} | {t.get('result','?'):4} | dur={dur}{marker}"
        )
