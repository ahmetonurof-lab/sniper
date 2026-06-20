"""
bot.py — sniper paper trade orchestrator
CBDR -> Sweep -> FVG Wick Rejection -> Entry -> Trailing -> Exit
Canli (paper) ortaminda calisir, gercek emir gondermez.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sys
import urllib.request
from datetime import UTC, datetime

import config as cfg
from bot_binance import BinanceRESTClient
from bot_infra import _close_ohlc_writers, _RateLimiter
from fvg import detect_fvgs
from models import Bar
from retrace_state import RetraceStateMachine
from session import SessionState, detect_phase_from_timestamp
from websocket import BinanceWSHub

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "..", "output")
os.makedirs(_OUTPUT_DIR, exist_ok=True)

_log_file = os.path.join(_OUTPUT_DIR, "paper_trade.log")

# Root logger: ws_hub ve diger loglar icin
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.FileHandler(_log_file, mode="a", encoding="utf-8-sig")],
    force=True,
)

# sniper.paper: console + file (propagate kapali, cift yazmayi onler)
log = logging.getLogger("sniper.paper")
log.setLevel(logging.INFO)
log.propagate = False
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
log.addHandler(_ch)
log.addHandler(logging.FileHandler(_log_file, mode="a", encoding="utf-8-sig"))
log.handlers[1].setFormatter(_fmt)

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── Coin bazli konfigurasyon (config.py'den okur) ──────────────
INITIAL_CAPITAL = cfg.INITIAL_BALANCE
RISK_PER_TRADE = cfg.RISK_PER_TRADE


class PaperTrader:
    def __init__(self, symbols: list[str] | None = None):
        self.symbols = [s.upper() for s in (symbols or cfg.SYMBOLS)]

        # Testnet / Mainnet config
        self.testnet = cfg.IS_TESTNET
        if self.testnet:
            self.rest_base = "https://demo-fapi.binance.com"
            self.ws_base = "wss://fstream.binancefuture.com/stream?streams="
        else:
            self.rest_base = "https://fapi.binance.com"
            self.ws_base = "wss://fstream.binance.com/stream?streams="

        self.hub = BinanceWSHub(
            symbols=self.symbols,
            timeframes=["15m"],
            max_bars=500,
            base_url=self.ws_base,
        )
        self.states: dict[str, SessionState] = {}
        self.rsms: dict[str, RetraceStateMachine] = {}
        self.cfgs: dict[str, dict] = {}
        self.active_trades: dict[str, dict] = {}
        self.trades: list[dict] = []
        self._log_state: dict[str, dict] = {}
        self._balance = INITIAL_CAPITAL

        # REST client (testnet/mainnet)
        api_key = cfg.BINANCE_API_KEY or ""
        api_secret = cfg.BINANCE_API_SECRET or ""
        self.rest = BinanceRESTClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=self.rest_base,
            rate_limiter=_RateLimiter(1200),
            semaphore=asyncio.Semaphore(5),
        )

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
        print(f"[{sym:<12}] {msg}", flush=True)

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

        # Session gate + CBDR durumu (her zaman goster)
        ss = self.states[sym]
        ss.update(dt, current.open, current.high, current.low, current.close, atr_val)

        if session == "ASIA":
            self._pl(sym, "session", f"🟥 SESSION: ASIA (22:00-02:00) | bar_utc_hour={hour} | trading kapali")
            return

        cbdr_status = "✅ LOCKED" if ss.cbdr_locked else "⏳ BODY TRACKING..."
        self._pl(sym, "session", (
            f"🟩 SESSION: {session} | {hour:02d}:{dt.minute:02d} UTC "
            f"| CBDR: {cbdr_status}"
        ))

        if not ss.cbdr_locked:
            # CBDR henuz kilitlenmedi, body tracking devam ediyor
            return

        if not ss.sweep_confirmed:
            # CBDR kilitli ama sweep yok — bekliyor
            self._pl(sym, "sweep_wait", (
                f"🟨 SWEEP: BEKLENIYOR | CBDR_BODY: [{ss.cbdr_body_low:.2f}-{ss.cbdr_body_high:.2f}]"
            ))
            return

        # Sweep var
        sweep_dir = ss.sweep_direction or "bullish"
        sweep_lvl = ss.sweep_level or 0.0
        self._pl(sym, "sweep", f"🟩 SWEEP: DETECTED | TYPE: {sweep_dir.upper()} | LEVEL: {sweep_lvl:.2f}")
        # sweep_wait varsa temizle
        if "sweep_wait" in self._log_state.get(sym, {}):
            del self._log_state[sym]["sweep_wait"]

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

    async def _prefill_bars(self, sym: str):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&limit=500"
        try:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(url, timeout=15).read().decode(),
            )
            data = json.loads(raw)
            bars = [
                Bar(
                    index=i,
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    timestamp=int(k[0]),
                    is_closed=True,
                )
                for i, k in enumerate(data)
            ]
            self.hub.prefill_bars(sym, "15m", bars)
            log.info("[PREFILL] %s 15m: %d bar yuklendi", sym, len(bars))
        except Exception as e:
            log.warning("[PREFILL] %s REST hatasi: %s", sym, e)

    def _warmup_cbdr(self, sym: str):
        """Prefill barlarla CBDR body tracking'i besle (gecmis 22:00-02:00 barlarindan body hesapla)."""
        bars = self.hub.get_bars(sym, "15m")
        if not bars or len(bars) < 10:
            return
        ss = self.states[sym]
        for bar in bars:
            try:
                dt = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC)
            except Exception:
                continue
            atr = max(bar.range, bar.close * 0.0001)
            ss.update(dt, bar.open, bar.high, bar.low, bar.close, atr)
        log.info("[WARMUP] %s CBDR body: lock=%s | body=[%.2f-%.2f] | sweep=%s",
                 sym, ss.cbdr_locked, ss.cbdr_body_low, ss.cbdr_body_high, ss.sweep_confirmed)

    async def run(self):
        for sym in self.symbols:
            self.hub.register_callback(sym, "15m", lambda b, s=sym: self.on_15m(s, b))

        net = "TESTNET" if self.testnet else "MAINNET"
        log.info("PaperTrader baslatiliyor. Semboller: %s | %s", self.symbols, net)

        # Testnet bakiyesini cek
        if cfg.BINANCE_API_KEY:
            try:
                bal = await self.rest.get_balance()
                if bal > 0:
                    self._balance = bal
                    log.info("BALANCE: %.2f USDT (%s)", self._balance, net)
                else:
                    log.warning("BALANCE: 0 USDT, varsayilan %.2f kullaniliyor", INITIAL_CAPITAL)
            except Exception as e:
                log.warning("BALANCE: alinamadi (%s), varsayilan %.2f kullaniliyor", e, INITIAL_CAPITAL)
        else:
            log.info("BALANCE: varsayilan %.2f USDT (API key yok)", INITIAL_CAPITAL)

        # Gecmis barlari yukle
        await asyncio.gather(*[self._prefill_bars(sym) for sym in self.symbols])

        # CBDR body'yi gecmis barlardan hesapla (22:00-02:00 araligindakileri bul)
        for sym in self.symbols:
            self._warmup_cbdr(sym)

        # Prefill sonrasi hemen analizi tetikle (15dk bekleme yok)
        for sym in self.symbols:
            bars = self.hub.get_bars(sym, "15m")
            if bars and len(bars) >= 10:
                await self.on_15m(sym, bars)
                log.info("[INIT] %s ilk analiz tamam (%d bar)", sym, len(bars))

        log.info("Gecmis barlar yuklendi, WS baslatiliyor...")

        log.info("Gecmis barlar yuklendi, WS baslatiliyor...")
        await self.hub.run()


if __name__ == "__main__":
    bot = PaperTrader(sys.argv[1:] if len(sys.argv) > 1 else None)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Kullanici tarafindan durduruldu.")
        bot.hub.stop()
        _close_ohlc_writers()
