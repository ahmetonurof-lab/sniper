"""
analyzer_v3.py — HTF FVG Wick Rejection + Cift Yonlu Trailing SL/TP + Short Destek.
ADX filtresi yok. OB tarasi yok. Sadece FVG.
Long ve Short islem destegi.
Yeni FVG olusunca SL ve TP birlikte tasinin (Trailing).
Kullanilabilir: python analyzer_v3.py [SYMBOL]
  Sembol belirtilmezse BTCUSDT kullanilir.
"""

import csv
import os
import sys
from datetime import UTC, datetime

from fvg import detect_fvgs
from models import Bar
from retrace_state import RetraceStateMachine
from session import DailyBias, SessionState

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Coin bazli konfigürasyon ──────────────────────────────────────────────
SYMBOL_CONFIGS = {
    "BTCUSDT": {
        "MIN_FVG_SIZE": 10.0,
        "SL_ATR_MULT": 1.5,
        "TP_RR": 2.0,
        "FVG_BUFFER_MULT": 0.25,
    },
    "BNBUSDT": {
        "MIN_FVG_SIZE": 0.5,
        "SL_ATR_MULT": 1.5,
        "TP_RR": 2.0,
        "FVG_BUFFER_MULT": 0.25,
    },
    "AVAXUSDT": {
        "MIN_FVG_SIZE": 0.03,
        "SL_ATR_MULT": 1.5,
        "TP_RR": 2.0,
        "FVG_BUFFER_MULT": 0.25,
    },
    "LINKUSDT": {
        "MIN_FVG_SIZE": 0.02,
        "SL_ATR_MULT": 1.5,
        "TP_RR": 2.0,
        "FVG_BUFFER_MULT": 0.25,
    },
}

INITIAL_CAPITAL = 10000.0
RISK_PER_TRADE = 0.01

# Sembol belirtilmezse BTCUSDT
SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
CFG = SYMBOL_CONFIGS.get(SYMBOL, SYMBOL_CONFIGS["BTCUSDT"])
MIN_FVG_SIZE = CFG["MIN_FVG_SIZE"]
SL_ATR_MULT = CFG["SL_ATR_MULT"]
TP_RR = CFG["TP_RR"]
FVG_BUFFER_MULT = CFG["FVG_BUFFER_MULT"]

CSV_FILE = os.path.join(os.path.dirname(__file__), "data", f"{SYMBOL}_1m.csv")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")
os.makedirs(REPORT_DIR, exist_ok=True)
REPORT_FILE = os.path.join(REPORT_DIR, f"{SYMBOL}_report.md")


def load_data(filepath):
    bars = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            ts = int(datetime.strptime(row["open_time"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
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


def apply_trailing_stop(active_trades, current_fvgs, risk_pts, buffer_mult, pipeline, min_fvg_size):
    """
    Aktif islemler icin FVG bazli trailing SL/TP guncellemesi.
    Sadece yone uygun, dolmamis ve gecersiz olmamis FVG'ler kullanilir.
    SL kaydirildiginda TP de ayni miktarda kayar (dual trailing).
    """
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

            buffer = risk_pts * buffer_mult

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
    return active_trades


def close_trades(active_trades, current, scan_bar, trades, pipeline):
    """Aktif islemler icin SL/TP exit kontrolu."""
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
        else:
            still_active.append(trade)
    return still_active


def format_report(symbol, trades, pipeline, total_signals, rejected_other,
                  initial_capital, sl_atr_mult, tp_rr, fvg_buffer_mult,
                  min_fvg_size, n_bars_1m, n_bars_15m):
    """Markdown formatinda detayli rapor olustur."""
    lines = []
    lines.append(f"# SNIPER BACKTEST RAPORU — {symbol}")
    lines.append("")
    lines.append(f"## Parametreler")
    lines.append(f"| Parametre | Deger |")
    lines.append(f"|-----------|-------|")
    lines.append(f"| Sembol | {symbol} |")
    lines.append(f"| Min FVG Size | {min_fvg_size} |")
    lines.append(f"| SL ATR Mult | {sl_atr_mult} |")
    lines.append(f"| TP R:R | {tp_rr} |")
    lines.append(f"| FVG Buffer Mult | {fvg_buffer_mult} |")
    lines.append(f"| Risk/Trade | %{RISK_PER_TRADE * 100:.0f} |")
    lines.append(                f"| Session | ALL (CBDR+London+NY) |")
    lines.append(f"| Initial Capital | {initial_capital:.0f} USDT |")
    lines.append(f"| 1m Bars | {n_bars_1m} |")
    lines.append(f"| 15m Bars | {n_bars_15m} |")
    lines.append("")

    lines.append("## Pipeline")
    lines.append("| Asama | Sayi |")
    lines.append("|-------|------|")
    for k, v in pipeline.items():
        lines.append(f"| {k} | {v} |")
    lines.append(f"| total_signals | {total_signals} |")
    lines.append(f"| rejected_other | {rejected_other} |")
    lines.append("")

    if not trades:
        lines.append("**Sinyal bulunamadi.**")
        return "\n".join(lines)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)

    # Drawdown
    dd_max = 0.0
    dd_peak = initial_capital
    running = initial_capital
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

    lines.append("## Genel Performans")
    lines.append("| Metrik | Deger |")
    lines.append("|--------|-------|")
    lines.append(f"| Toplam Islem | {len(trades)} |")
    lines.append(f"| Kazanan | {len(wins)} (%{len(wins) / len(trades) * 100:.1f}) |")
    lines.append(f"| Kaybeden | {len(losses)} (%{len(losses) / len(trades) * 100:.1f}) |")
    lines.append(f"| TP ile kapanan | {tp_count} (%{tp_count / len(trades) * 100:.1f}) |")
    lines.append(f"| SL ile kapanan | {sl_count} (%{sl_count / len(trades) * 100:.1f}) |")
    lines.append(f"| Acik kalan | {open_count} |")
    lines.append(f"| Toplam PnL | **{total_pnl:+.2f} USDT** |")
    lines.append(f"| Max Drawdown | {dd_max:.1f}% |")
    lines.append(f"| Max Ardisik Kayip | {max_cons_loss} islem |")
    lines.append(f"| Ort. Trailing Sayisi | {avg_trailing:.1f} |")
    lines.append("")

    # R:R analizi
    wt = sum(t["rr"] for t in wins) / len(wins) if wins else 0
    lt = sum(t["rr"] for t in losses) / len(losses) if losses else 0
    lines.append("## R:R Analizi")
    lines.append("| Metrik | Deger |")
    lines.append("|--------|-------|")
    lines.append(f"| Ort. Kazanan R:R | {wt:+.2f} |")
    lines.append(f"| Ort. Kaybeden R:R | {lt:+.2f} |")
    if wt > 0 and lt != 0:
        lines.append(f"| Profit Factor | {abs(wt / lt):.2f} |")
    lines.append("")

    # Long/Short
    long_trades = [t for t in trades if t["side"] == "long"]
    short_trades = [t for t in trades if t["side"] == "short"]
    long_wins = [t for t in long_trades if t["pnl"] > 0]
    short_wins = [t for t in short_trades if t["pnl"] > 0]
    long_pnl = sum(t["pnl"] for t in long_trades)
    short_pnl = sum(t["pnl"] for t in short_trades)
    long_wr = len(long_wins) / len(long_trades) * 100 if long_trades else 0
    short_wr = len(short_wins) / len(short_trades) * 100 if short_trades else 0
    long_avg_rr = sum(t["rr"] for t in long_wins) / len(long_wins) if long_wins else 0
    short_avg_rr = sum(t["rr"] for t in short_wins) / len(short_wins) if short_wins else 0
    long_trail = sum(t.get("trailing_count", 0) for t in long_trades) / max(len(long_trades), 1)
    short_trail = sum(t.get("trailing_count", 0) for t in short_trades) / max(len(short_trades), 1)

    lines.append("## Long / Short Karsilastirma")
    lines.append("| Yon | Islem | WR | PnL | Avg Win RR | Ort Trail |")
    lines.append("|-----|-------|----|-----|-----------|----------|")
    lines.append(
        f"| LONG | {len(long_trades)} | {long_wr:.1f}% | {long_pnl:+.2f} | {long_avg_rr:+.2f} | {long_trail:.1f} |"
    )
    lines.append(
        f"| SHORT | {len(short_trades)} | {short_wr:.1f}% | {short_pnl:+.2f} | {short_avg_rr:+.2f} | {short_trail:.1f} |"
    )
    lines.append("")

    # Trailing etkisi
    trailed = [t for t in trades if t.get("trailing_count", 0) > 0]
    not_trailed = [t for t in trades if t.get("trailing_count", 0) == 0]
    if trailed and not_trailed:
        trailed_pnl = sum(t["pnl"] for t in trailed)
        not_trailed_pnl = sum(t["pnl"] for t in not_trailed)
        trailed_wr = sum(1 for t in trailed if t["pnl"] > 0) / len(trailed) * 100
        not_trailed_wr = sum(1 for t in not_trailed if t["pnl"] > 0) / len(not_trailed) * 100
        lines.append("## Trailing Etkisi")
        lines.append("| Durum | Islem | PnL | WR |")
        lines.append("|-------|-------|-----|----|")
        lines.append(
            f"| Trailing aktif | {len(trailed)} | {trailed_pnl:+.2f} | {trailed_wr:.1f}% |"
        )
        lines.append(
            f"| Trailing yok | {len(not_trailed)} | {not_trailed_pnl:+.2f} | {not_trailed_wr:.1f}% |"
        )
        lines.append("")

    # Son 20 trade
    lines.append("## Son 20 Trade")
    lines.append("| # | Side | Entry | Exit | PnL | R:R | Result | Trail | FVG |")
    lines.append("|---|------|-------|------|-----|-----|--------|-------|-----|")
    for i, t in enumerate(trades[-20:]):
        fvg_info = "YES" if t.get("trigger_fvg") else "NO"
        lines.append(
            f"| {i + 1} | {t['side']} | {t['entry_price']:.2f} | "
            f"{t.get('exit_price', 0):.2f} | {t['pnl']:+.2f} | "
            f"{t['rr']:+.2f} | {t['result']} | "
            f"{t.get('trailing_count', 0)} | {fvg_info} |"
        )
    lines.append("")

    return "\n".join(lines)


def run():
    print(f"Loading data for {SYMBOL}...")
    bars_1m = load_data(CSV_FILE)
    bars_15m = resample_15m(bars_1m)
    print(f"  1m: {len(bars_1m)} bars | 15m: {len(bars_15m)} bars")
    print(f"  Config: MIN_FVG_SIZE={MIN_FVG_SIZE}, SL_ATR_MULT={SL_ATR_MULT}, TP_RR={TP_RR}, FVG_BUFFER={FVG_BUFFER_MULT}\n")

    ss = SessionState()
    rsm = RetraceStateMachine(min_fvg_size=MIN_FVG_SIZE)
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
        "new_entry": 0,
        "trailing_sl_updates": 0,
        "trailing_tp_updates": 0,
        "closed": 0,
    }
    total_signals = 0
    rejected_other = 0

    for scan_bar in range(WINDOW, len(bars_15m), 5):
        chunk = bars_15m[scan_bar - WINDOW : scan_bar + 1]
        current = bars_15m[scan_bar]
        atr_val = max(current.range, current.close * 0.0001)

        try:
            entry_dt = datetime.fromtimestamp(current.timestamp / 1000, tz=UTC)
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
            }
            active_trades.append(new_trade)
            pipeline["new_entry"] += 1
            ss.trades_today += 1
            rsm.reset()

        # 6 — Trailing SL/TP (FVG bazli, coin-specific min_fvg_size)
        if active_trades and current.is_closed:
            current_fvgs = detect_fvgs(
                chunk,
                lookback=min(50, len(chunk)),
                timeframe="15m",
                min_fvg_size=MIN_FVG_SIZE,
            )
            active_trades = apply_trailing_stop(
                active_trades, current_fvgs, risk_pts, FVG_BUFFER_MULT, pipeline, MIN_FVG_SIZE
            )

        # 7 — Exit kontrolu
        active_trades = close_trades(active_trades, current, scan_bar, trades, pipeline)

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

    # Rapor olustur
    report = format_report(
        SYMBOL, trades, pipeline, total_signals, rejected_other,
        INITIAL_CAPITAL, SL_ATR_MULT, TP_RR, FVG_BUFFER_MULT,
        MIN_FVG_SIZE, len(bars_1m), len(bars_15m),
    )

    # Ekrana yazdir
    print(report)

    # Dosyaya kaydet
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nRapor kaydedildi: {REPORT_FILE}")


if __name__ == "__main__":
    run()
