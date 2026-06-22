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
import time
import urllib.request
from datetime import UTC, datetime

import config as cfg
from bot_binance import BinanceRESTClient
from bot_infra import _close_ohlc_writers, _RateLimiter
from fvg import detect_fvgs
from models import Bar
from retrace_state import RetraceStateMachine
from session import DailyBias, SessionState
from state_manager import (
    can_open_trade,
    mark_trade_opened,
    mark_trade_closed,
    reconcile_from_active,
)
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

# sniper.paper: sadece file (console cikti yok)
log = logging.getLogger("sniper.paper")
log.setLevel(logging.INFO)
log.propagate = False
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log.addHandler(logging.FileHandler(_log_file, mode="a", encoding="utf-8-sig"))

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
        self._stage: dict[str, dict] = {}
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
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        # Farklı coin grubu arasında boşluk
        _prev_sym = getattr(self, "_prev_print_sym", None)
        _separator = "" if _prev_sym == sym else "\n"
        self._prev_print_sym = sym
        print(f"{_separator}[{ts}] [{sym:<12}] {msg}", flush=True)

    def _session_label(self, hour: int) -> str:
        if hour >= 22 or hour < 2:
            return "ASIA"
        elif 2 <= hour < 13:
            return "LONDON"
        return "NEWYORK"

    async def _on_15m_close(self, sym: str, bars_15m: list[Bar]):
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

        # Session gate + CBDR durumu
        ss = self.states[sym]
        ss.update(dt, current.open, current.high, current.low, current.close, atr_val)

        if session == "ASIA":
            self._pl(sym, "st_ses", "🟥 SESSION: ASIA | 22:00-02:00 UTC | trading kapali")
            self._stage.pop(sym, None)
            return

        # ── Stage-based pipeline display ──
        st = self._stage.setdefault(sym, {})
        ts = f"{hour:02d}:{dt.minute:02d}"

        bias_str = ""
        if ss.daily_bias != DailyBias.NEUTRAL:
            d = "LONG" if ss.daily_bias == DailyBias.BULLISH else "SHORT"
            c = "🟩" if d == "LONG" else "🟥"
            bias_str = f" | BIAS: {c}{d}"
        cbdr_s = "✅ LOCKED" if ss.cbdr_locked else "⏳ BODY TRACKING..."
        self._pl(sym, "st_ses", f"🟩 SESSION: {session} | {ts} UTC | CBDR: {cbdr_s}{bias_str}")

        if not ss.cbdr_locked:
            st.clear()
            return

        # ── Stage 1: SWEEP ──
        if ss.sweep_confirmed:
            sd = ss.sweep_direction or "bullish"
            sl = ss.sweep_level or 0.0
            si = "🟩" if sd == "bullish" else "🟥"
            self._pl(sym, "st_swp", f"🟩 SWEEP: DETECTED | {si}{sd.upper()} | {sl:.2f}")
        else:
            bstr = ""
            if ss.daily_bias != DailyBias.NEUTRAL:
                d = "LONG" if ss.daily_bias == DailyBias.BULLISH else "SHORT"
                c = "🟩" if d == "LONG" else "🟥"
                bstr = f" | BIAS: {c}{d}"
            self._pl(sym, "st_swp", f"🟨 SWEEP: BEKLENIYOR{bstr} | CBDR: [{ss.cbdr_body_low:.2f}-{ss.cbdr_body_high:.2f}] | {ts}")
            self._log_state.get(sym, {}).pop("st_fvg", None)
            self._log_state.get(sym, {}).pop("st_wck", None)
            return

        # ── Stage 2: FVG SCAN + Stage 3: WICK REJECTION ──
        rsm = self.rsms[sym]
        if rsm.state_name == "IDLE":
            rsm.on_sweep(direction=ss.sweep_direction or "bullish", level=ss.sweep_level or 0.0, bar_index=current.index)

        if rsm.state_name == "SWEEP_DETECTED":
            rsm.on_sweep_confirmed(bars_15m, current)

        if rsm.state_name == "TRIGGER_READY":
            tfvg = rsm.trigger_fvg
            self._pl(sym, "st_fvg", f"🟩 FVG_SCAN | MIN_SIZE: {min_fvg}")
            self._pl(sym, "st_wck", f"🟩 WICK_REJECTION | FVG:[{tfvg.bottom:.2f}-{tfvg.top:.2f}] | BODY_SAFE | CLOSE: {current.close:.2f}")
        elif rsm.state_name == "SWEEP_DETECTED":
            self._pl(sym, "st_fvg", f"🟨 FVG_SCAN | MIN_SIZE: {min_fvg} | FVG ARANIYOR...")
            self._log_state.get(sym, {}).pop("st_wck", None)
        else:
            self._pl(sym, "st_fvg", f"🟨 FVG_SCAN | MIN_SIZE: {min_fvg} | FVG BULUNAMADI")
            self._log_state.get(sym, {}).pop("st_wck", None)

        if rsm.can_trigger():
            await self._try_entry(sym, current, atr_val, rsm, ss, ss.sweep_direction or "bullish", sl_atr, tp_rr, fvg_buf, min_fvg)

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
                buffer = trade["risk_pts"] * fvg_buf
                if trade["side"] == "long":
                    new_sl = fvg.bottom - buffer
                    if new_sl > trade["sl"]:
                        sl_diff = new_sl - trade["sl"]
                        trade["sl"] = new_sl
                        trade["tp"] = trade["tp"] + sl_diff
                        trade["trailing_count"] += 1
                        log.info(
                            "[TRAIL] %s trail#%d sl=%.2f tp=%.2f",
                            sym,
                            trade["trailing_count"],
                            trade["sl"],
                            trade["tp"],
                        )
                        await self._update_orders(sym, trade)
                else:
                    new_sl = fvg.top + buffer
                    if new_sl < trade["sl"]:
                        sl_diff = trade["sl"] - new_sl
                        trade["sl"] = new_sl
                        trade["tp"] = trade["tp"] - sl_diff
                        trade["trailing_count"] += 1
                        log.info(
                            "[TRAIL] %s trail#%d sl=%.2f tp=%.2f",
                            sym,
                            trade["trailing_count"],
                            trade["sl"],
                            trade["tp"],
                        )
                        await self._update_orders(sym, trade)

        # Exit kontrolu (15m bazli)
        trade_exit = self.active_trades.get(sym)
        if trade_exit:
            side_exit = trade_exit["side"]
            if side_exit == "long":
                if current.low <= trade_exit["sl"]:
                    trade_exit["exit_price"] = trade_exit["sl"]
                    trade_exit["exit_bar"] = current.index
                    trade_exit["exit_timestamp"] = current.timestamp
                    trade_exit["result"] = "SL"
                    await self._exit_trade(sym, trade_exit, current, trade_exit["exit_timestamp"])
                elif current.high >= trade_exit["tp"]:
                    trade_exit["exit_price"] = trade_exit["tp"]
                    trade_exit["exit_bar"] = current.index
                    trade_exit["exit_timestamp"] = current.timestamp
                    trade_exit["result"] = "TP"
                    await self._exit_trade(sym, trade_exit, current, trade_exit["exit_timestamp"])
            else:
                if current.high >= trade_exit["sl"]:
                    trade_exit["exit_price"] = trade_exit["sl"]
                    trade_exit["exit_bar"] = current.index
                    trade_exit["exit_timestamp"] = current.timestamp
                    trade_exit["result"] = "SL"
                    await self._exit_trade(sym, trade_exit, current, trade_exit["exit_timestamp"])
                elif current.low <= trade_exit["tp"]:
                    trade_exit["exit_price"] = trade_exit["tp"]
                    trade_exit["exit_bar"] = current.index
                    trade_exit["exit_timestamp"] = current.timestamp
                    trade_exit["result"] = "TP"
                    await self._exit_trade(sym, trade_exit, current, trade_exit["exit_timestamp"])

    async def _try_entry(self, sym, current, atr_val, rsm, ss, sweep_dir, sl_atr, tp_rr, fvg_buf, min_fvg):
        if sym in self.active_trades:
            rsm.reset()
            return

        # -- Gunluk kota kontrolu: bugün bu sembol için zaten işlem açıldıysa geç --
        if not can_open_trade(sym):
            self._pl(sym, "quota", f"QUOTA: {sym} bugün işlem kotası doldu, sinyal pas geçiliyor")
            log.info("[STATE] %s günlük kota doldu, _try_entry atlandı", sym)
            rsm.reset()
            return
        # -- Gunluk kota kontrolu sonu --

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

        bias_icon = "🟩" if side == "long" else "🟥"
        self._pl(
            sym,
            "entry",
            (
                f"🟨 ENTRY: {bias_icon}{side.upper()} | PRICE: {entry_price:.2f} "
                f"| SL: {sl:.2f} | TP: {tp:.2f} | QTY: {qty:.4f}"
            ),
        )
        log.info("[PAPER] %s %s @ %.2f sl=%.2f tp=%.2f qty=%.4f", sym, side, entry_price, sl, tp, qty)

        # ── Testnet/Mainnet emirleri (sonnet trader.py'den adapte) ──
        if cfg.BINANCE_API_KEY and getattr(self, "_live", False):
            mkt_side = "BUY" if side == "long" else "SELL"
            sl_side = "SELL" if side == "long" else "BUY"

            # 1. Quantity + price precision
            try:
                rounded_qty = await self.rest.apply_amount_precision(sym, qty)
                valid_qty = await self.rest.validate_min_amount(sym, rounded_qty)
                if valid_qty <= 0:
                    self._pl(sym, "order_err", f"❌ ORDER: qty={qty:.6f} minQty altinda")
                    log.warning("[ORDER] %s qty=%.8f minQty altinda, emir atlandi", sym, qty)
                else:
                    rounded_sl = await self.rest.apply_price_precision(sym, sl)
                    rounded_tp = await self.rest.apply_price_precision(sym, tp)

                    # 2. MARKET entry
                    mkt_resp = await self.rest.place_market_order(sym, mkt_side, valid_qty)
                    mkt_id = mkt_resp.get("orderId") or mkt_resp.get("id") or ""
                    if mkt_id:
                        self._pl(sym, "order_ok", f"✅ ORDER: MARKET {mkt_side} OK | ID: {mkt_id}")
                        log.info("[ORDER] %s MARKET entry OK orderId=%s qty=%.8f", sym, mkt_id, valid_qty)

                        # 3. SL (algo endpoint)
                        sl_resp = await self.rest.place_stop_order(sym, sl_side, valid_qty, rounded_sl)
                        sl_id = sl_resp.get("algoId") or sl_resp.get("orderId") or sl_resp.get("id") or ""
                        if sl_id:
                            log.info("[ORDER] %s SL OK algoId=%s", sym, sl_id)
                        else:
                            log.warning("[ORDER] %s SL BASARISIZ! resp=%s", sym, sl_resp)

                        # 4. TP (algo endpoint)
                        tp_resp = await self.rest.place_tp_order(sym, sl_side, valid_qty, rounded_tp)
                        tp_id = tp_resp.get("algoId") or tp_resp.get("orderId") or tp_resp.get("id") or ""
                        if tp_id:
                            log.info("[ORDER] %s TP OK algoId=%s", sym, tp_id)
                        else:
                            log.warning("[ORDER] %s TP BASARISIZ! resp=%s", sym, tp_resp)
                    else:
                        self._pl(sym, "order_err", "❌ ORDER: MARKET BASARISIZ")
                        log.warning("[ORDER] %s MARKET entry BASARISIZ — SL/TP atlandi", sym)
            except Exception as e:
                self._pl(sym, "order_err", f"❌ ORDER: HATA — {e}")
                log.exception("[ORDER] %s beklenmeyen hata", sym)

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
            "risk_pts": risk_pts,
            "sl_order_id": sl_id if (cfg.BINANCE_API_KEY and getattr(self, "_live", False)) else "",
            "tp_order_id": tp_id if (cfg.BINANCE_API_KEY and getattr(self, "_live", False)) else "",
        }
        # -- Diske state yaz (restart koruması) --
        mark_trade_opened(sym, entry_price)
        rsm.reset()

    async def _update_orders(self, sym: str, trade: dict):
        if not cfg.BINANCE_API_KEY or not getattr(self, "_live", False):
            return
        sl_side = "SELL" if trade["side"] == "long" else "BUY"
        qty = trade.get("qty", trade.get("lot", 0))
        sl_resp = await self.rest.place_stop_order(
            sym, sl_side, qty, trade["sl"], client_id=f"sl_{sym}_{int(time.time())}"
        )
        sl_id = sl_resp.get("algoId") or sl_resp.get("orderId") or sl_resp.get("id") or ""
        trade["sl_order_id"] = sl_id
        tp_resp = await self.rest.place_tp_order(
            sym, sl_side, qty, trade["tp"], client_id=f"tp_{sym}_{int(time.time())}"
        )
        tp_id = tp_resp.get("algoId") or tp_resp.get("orderId") or tp_resp.get("id") or ""
        trade["tp_order_id"] = tp_id
        log.info("[ORDER] %s trailing guncellendi sl=%.2f (id=%s) tp=%.2f (id=%s)", sym, trade["sl"], sl_id, trade["tp"], tp_id)

    async def _exit_trade(self, sym, trade, current, exit_timestamp: int):
        diff = (
            (trade["exit_price"] - trade["entry_price"])
            if trade["side"] == "long"
            else (trade["entry_price"] - trade["exit_price"])
        )
        pnl = round(diff * trade["qty"], 2)
        self._balance += pnl
        self._pl(
            sym,
            f"exit_{exit_timestamp}",
            (
                f"🟥 EXIT: {trade['result']} | PRICE: {trade['exit_price']:.2f} "
                f"| PNL: {pnl:+.2f} | BALANCE: {self._balance:.2f} | TRAIL: {trade['trailing_count']}"
            ),
        )
        log.info(
            "[PAPER] %s %s exit=%s pnl=%.2f balance=%.2f", sym, trade["result"], trade["exit_price"], pnl, self._balance
        )

        # Testnet'teki pozisyonu kapat + eski SL/TP emirlerini iptal et
        if cfg.BINANCE_API_KEY and getattr(self, "_live", False):
            try:
                # Önce eski SL/TP emirlerini bul ve iptal et (-4130 hatasını önlemek için)
                try:
                    open_orders = await self.rest.get_all_orders(sym)
                    for o in open_orders if isinstance(open_orders, list) else []:
                        oid = o.get("algoId") or o.get("orderId") or o.get("id")
                        if oid:
                            is_algo = "algoId" in o
                            await self.rest.cancel_order(oid, sym, reason="exit_close", is_algo=is_algo)
                except Exception as e:
                    log.warning("[CANCEL] %s emir iptal hatasi (googled): %s", sym, e)

                close_side = "SELL" if trade["side"] == "long" else "BUY"
                qty = trade.get("qty", trade.get("lot", 0))
                rounded_qty = await self.rest.apply_amount_precision(sym, qty)
                valid_qty = await self.rest.validate_min_amount(sym, rounded_qty)
                if valid_qty > 0:
                    close_resp = await self.rest.place_market_order(sym, close_side, valid_qty)
                    if close_resp.get("orderId") or close_resp.get("id"):
                        log.info("[CLOSE] %s pozisyon kapatildi orderId=%s", sym, close_resp.get("orderId", ""))
                else:
                    log.warning("[CLOSE] %s qty=%.8f minQty altinda, pozisyon kapatilamadi", sym, qty)
            except Exception as e:
                log.warning("[CLOSE] %s pozisyon kapatma hatasi: %s", sym, e)
        self.trades.append(
            {
                **trade,
                "pnl": pnl,
                "exit_bar": trade["exit_bar"],
                "close_time": exit_timestamp,
            }
        )
        del self.active_trades[sym]
        # -- Diske state guncelle (trade kapandı) --
        mark_trade_closed(sym)

    async def on_15m(self, sym: str, bars: list[Bar]):
        if len(bars) < 10:
            return
        await self._on_15m_close(sym, bars)

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
        """Prefill barlarla CBDR body tracking'i besle (sadece 22:00-02:00 barlari)."""
        bars = self.hub.get_bars(sym, "15m")
        if not bars or len(bars) < 10:
            return
        ss = self.states[sym]
        for bar in bars:
            try:
                dt = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC)
            except Exception:
                continue
            # Sadece CBDR saatleri (22:00-02:00 UTC)
            if not (dt.hour >= 22 or dt.hour < 2):
                continue
            atr = max(bar.range, bar.close * 0.0001)
            ss.update(dt, bar.open, bar.high, bar.low, bar.close, atr)
        log.info(
            "[WARMUP] %s CBDR body: lock=%s | body=[%.2f-%.2f] | sweep=%s",
            sym,
            ss.cbdr_locked,
            ss.cbdr_body_low,
            ss.cbdr_body_high,
            ss.sweep_confirmed,
        )

    async def _recover_positions(self):
        """API'de açık pozisyon varsa active_trades'e yükle, cift trade'i engelle."""
        if not cfg.BINANCE_API_KEY:
            return
        try:
            positions = await self.rest.get_positions()
            if not positions:
                self._pl("SYSTEM", "recover", "✅ API'de acik pozisyon yok")
                return

            self._pl("SYSTEM", "recover", f"🔄 {len(positions)} pozisyon bulundu, envantere aliniyor...")
            for pos in positions:
                sym = pos["symbol"]
                if sym not in self.symbols:
                    continue
                amt = float(pos.get("positionAmt", 0))
                direction = "long" if amt > 0 else "short"
                entry = float(pos.get("entryPrice", 0))

                # SL/TP emirlerini çek (normal + algo)
                open_orders = await self.rest.get_all_orders(sym)
                sl_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o) in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                    and (o.get("reduceOnly") in (True, "true", "True") or o.get("closePosition") in (True, "true", "True"))
                ]
                tp_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o) in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                    and (o.get("reduceOnly") in (True, "true", "True") or o.get("closePosition") in (True, "true", "True"))
                ]

                if sl_orders and tp_orders:
                    sl_price = self.rest.get_order_price(sl_orders[0])
                    tp_price = self.rest.get_order_price(tp_orders[0])
                    risk_pts = abs(entry - sl_price) / 2
                    sl_id = sl_orders[0].get("algoId") or sl_orders[0].get("orderId") or ""
                    tp_id = tp_orders[0].get("algoId") or tp_orders[0].get("orderId") or ""
                    self.active_trades[sym] = {
                        "entry_bar_index": 0,
                        "entry_price": entry,
                        "sl": sl_price,
                        "tp": tp_price,
                        "qty": abs(amt),
                        "side": direction,
                        "initial_sl": sl_price,
                        "initial_tp": tp_price,
                        "trailing_count": 0,
                        "risk_pts": risk_pts,
                        "sl_order_id": sl_id,
                        "tp_order_id": tp_id,
                    }
                    self._pl(
                        sym,
                        "recover",
                        f"🔒 {direction.upper()} @ {entry:.2f} | SL={sl_price:.2f} TP={tp_price:.2f} | yeni trade engellendi",
                    )
                else:
                    self._pl(
                        sym, "recover", f"⚠️ {direction.upper()} @ {entry:.2f} | SL/TP bulunamadi (pozisyon korumasiz)"
                    )
                    atr_est = entry * 0.0001
                    risk_pts = atr_est * self.cfgs[sym]["SL_ATR_MULT"]
                    if direction == "long":
                        sl = entry - risk_pts * 2
                        tp = entry + risk_pts * self.cfgs[sym]["TP_RR"]
                    else:
                        sl = entry + risk_pts * 2
                        tp = entry - risk_pts * self.cfgs[sym]["TP_RR"]

                    self.active_trades[sym] = {
                        "entry_bar_index": 0,
                        "entry_price": entry,
                        "sl": sl,
                        "tp": tp,
                        "qty": abs(amt),
                        "side": direction,
                        "initial_sl": sl,
                        "initial_tp": tp,
                        "trailing_count": 0,
                        "risk_pts": risk_pts,
                    }
                    self._pl(
                        sym, "recover", f"🔒 {direction.upper()} @ {entry:.2f} | SYNTHETIC SL={sl:.2f} TP={tp:.2f}"
                    )
        except Exception as e:
            self._pl("SYSTEM", "recover", f"❌ Pozisyon kurtarma hatasi: {e}")

    async def run(self):
        for sym in self.symbols:
            self.hub.register_callback(sym, "15m", lambda b, s=sym: self.on_15m(s, b))

        net = "TESTNET" if self.testnet else "MAINNET"
        self._pl("SYSTEM", "start", f"🚀 PaperTrader baslatiliyor | Semboller: {self.symbols} | {net}")

        # Testnet bakiyesini cek
        if cfg.BINANCE_API_KEY:
            try:
                bal = await self.rest.get_balance()
                if bal > 0:
                    self._balance = bal
                    self._pl("SYSTEM", "balance", f"💰 BALANCE: {self._balance:.2f} USDT ({net})")
                else:
                    self._pl("SYSTEM", "balance", f"⚠️ BALANCE: 0 USDT, varsayilan {INITIAL_CAPITAL:.2f} kullaniliyor")
            except Exception as e:
                self._pl("SYSTEM", "balance", f"⚠️ BALANCE: alinamadi ({e}), varsayilan {INITIAL_CAPITAL:.2f}")
        else:
            self._pl("SYSTEM", "balance", f"💰 BALANCE: varsayilan {INITIAL_CAPITAL:.2f} USDT (API key yok)")

        # Live mod aktif — prefill/analiz öncesi, böylece trailing+entry emirleri çalışır
        self._live = True

        # API'de açık pozisyon varsa envantere al (restart koruması)
        await self._recover_positions()
        # -- State dosyasını active_trades ile senkronize et --
        reconcile_from_active(self.active_trades)

        # -- User Data Stream (WS ile SL/TP takibi) --
        if cfg.BINANCE_API_KEY:
            try:
                listen_key = await self.rest.get_listen_key()
                if listen_key:
                    self.hub.set_user_data_listen_key(listen_key)
                    self._register_user_data_callbacks()
                    asyncio.create_task(self.hub._listen_key_refresh_loop(self.rest))
                    log.info("[USER_DATA] Listen key aktif: %s...", listen_key[:10])
            except Exception as e:
                log.warning("[USER_DATA] Listen key basarisiz (devam): %s", e)

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
        await self.hub.run()

    # ─────────────────────────────────────────────────────────────────
    # User Data Stream callback'leri
    # ─────────────────────────────────────────────────────────────────

    def _register_user_data_callbacks(self) -> None:
        @self.hub.on_user_data("ORDER_TRADE_UPDATE")
        async def on_order_update(msg: dict) -> None:
            od = msg.get("o", {})
            sym = od.get("s", "")
            status = od.get("X", "")
            oid = str(od.get("c", "") or od.get("i", ""))
            log.info("[WS-ORDER] %s status=%s id=%s", sym, status, oid)
            if status not in ("CANCELED", "EXPIRED"):
                return
            trade = self.active_trades.get(sym)
            if not trade:
                return
            s_id = str(trade.get("sl_order_id", ""))
            t_id = str(trade.get("tp_order_id", ""))
            if oid not in (s_id, t_id):
                return
            label = "SL" if oid == s_id else "TP"
            log.warning("🚨 [WS-REPAIR] %s %s emri silindi — onariliyor...", sym, label)
            try:
                await self._repair_protection(sym, trade, has_sl=(oid != s_id), has_tp=(oid != t_id))
            except Exception as e:
                log.critical("[WS-REPAIR] %s onarim hatasi: %s", sym, e)

        @self.hub.on_user_data("ACCOUNT_UPDATE")
        async def on_account_update(msg: dict) -> None:
            ud = msg.get("a", {})
            for bal in ud.get("B", []):
                if bal.get("a") in ("USDT", "FDUSD", "USDC"):
                    self._balance = float(bal.get("bc", self._balance))

    async def _repair_protection(self, sym: str, trade: dict, has_sl: bool, has_tp: bool) -> None:
        """WS tarafindan silinen SL/TP emirlerini yeniden olustur."""
        if not has_sl and trade.get("sl"):
            sl_side = "SELL" if trade["side"] == "long" else "BUY"
            sl_resp = await self.rest.place_stop_order(sym, sl_side, trade["qty"], trade["sl"])
            trade["sl_order_id"] = sl_resp.get("algoId") or sl_resp.get("orderId") or ""
            log.info("[REPAIR] %s SL yeniden kuruldu: %.2f (id=%s)", sym, trade["sl"], trade["sl_order_id"])
        if not has_tp and trade.get("tp"):
            sl_side = "SELL" if trade["side"] == "long" else "BUY"
            tp_resp = await self.rest.place_tp_order(sym, sl_side, trade["qty"], trade["tp"])
            trade["tp_order_id"] = tp_resp.get("algoId") or tp_resp.get("orderId") or ""
            log.info("[REPAIR] %s TP yeniden kuruldu: %.2f (id=%s)", sym, trade["tp"], trade["tp_order_id"])
        log.info("[REPAIR] %s onarim tamam", sym)


if __name__ == "__main__":
    bot = PaperTrader(sys.argv[1:] if len(sys.argv) > 1 else None)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Kullanici tarafindan durduruldu.")
        bot.hub.stop()
        _close_ohlc_writers()
