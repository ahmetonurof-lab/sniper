"""
bot.py — sniper paper trade orchestrator
CBDR -> Sweep -> FVG Wick Rejection -> Entry -> Trailing -> Exit
Canli (paper) ortaminda calisir, gercek emir gondermez.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime

import config as cfg
from bot_infra import _close_ohlc_writers
from fvg import detect_fvgs
from models import Bar
from retrace_state import RetraceStateMachine
from session import SessionState, detect_phase_from_timestamp
from websocket import BinanceWSHub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.handlers.TimedRotatingFileHandler(
            filename="output/paper_trade.log",
            when="midnight",
            backupCount=7,
            encoding="utf-8-sig",
        ),
    ],
)
log = logging.getLogger("sniper.paper")
log.propagate = False
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
log.addHandler(_console)

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

os.makedirs("output", exist_ok=True)

# ── Coin bazli konfigurasyon (config.py'den okur) ──────────────
INITIAL_CAPITAL = cfg.INITIAL_BALANCE
RISK_PER_TRADE = cfg.RISK_PER_TRADE


class PaperTrader:
    def __init__(self, symbols: list[str] | None = None):
        self.symbols = [s.upper() for s in (symbols or cfg.SYMBOLS)]
        self.hub = BinanceWSHub(
            symbols=self.symbols,
            timeframes=["15m"],
            max_bars=500,
            base_url="wss://fstream.binancefuture.com/stream?streams=",
        )
        self.states: dict[str, SessionState] = {}
        self.rsms: dict[str, RetraceStateMachine] = {}
        self.cfgs: dict[str, dict] = {}
        self.active_trades: dict[str, dict] = {}
        self.trades: list[dict] = []
        self._log_state: dict[str, dict] = {}
        self._balance = INITIAL_CAPITAL

        for sym in self.symbols:
            min_fvg = cfg.FVG_SIZE_MAP.get(sym, 0.5)
            self.cfgs[sym] = {
                "MIN_FVG_SIZE": min_fvg,
                "SL_ATR_MULT": cfg.SL_ATR_MULT,
                "TP_RR": cfg.TP_RR,
                "FVG_BUFFER_MULT": cfg.FVG_BUFFER_MULT,
            }
            self.states[sym] = SessionState()
            self.rsms[sym] = RetraceStateMachine(min_fvg_size=min_fvg)

    def _pl(self, sym: str, key: str, msg: str):
        prev = self._log_state.get(sym, {}).get(key)
        if prev == msg:
            return
        self._log_state.setdefault(sym, {})[key] = msg
        print(f"[{sym:<12}] {msg}")

    def _session_label(self, hour: int) -> str:
        if hour >= 22 or hour < 2:
            return "ASIA"
        elif 2 <= hour < 13:
            return "LONDON"
        return "NEWYORK"

    def _on_15m_close(self, sym: str, bars_15m: list[Bar]):
        cfg = self.cfgs[sym]
        min_fvg = cfg["MIN_FVG_SIZE"]
        sl_atr = cfg["SL_ATR_MULT"]
        tp_rr = cfg["TP_RR"]
        fvg_buf = cfg["FVG_BUFFER_MULT"]

        current = bars_15m[-1]
        atr_val = max(current.range, current.close * 0.0001)
        try:
            dt = datetime.fromtimestamp(current.timestamp / 1000, tz=UTC)
        except Exception:
            return
        hour = dt.hour
        session = self._session_label(hour)

        # Session gate
        if session == "ASIA":
            self._pl(sym, "session", f"🟥 SESSION: ASIA (22:00-02:00) | trading kapali")
            return
        self._pl(sym, "session", f"🟩 SESSION: {session} | {hour:02d}:{dt.minute:02d} UTC | ✅")

        # CBDR tracking + sweep
        ss = self.states[sym]
        ss.update(dt, current.open, current.high, current.low, current.close, atr_val)

        if not ss.sweep_confirmed:
            return

        sweep_dir = ss.sweep_direction or "bullish"
        sweep_lvl = ss.sweep_level or 0.0
        self._pl(sym, "sweep", f"🟩 SWEEP: DETECTED | TYPE: {sweep_dir.upper()} | LEVEL: {sweep_lvl:.2f}")

        rsm = self.rsms[sym]
        if rsm.state_name == "IDLE":
            rsm.on_sweep(direction=sweep_dir, level=sweep_lvl, bar_index=current.index)

        if rsm.state_name == "SWEEP_DETECTED":
            rsm.on_sweep_confirmed(bars_15m, current)
            if rsm.state_name == "TRIGGER_READY":
                tfvg = rsm.trigger_fvg
                self._pl(sym, "fvg_size", f"🟩 FVG_SCAN | MIN_SIZE: {min_fvg}")
                self._pl(sym, "wick", (
                    f"🟩 WICK_REJECTION | FVG:[{tfvg.bottom:.2f}-{tfvg.top:.2f}] "
                    f"| WICK_TOUCHED: {tfvg.top if sweep_dir == 'bullish' else tfvg.bottom:.2f} "
                    f"| CLOSE: {current.close:.2f} | BODY_SAFE"
                ))
            elif rsm.state_name == "IDLE":
                self._pl(sym, "fvg_size", f"🟨 FVG_SCAN | MIN_SIZE: {min_fvg}")
                if "wick" in self._log_state.get(sym, {}):
                    del self._log_state[sym]["wick"]

        if rsm.can_trigger():
            self._try_entry(sym, current, atr_val, rsm, ss, sweep_dir, sl_atr, tp_rr, fvg_buf, min_fvg)

        # Trailing (15m FVG bazli, backtest ile ayni)
        trade = self.active_trades.get(sym)
        if trade and current.is_closed:
            chunk = bars_15m[:-1] if len(bars_15m) > 1 else bars_15m
            fvgs = detect_fvgs(chunk, lookback=min(50, len(chunk)), timeframe="15m", min_fvg_size=min_fvg)
            for fvg in fvgs:
                if trade["side"] == "long" and fvg.direction != "bullish":
                    continue
                if trade["side"] == "short" and fvg.direction != "bearish":
                    continue
                if fvg.filled or fvg.invalidated:
                    continue
                buffer = atr_val * fvg_buf
                if trade["side"] == "long":
                    new_sl = fvg.bottom - buffer
                    if new_sl > trade["sl"]:
                        sl_diff = new_sl - trade["sl"]
                        trade["sl"] = new_sl
                        trade["tp"] = trade["tp"] + sl_diff
                        trade["trailing_count"] += 1
                        log.info("[TRAIL] %s trail#%d sl=%.2f tp=%.2f", sym, trade["trailing_count"], trade["sl"], trade["tp"])
                else:
                    new_sl = fvg.top + buffer
                    if new_sl < trade["sl"]:
                        sl_diff = trade["sl"] - new_sl
                        trade["sl"] = new_sl
                        trade["tp"] = trade["tp"] - sl_diff
                        trade["trailing_count"] += 1
                        log.info("[TRAIL] %s trail#%d sl=%.2f tp=%.2f", sym, trade["trailing_count"], trade["sl"], trade["tp"])

    def _try_entry(self, sym, current, atr_val, rsm, ss, sweep_dir, sl_atr, tp_rr, fvg_buf, min_fvg):
        if sym in self.active_trades:
            rsm.reset()
            return

        side = "long" if sweep_dir == "bullish" else "short"
        entry_price = current.close
        risk_pts = atr_val * sl_atr
        trigger_fvg = rsm.trigger_fvg

        if side == "long":
            sl = (trigger_fvg.bottom - (risk_pts * fvg_buf)) if trigger_fvg else (entry_price - risk_pts * 2)
            tp = ss.london_high if ss.london_high > entry_price else entry_price + risk_pts * tp_rr
        else:
            sl = (trigger_fvg.top + (risk_pts * fvg_buf)) if trigger_fvg else (entry_price + risk_pts * 2)
            tp = ss.london_low if ss.london_low < entry_price else entry_price - risk_pts * tp_rr

        risk_dist = abs(sl - entry_price)
        if risk_dist <= 0:
            rsm.reset()
            return

        qty = (self._balance * RISK_PER_TRADE) / risk_dist
        if qty <= 0:
            rsm.reset()
            return

        self._pl(sym, "entry", (
            f"🟨 ENTRY: {side.upper()} | PRICE: {entry_price:.2f} "
            f"| SL: {sl:.2f} | TP: {tp:.2f} | QTY: {qty:.4f}"
        ))
        log.info("[PAPER] %s %s @ %.2f sl=%.2f tp=%.2f qty=%.4f",
                 sym, side, entry_price, sl, tp, qty)

        self.active_trades[sym] = {
            "entry_bar_index": current.index,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
            "qty": qty,
            "side": side,
            "initial_sl": sl,
            "initial_tp": tp,
            "trailing_count": 0,
        }
        rsm.reset()

        # Exit kontrolu (15m bazli)
        trade = self.active_trades.get(sym)
        if trade:
            side = trade["side"]
            if side == "long":
                if current.low <= trade["sl"]:
                    trade["exit_price"] = trade["sl"]
                    trade["result"] = "SL"
                    self._exit_trade(sym, trade, current)
                elif current.high >= trade["tp"]:
                    trade["exit_price"] = trade["tp"]
                    trade["result"] = "TP"
                    self._exit_trade(sym, trade, current)
            else:
                if current.high >= trade["sl"]:
                    trade["exit_price"] = trade["sl"]
                    trade["result"] = "SL"
                    self._exit_trade(sym, trade, current)
                elif current.low <= trade["tp"]:
                    trade["exit_price"] = trade["tp"]
                    trade["result"] = "TP"
                    self._exit_trade(sym, trade, current)

    def _exit_trade(self, sym, trade, current):
        diff = (trade["exit_price"] - trade["entry_price"]) if trade["side"] == "long" \
            else (trade["entry_price"] - trade["exit_price"])
        pnl = round(diff * trade["qty"], 2)
        self._balance += pnl
        self._pl(sym, f"exit_{current.timestamp}", (
            f"🟥 EXIT: {trade['result']} | PRICE: {trade['exit_price']:.2f} "
            f"| PNL: {pnl:+.2f} | BALANCE: {self._balance:.2f} | TRAIL: {trade['trailing_count']}"
        ))
        log.info("[PAPER] %s %s exit=%s pnl=%.2f balance=%.2f",
                 sym, trade['result'], trade['exit_price'], pnl, self._balance)
        self.trades.append({
            **trade, "pnl": pnl, "exit_bar": current.index, "close_time": current.timestamp,
        })
        del self.active_trades[sym]

    async def on_15m(self, sym: str, bars: list[Bar]):
        if len(bars) < 10:
            return
        self._on_15m_close(sym, bars)

    async def run(self):
        for sym in self.symbols:
            self.hub.register_callback(sym, "15m", lambda b, s=sym: self.on_15m(s, b))

        log.info("PaperTrader baslatildi. Semboller: %s", self.symbols)
        log.info("BASLANGIC BAKIYESI: %.2f USDT", self._balance)
        await self.hub.run()


if __name__ == "__main__":
    bot = PaperTrader(sys.argv[1:] if len(sys.argv) > 1 else None)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Kullanici tarafindan durduruldu.")
        bot.hub.stop()
        _close_ohlc_writers()
