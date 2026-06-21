"""
analyzer_v3.py — HTF FVG Wick Rejection + Cift Yonlu Trailing SL/TP + Short Destek.
ADX filtresi yok. OB tarasi yok. Sadece FVG.
Long ve Short islem destegi.
Yeni FVG olusunca SL ve TP birlikte tasinin (Trailing).
"""

import csv
import os
import sys
from datetime import datetime, timezone
from models import Bar
from session import SessionState, detect_phase_from_timestamp, SessionPhase, DailyBias
from retrace_state import RetraceStateMachine, scan_htf_fvgs, HTFFVG
from fvg import detect_fvgs

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SYMBOL = "BTCUSDT"
MIN_FVG_SIZE = 10.0
CSV_FILE = os.path.join(os.path.dirname(__file__), "data", f"{SYMBOL}_1m.csv")
INITIAL_CAPITAL = 10000.0
RISK_PER_TRADE = 0.01
SL_ATR_MULT = 1.5
TP_RR = 2.0
FVG_BUFFER_MULT = 0.25


def load_data(filepath):
    bars = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            ts = int(datetime.strptime(row["open_time"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
            bars.append(Bar(
                index=i, open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]), is_closed=True, timestamp=ts,
            ))
    return bars


def resample_15m(bars_1m):
    m15 = []
    for i in range(0, len(bars_1m), 15):
        c = bars_1m[i:i + 15]
        if len(c) < 15:
            break
        m15.append(Bar(
            index=c[0].index, open=c[0].open,
            high=max(b.high for b in c), low=min(b.low for b in c),
            close=c[-1].close, volume=sum(b.volume for b in c),
            is_closed=True, timestamp=c[0].timestamp,
        ))
    return m15


def get_liquidity_level(bars, center_idx, side, left=2, right=2):
    """2 sol + entry + 2 sag bar icinden pivot level bul.
    Long entry -> en yuksek high (LBS seviyesi)
    Short entry -> en dusuk low (SBS seviyesi)"""
    if center_idx - left < 0 or center_idx + right >= len(bars):
        return None
    window = bars[center_idx - left:center_idx + right + 1]
    if side == "long":
        return max(b.high for b in window)
    else:
        return min(b.low for b in window)


def run():
    print("Loading data...")
    bars_1m = load_data(CSV_FILE)
    bars_15m = resample_15m(bars_1m)
    print(f"  1m: {len(bars_1m)} bars | 15m: {len(bars_15m)} bars\n")

    ss = SessionState()
    rsm = RetraceStateMachine(min_fvg_size=MIN_FVG_SIZE)
    rsm_retrade = RetraceStateMachine(min_fvg_size=MIN_FVG_SIZE * 0.3)
    trades = []
    active_trades = []
    WINDOW = 500

    pipeline = {
        "cbdr_locked": 0,
        "sweep_detected": 0,
        "sweep_fed": 0,
        "fvg_scanned": 0,
        "wick_rejection": 0,
        "trigger_ready": 0,
        "filter_bias": 0,
        "filter_session": 0,
        "new_entry": 0,
        "trailing_sl_updates": 0,
        "trailing_tp_updates": 0,
        "closed": 0,
        "retrade_armed": 0,
        "retrade_sweep": 0,
        "retrade_sweep_fed": 0,
        "retrade_fvg_scanned": 0,
        "retrade_wick_rejection": 0,
        "retrade_trigger_ready": 0,
        "retrade_entry": 0,
    }
    total_signals = 0
    rejected_other = 0

    for scan_bar in range(WINDOW, len(bars_15m), 5):
        chunk = bars_15m[scan_bar - WINDOW:scan_bar + 1]
        current = bars_15m[scan_bar]
        atr_val = max(current.range, current.close * 0.0001)

        try:
            entry_dt = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc)
        except Exception:
            continue

        # 1 — CBDR tracking
        ss.update(entry_dt, current.open, current.high, current.low, current.close, atr_val)
        if ss.cbdr_locked:
            pipeline["cbdr_locked"] += 1

        # 2 — Sweep tespit
        if ss.sweep_confirmed:
            pipeline["sweep_detected"] += 1

        # 3 — Sweep IDLE ise RSM'e besle
        if ss.sweep_confirmed and rsm.state_name == "IDLE":
            pipeline["sweep_fed"] += 1
            rsm.on_sweep(
                direction=ss.sweep_direction or "bullish",
                level=ss.sweep_level or 0.0,
                bar_index=current.index,
            )

        # 4 — SWEEP_DETECTED ise aninda FVG taramasi + wick rejection
        if rsm.state_name == "SWEEP_DETECTED":
            pipeline["fvg_scanned"] += 1
            rsm.on_sweep_confirmed(chunk, current)
            if rsm.state_name == "TRIGGER_READY":
                pipeline["wick_rejection"] += 1

        # 5 — TRIGGER_READY ise yeni islem ac (LONG veya SHORT)
        if rsm.can_trigger():
            pipeline["trigger_ready"] += 1
            total_signals += 1

            sweep_dir = rsm.direction
            daily_bias = ss.daily_bias

            if sweep_dir == "bullish" and daily_bias == DailyBias.BEARISH:
                rsm.reset()
                rejected_other += 1
                continue
            if sweep_dir == "bearish" and daily_bias == DailyBias.BULLISH:
                rsm.reset()
                rejected_other += 1
                continue
            if daily_bias == DailyBias.NEUTRAL:
                rsm.reset()
                rejected_other += 1
                continue
            pipeline["filter_bias"] += 1

            phase = detect_phase_from_timestamp(current.timestamp)
            if phase != SessionPhase.NEWYORK:
                pipeline["filter_session"] += 1
                rsm.reset()
                rejected_other += 1
                continue

            side = "long" if sweep_dir == "bullish" else "short"
            entry_price = current.close
            risk_pts = atr_val * SL_ATR_MULT
            trigger_fvg = rsm.trigger_fvg

            if side == "long":
                if trigger_fvg:
                    sl = trigger_fvg.bottom - (risk_pts * FVG_BUFFER_MULT)
                else:
                    sl = entry_price - risk_pts * 2
                tp = ss.london_high if ss.london_high > entry_price else entry_price + risk_pts * TP_RR
            else:
                if trigger_fvg:
                    sl = trigger_fvg.top + (risk_pts * FVG_BUFFER_MULT)
                else:
                    sl = entry_price + risk_pts * 2
                tp = ss.london_low if ss.london_low < entry_price else entry_price - risk_pts * TP_RR

            qty = (INITIAL_CAPITAL * RISK_PER_TRADE) / abs(sl - entry_price) if abs(sl - entry_price) > 0 else 0
            if qty <= 0:
                rsm.reset()
                rejected_other += 1
                continue

            new_trade = {
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
            }
            active_trades.append(new_trade)
            pipeline["new_entry"] += 1
            ss.trades_today += 1
            rsm.reset()

        # 6 — Aktif islemler icin trailing SL/TP guncelleme
        if active_trades and current.is_closed:
            current_fvgs = detect_fvgs(chunk, lookback=min(50, len(chunk)), timeframe="15m", min_fvg_size=MIN_FVG_SIZE)

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

                    buffer = abs(trade["initial_sl"] - trade["entry_price"]) * FVG_BUFFER_MULT

                    if trade["side"] == "long":
                        new_sl = fvg.bottom - buffer
                        if new_sl > trade["sl"]:
                            sl_diff = new_sl - trade["sl"]
                            trade["sl"] = new_sl
                            trade["tp"] = trade["tp"] + sl_diff
                            trade["trailing_count"] += 1
                            pipeline["trailing_sl_updates"] += 1
                            pipeline["trailing_tp_updates"] += 1
                    else:
                        new_sl = fvg.top + buffer
                        if new_sl < trade["sl"]:
                            sl_diff = trade["sl"] - new_sl
                            trade["sl"] = new_sl
                            trade["tp"] = trade["tp"] - sl_diff
                            trade["trailing_count"] += 1
                            pipeline["trailing_sl_updates"] += 1
                            pipeline["trailing_tp_updates"] += 1

        # 7 — Aktif islemler icin exit kontrolu
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
                if trade["side"] == "long":
                    diff = trade["exit_price"] - trade["entry_price"]
                else:
                    diff = trade["entry_price"] - trade["exit_price"]
                trade["pnl"] = round(diff * trade["qty"], 2)
                risk = abs(trade["initial_sl"] - trade["entry_price"])
                trade["rr"] = round(diff / risk if risk > 0 else 0, 2)
                trades.append(trade)
                pipeline["closed"] += 1

                # 1. entry kapandi -> retrade icin arm et (trailing sweep)
                if not trade.get("is_retrade", False) and ss.trades_today == 1 and not ss.retrade_armed:
                    ss.retrade_armed = True
                    ss.retrade_side = "short" if trade["side"] == "long" else "long"
                    ss.retrade_sweep_level = 0.0  # kullanilmayacak
                    ss.retrade_entry_bar = trade["entry_bar"]
                    pipeline["retrade_armed"] += 1
            else:
                still_active.append(trade)

        active_trades = still_active

        # 8 — Retrade: trailing sweep kontrol + FVG + 2. entry
        # Son 15 bar'in highest high/lowest low'una gore sweep tara.
        # Loop her 5.15m bar'da calisir, aradaki 4 bari da tara.
        if ss.retrade_armed and ss.trades_today == 1 and not active_trades:
            sweep_bar_idx = None
            sweep_found = False
            lookback = min(5, scan_bar)
            for check_idx in range(scan_bar - 4, scan_bar + 1):
                if check_idx < 0 or check_idx >= len(bars_15m):
                    continue
                cb = bars_15m[check_idx]
                if check_idx - lookback < 0:
                    continue
                recent_bars = bars_15m[check_idx - lookback:check_idx]

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
                pipeline["retrade_sweep"] += 1
                sweep_dir = "bearish" if ss.retrade_side == "short" else "bullish"
                sweep_bar = bars_15m[sweep_bar_idx]
                if rsm_retrade.state_name == "IDLE":
                    rsm_retrade.on_sweep(
                        direction=sweep_dir,
                        level=ss.retrade_sweep_level,
                        bar_index=sweep_bar.index,
                    )
                    pipeline["retrade_sweep_fed"] += 1

            if rsm_retrade.state_name == "SWEEP_DETECTED":
                pipeline["retrade_fvg_scanned"] += 1
                sweep_bar = bars_15m[sweep_bar_idx]
                sweep_chunk = bars_15m[sweep_bar_idx - WINDOW:sweep_bar_idx + 1] if sweep_bar_idx >= WINDOW else chunk
                rsm_retrade.on_sweep_confirmed(sweep_chunk, sweep_bar)
                if rsm_retrade.state_name == "TRIGGER_READY":
                    pipeline["retrade_wick_rejection"] += 1

            if rsm_retrade.can_trigger():
                pipeline["retrade_trigger_ready"] += 1
                phase_rt = detect_phase_from_timestamp(current.timestamp)
                if phase_rt != SessionPhase.NEWYORK:
                    rsm_retrade.reset()
                else:
                    retrade_entry_price = current.close
                    retrade_risk_pts = atr_val * SL_ATR_MULT
                    retrade_fvg = rsm_retrade.trigger_fvg

                    if ss.retrade_side == "long":
                        if retrade_fvg:
                            retrade_sl = retrade_fvg.bottom - (retrade_risk_pts * FVG_BUFFER_MULT)
                        else:
                            retrade_sl = retrade_entry_price - retrade_risk_pts * 2
                        retrade_tp = (
                            ss.london_high if ss.london_high > retrade_entry_price
                            else retrade_entry_price + retrade_risk_pts * TP_RR
                        )
                    else:
                        if retrade_fvg:
                            retrade_sl = retrade_fvg.top + (retrade_risk_pts * FVG_BUFFER_MULT)
                        else:
                            retrade_sl = retrade_entry_price + retrade_risk_pts * 2
                        retrade_tp = (
                            ss.london_low if ss.london_low < retrade_entry_price
                            else retrade_entry_price - retrade_risk_pts * TP_RR
                        )

                    retrade_qty = (
                        (INITIAL_CAPITAL * RISK_PER_TRADE) / abs(retrade_sl - retrade_entry_price)
                        if abs(retrade_sl - retrade_entry_price) > 0 else 0
                    )

                    if retrade_qty > 0:
                        retrade_trade = {
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
                        }
                        active_trades.append(retrade_trade)
                        pipeline["retrade_entry"] += 1
                        ss.trades_today += 1

                    rsm_retrade.reset()
                    ss.retrade_armed = False

    # Kapatilmamis islemleri son fiyatla kapat
    if bars_15m:
        last_price = bars_15m[-1].close
        for trade in active_trades:
            if not trade.get("closed"):
                trade["exit_price"] = last_price
                trade["exit_bar"] = len(bars_15m) - 1
                trade["result"] = "OPEN"
                trade["closed"] = True
                if trade["side"] == "long":
                    diff = last_price - trade["entry_price"]
                else:
                    diff = trade["entry_price"] - last_price
                trade["pnl"] = round(diff * trade["qty"], 2)
                risk = abs(trade["initial_sl"] - trade["entry_price"])
                trade["rr"] = round(diff / risk if risk > 0 else 0, 2)
                trades.append(trade)
                pipeline["closed"] += 1

    # Raporlama
    print("=" * 78)
    print("  SNIPER BACKTEST RAPORU v7 — FVG Wick Rejection + Short + Dual Trailing + Retrade")
    print(f"  {SYMBOL} | {len(trades)} Islem")
    print("=" * 78)
    print(f"  Parametreler: SL=FVG edge +/- buffer | TP=London High/Low veya {TP_RR}R | Risk=%{RISK_PER_TRADE*100:.0f}")
    print(f"                FVG buffer={FVG_BUFFER_MULT}x risk_pts | Session=NEWYORK | ADX yok | OB yok")

    print(f"\n  PIPELINE")
    print(f"  {'-' * 56}")
    for k, v in pipeline.items():
        print(f"  {k:<35}{v}")
    print(f"  {'total_signals':<35}{total_signals}")
    print(f"  {'rejected_other':<35}{rejected_other}")

    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)

        dd_max = 0.0
        dd_peak = INITIAL_CAPITAL
        running = INITIAL_CAPITAL
        for t in trades:
            running += t["pnl"]
            if running > dd_peak:
                dd_peak = running
            dd = (dd_peak - running) / dd_peak * 100 if dd_peak > 0 else 0
            if dd > dd_max:
                dd_max = dd

        cons_loss = 0
        max_cons_loss = 0
        for t in trades:
            if t["pnl"] <= 0:
                cons_loss += 1
                if cons_loss > max_cons_loss:
                    max_cons_loss = cons_loss
            else:
                cons_loss = 0

        tp_count = sum(1 for t in trades if t["result"] == "TP")
        sl_count = sum(1 for t in trades if t["result"] == "SL")
        open_count = sum(1 for t in trades if t["result"] == "OPEN")
        avg_trailing = sum(t.get("trailing_count", 0) for t in trades) / len(trades)

        print(f"\n  GENEL PERFORMANS")
        print(f"  {'-' * 56}")
        print(f"  {'Toplam Islem':<30}{len(trades)}")
        if trades:
            print(f"  {'Kazanan':<30}{len(wins)}  (%{len(wins) / len(trades) * 100:.1f})")
            print(f"  {'Kaybeden':<30}{len(losses)}  (%{len(losses) / len(trades) * 100:.1f})")
        print(f"  {'TP ile kapanan':<30}{tp_count}  (%{tp_count/len(trades)*100:.1f})")
        print(f"  {'SL ile kapanan':<30}{sl_count}  (%{sl_count/len(trades)*100:.1f})")
        print(f"  {'Acik kalan':<30}{open_count}")
        print(f"  {'Toplam PnL (USDT)':<30}{total_pnl:+.2f}")
        print(f"  {'Max Drawdown':<30}{dd_max:.1f}%")
        print(f"  {'Max Ardisik Kayip':<30}{max_cons_loss} islem")
        print(f"  {'Ort. Trailing Sayisi':<30}{avg_trailing:.1f}")

        wt = sum(t["rr"] for t in wins) / len(wins) if wins else 0
        lt = sum(t["rr"] for t in losses) / len(losses) if losses else 0
        print(f"\n  R:R ANALIZI")
        print(f"  {'-' * 56}")
        print(f"  {'Ort. Kazanan R:R':<30}{wt:+.2f}")
        print(f"  {'Ort. Kaybeden R:R':<30}{lt:+.2f}")
        if wt > 0 and lt != 0:
            profit_factor = abs(wt / lt)
            print(f"  {'Profit Factor (W/L)':<30}{profit_factor:.2f}")

        long_trades = [t for t in trades if t["side"] == "long"]
        short_trades = [t for t in trades if t["side"] == "short"]
        long_wins = [t for t in long_trades if t["pnl"] > 0]
        short_wins = [t for t in short_trades if t["pnl"] > 0]
        long_pnl = sum(t["pnl"] for t in long_trades)
        short_pnl = sum(t["pnl"] for t in short_trades)
        long_wr = len(long_wins) / len(long_trades) * 100 if long_trades else 0
        short_wr = len(short_wins) / len(short_trades) * 100 if short_trades else 0
        long_avg_win_rr = sum(t["rr"] for t in long_wins) / len(long_wins) if long_wins else 0
        short_avg_win_rr = sum(t["rr"] for t in short_wins) / len(short_wins) if short_wins else 0

        print(f"\n  LONG / SHORT KARSILASTIRMA")
        print(f"  {'-' * 60}")
        print(f"  {'':<12}{'Islem':<8}{'WR':<8}{'PnL':<14}{'Avg Win RR':<12}{'Trail'}")
        print(f"  {'-' * 60}")
        print(f"  {'LONG':<12}{len(long_trades):<8}{long_wr:<7.1f}%{long_pnl:<+14.2f}{long_avg_win_rr:<+12.2f}{sum(t.get('trailing_count',0) for t in long_trades)/max(len(long_trades),1):.1f}")
        print(f"  {'SHORT':<12}{len(short_trades):<8}{short_wr:<7.1f}%{short_pnl:<+14.2f}{short_avg_win_rr:<+12.2f}{sum(t.get('trailing_count',0) for t in short_trades)/max(len(short_trades),1):.1f}")

        primary_trades = [t for t in trades if not t.get("is_retrade", False)]
        retrade_trades = [t for t in trades if t.get("is_retrade", False)]
        if retrade_trades:
            rt_wins = [t for t in retrade_trades if t["pnl"] > 0]
            rt_pnl = sum(t["pnl"] for t in retrade_trades)
            rt_wr = len(rt_wins) / len(retrade_trades) * 100
            print(f"\n  RETRADE (2. ENTRY) ANALIZI")
            print(f"  {'-' * 56}")
            print(f"  {'1. Entry (gunun ilk islemi)':<30}{len(primary_trades)}")
            print(f"  {'2. Entry (retrade)':<30}{len(retrade_trades)}  (PnL={rt_pnl:+.2f}, WR={rt_wr:.1f}%)")
            if total_pnl:
                print(f"  {'Retrade katkisi (toplam PnL)':<30}%{rt_pnl/total_pnl*100:.1f}")

        trailed = [t for t in trades if t.get("trailing_count", 0) > 0]
        not_trailed = [t for t in trades if t.get("trailing_count", 0) == 0]
        if trailed and not_trailed:
            trailed_pnl = sum(t["pnl"] for t in trailed)
            not_trailed_pnl = sum(t["pnl"] for t in not_trailed)
            trailed_wr = sum(1 for t in trailed if t["pnl"] > 0) / len(trailed) * 100
            not_trailed_wr = sum(1 for t in not_trailed if t["pnl"] > 0) / len(not_trailed) * 100
            print(f"\n  TRAILING ETKISI")
            print(f"  {'-' * 56}")
            print(f"  {'Trailing aktif islem':<30}{len(trailed)} (PnL={trailed_pnl:+.2f}, WR={trailed_wr:.1f}%)")
            print(f"  {'Trailing yok islem':<30}{len(not_trailed)} (PnL={not_trailed_pnl:+.2f}, WR={not_trailed_wr:.1f}%)")

        print(f"\n  SON 10 TRADE")
        print(f"  {'-' * 85}")
        print(f"  {'#':<4}{'Side':<7}{'Entry':<11}{'Exit':<11}{'PnL':<10}{'R:R':<8}{'Result':<6}{'Trail':<6}{'FVG'}")
        print(f"  {'-' * 85}")
        for i, t in enumerate(trades[-10:]):
            fvg_info = "YES" if t.get("trigger_fvg") else "NO"
            print(
                f"  {i+1:<4}{t['side']:<7}"
                f"{t['entry_price']:<11.2f}"
                f"{t.get('exit_price', 0):<11.2f}"
                f"{t['pnl']:<+10.2f}"
                f"{t['rr']:<+8.2f}"
                f"{t['result']:<6}"
                f"{t.get('trailing_count', 0):<6}"
                f"{fvg_info}"
            )
    else:
        print("\n  Sinyal bulunamadi.")

    print()
    print("=" * 78)


if __name__ == "__main__":
    run()
