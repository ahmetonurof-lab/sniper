"""
bot.py — sniper paper trade orchestrator
CBDR -> Sweep -> FVG Wick Rejection -> Entry -> Trailing (1m) -> Exit (1m) -> Retrade
Backtest (analyzer.py) ile birebir ayni performans.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from collections import deque
from datetime import UTC, datetime, timezone, timedelta

import config as cfg
from bot_binance import BinanceRESTClient
from bot_infra import _close_ohlc_writers, _RateLimiter
from models import ActiveTrade, Bar, PendingLock, Result
from retrace_state import RetraceStateMachine
from session import SessionState
from state_manager import (
    mark_trade_opened,
    mark_trade_closed,
    reconcile_from_active,
    get_trade_count_today,
    load_retrade_arm,
    clear_retrade_arm,
)
from state_writer import write_state
from trade_exporter import export_trade
from snapshot.snapshot import capture_snapshot
from trading import (
    SignalEngine,
    EntryManager,
    TrailingManager,
    RetradeEngine,
    OrderManager,
    RecoveryManager,
    ConsoleReporter,
    UserDataHandler,
)
from websocket import BinanceWSHub

TR_TZ = timezone(timedelta(hours=3))

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "..", "output")
os.makedirs(_OUTPUT_DIR, exist_ok=True)

_log_file = os.path.join(_OUTPUT_DIR, "paper_trade.log")


def _setup_logging() -> logging.Logger:
    """Logger yapılandırması: TR saat dilimi, UTF-8, dosya."""
    logging.Formatter.converter = staticmethod(
        lambda ts: datetime.fromtimestamp(ts, TR_TZ).timetuple()
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
        handlers=[logging.FileHandler(_log_file, mode="a", encoding="utf-8-sig")],
        force=True,
    )

    _log = logging.getLogger("sniper.paper")
    _log.setLevel(logging.INFO)
    _log.propagate = False
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s")
    _log.addHandler(logging.FileHandler(_log_file, mode="a", encoding="utf-8-sig"))

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        _log.debug(
            "stdout/stderr reconfigure atlandi (encoding zaten UTF-8 veya non-TTY)"
        )

    return _log


log = _setup_logging()

INITIAL_CAPITAL = cfg.INITIAL_BALANCE
RISK_PER_TRADE = cfg.RISK_PER_TRADE


class PaperTrader:
    def __init__(self, symbols: list[str] | None = None):
        self.symbols = [s.upper() for s in (symbols or cfg.SYMBOLS)]

        self.testnet = cfg.IS_TESTNET
        if self.testnet:
            self.rest_base = "https://demo-fapi.binance.com"
            self.ws_base = "wss://fstream.binancefuture.com/stream?streams="
        else:
            self.rest_base = "https://fapi.binance.com"
            self.ws_base = "wss://fstream.binance.com/stream?streams="

        self.hub = BinanceWSHub(
            symbols=self.symbols,
            timeframes=["1m", "15m"],
            max_bars=500,
            base_url=self.ws_base,
        )
        self.states: dict[str, SessionState] = {}
        self.rsms: dict[str, RetraceStateMachine] = {}
        self.rsms_retrade: dict[str, RetraceStateMachine] = {}
        self.retrade_engines: dict[str, RetradeEngine] = {}
        self.signal_engines: dict[str, SignalEngine] = {}
        self.entry_manager: EntryManager | None = None
        self.cfgs: dict[str, dict] = {}
        self.active_trades: dict[str, ActiveTrade] = {}
        self.trades: deque[dict] = deque(maxlen=1000)
        self.reporter = ConsoleReporter()
        self._live = False
        self._wallet_balance: float = INITIAL_CAPITAL  # WS'den gelen wb (görüntüleme)
        self._available_balance: float = (
            INITIAL_CAPITAL  # REST availableBalance (position sizing)
        )

        api_key = cfg.BINANCE_API_KEY or ""
        api_secret = cfg.BINANCE_API_SECRET or ""
        self.rest = BinanceRESTClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=self.rest_base,
            rate_limiter=_RateLimiter(1200),
            semaphore=asyncio.Semaphore(5),
        )
        self.entry_manager = EntryManager(
            rest_client=self.rest,
            is_live=bool(cfg.BINANCE_API_KEY),
        )
        self.order_manager = OrderManager(
            rest_client=self.rest,
            is_live=bool(cfg.BINANCE_API_KEY),
        )
        self.recovery_manager = RecoveryManager(
            rest_client=self.rest,
            symbols=self.symbols,
            cfgs=self.cfgs,
            states=self.states,
            active_trades=self.active_trades,
            pl_callback=self._pl,
            order_manager=self.order_manager,
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
            self.rsms_retrade[sym] = RetraceStateMachine(
                min_fvg_size=min_fvg * cfg.RETRADE_FVG_SIZE_MULT
            )
            self.signal_engines[sym] = SignalEngine(self.rsms[sym])
            self.retrade_engines[sym] = RetradeEngine(self.rsms_retrade[sym])

    def _pl(self, sym: str, key: str, msg: str, force: bool = False):
        """ConsoleReporter'a delegate et. Imza birebir aynı."""
        self.reporter.emit(sym, key, msg, force)

    def _session_label(self, hour: int) -> str:
        if hour >= 22 or hour < 2:
            return "ASIA"
        elif 2 <= hour < 13:
            return "LONDON"
        return "NEWYORK"

    # ── 15m: Sinyal kurulumu (CBDR, Sweep, FVG, Entry, Retrade) ──

    async def _on_15m_close(self, sym: str, bars_15m: list[Bar]):
        sym_cfg = self.cfgs[sym]
        min_fvg = sym_cfg["MIN_FVG_SIZE"]
        sl_atr = sym_cfg["SL_ATR_MULT"]
        tp_rr = sym_cfg["TP_RR"]
        fvg_buf = sym_cfg["FVG_BUFFER_MULT"]

        current = bars_15m[-1]
        atr_val = max(current.range, current.close * cfg.DEFAULT_ATR_FALLBACK_PCT)
        try:
            dt = datetime.fromtimestamp(current.timestamp / 1000, tz=UTC)
        except Exception:
            return
        hour = dt.hour
        session = self._session_label(hour)

        ss = self.states[sym]
        ss.update(dt, current.open, current.high, current.low, current.close, atr_val)

        # Pozisyon açıkken sinyal taramasını atla. Trailing + exit _on_1m_close'da.
        if sym in self.active_trades:
            self.reporter.display_active_position(
                sym, self.active_trades[sym], hour, dt.minute
            )
            return

        if session == "ASIA":
            self._pl(
                sym,
                "st_ses",
                "\U0001f7e5 SESSION: ASIA | 22:00-02:00 UTC | trading kapali",
                force=True,
            )
            return

        # ── Session/CBDR status display → ConsoleReporter (Faz 6.2) ──
        self.reporter.display_session_status(sym, session, hour, dt.minute, ss)

        if not ss.cbdr_locked:
            log.info("[SKIP] %s CBDR henuz kilitlenmedi — sinyal taranmadi", sym)
            return

        # ── Sweep status display → ConsoleReporter (Faz 6.2) ──
        sweep_status = self.reporter.display_sweep_status(sym, ss, hour, dt.minute)
        if sweep_status == "dead":
            return
        if sweep_status == "waiting":
            await self._check_retrade(sym, bars_15m, current, atr_val, ss)
            return
        # "detected": devam

        rsm = self.rsms[sym]
        engine = self.signal_engines[sym]

        # FIX #9: Retrade armed → primary RSM'i atla, ölü döngüyü engelle.
        if ss.retrade_armed:
            await self._check_retrade(sym, bars_15m, current, atr_val, ss)
            return

        # ── Blok 8: RSM state progression → SignalEngine ──
        engine.progress_rsm(bars_15m, current, ss)

        # ── Blok 9: FVG/Wick durum yazdırma → ConsoleReporter (Faz 6.2) ──
        self.reporter.display_fvg_status(sym, rsm, min_fvg, current.close)

        # ── Blok 10: Trigger check + filtreler → SignalEngine ──
        result = engine.evaluate_trigger(current, ss)

        if result.decision == "TRIGGER":
            await self._try_entry(
                sym,
                current,
                atr_val,
                rsm,
                ss,
                result.direction,
                sl_atr,
                tp_rr,
                fvg_buf,
                min_fvg,
                is_retrade=False,
            )
        elif result.decision == "SKIP":
            # Filtre reddetti → rsm zaten resetlendi, erken dönüş
            # (orijinalde filter SKIP'lerinde return vardı, _check_retrade atlanırdı)
            return

        await self._check_retrade(sym, bars_15m, current, atr_val, ss)

        # State writer — dashboard için
        write_state(
            self.states,
            self.active_trades,
            self._available_balance,
            self._wallet_balance,
            self.symbols,
        )

    # ── Retrade: trailing sweep + FVG + 2. entry (analyzer.py #8) ──

    async def _check_retrade(
        self,
        sym: str,
        bars_15m: list[Bar],
        current: Bar,
        atr_val: float,
        ss: SessionState,
    ):
        sym_cfg = self.cfgs[sym]
        if not ss.retrade_armed:
            return
        if ss.trades_today != 1:
            log.info(
                "[SKIP] %s retrade — trades_today=%d (beklenen=1)", sym, ss.trades_today
            )
            return
        if sym in self.active_trades:
            log.info("[SKIP] %s retrade — aktif trade var, beklemede", sym)
            return

        # ── Sweep tespiti → RetradeEngine ──
        sweep_result = RetradeEngine.detect_sweep(bars_15m, current, ss.retrade_side)

        if not sweep_result.found:
            ss.retrade_fvg_attempts += 1
            log.info("[SKIP] %s retrade — sweep bulunamadi (%s)", sym, ss.retrade_side)
            return

        self._pl(
            sym,
            "rt_sweep",
            f"\U0001f7e9 RETRADE SWEEP | {ss.retrade_side.upper()} yonunde sweep bulundu bar={sweep_result.sweep_bar_idx}",
        )

        # ── RSM progression → RetradeEngine ──
        rsm_r = self.rsms_retrade[sym]
        retrade_engine = self.retrade_engines[sym]
        retrade_engine.progress_rsm(
            bars_15m, sweep_result.sweep_bar_idx, sweep_result.sweep_dir
        )

        # ── Trigger check + filtreler → RetradeEngine ──
        trigger_decision = retrade_engine.evaluate_trigger(
            current, sweep_result.sweep_bar_idx, ss.retrade_entry_bar
        )

        if trigger_decision.decision == "TRIGGER":
            await self._try_entry(
                sym,
                current,
                atr_val,
                rsm_r,
                ss,
                sweep_result.sweep_dir,
                sym_cfg["SL_ATR_MULT"],
                sym_cfg["TP_RR"],
                sym_cfg["FVG_BUFFER_MULT"],
                sym_cfg["MIN_FVG_SIZE"],
                is_retrade=True,
            )
            if sym not in self.active_trades:
                ss.retrade_armed = False
                clear_retrade_arm(sym)
            rsm_r.reset()
        elif trigger_decision.decision == "SKIP":
            # Filtre reddetti → rsm zaten resetlendi, erken dönüş
            return
        else:
            ss.retrade_fvg_attempts += 1

        # ── LHR fallback (FVG attempts exhausted) → RetradeEngine (Faz 4.3) ──
        if (
            ss.retrade_fvg_attempts >= cfg.RETRADE_FVG_MAX_ATTEMPTS
            and sym not in self.active_trades
        ):
            ss.retrade_mode = "lhr"
            lhr_result = RetradeEngine.try_lhr_fallback(
                retrade_side=ss.retrade_side,
                current_close=current.close,
                atr_val=atr_val,
                london_high=ss.london_high,
                london_low=ss.london_low,
                tp_rr=sym_cfg["TP_RR"],
            )
            if lhr_result.in_zone:
                risk_map = cfg.SYMBOL_RISK_MAP.get(sym, {})
                risk_pct = risk_map.get("retrade", RISK_PER_TRADE)
                EntryManager.execute_lhr_entry(
                    sym=sym,
                    side=lhr_result.side,
                    current=current,
                    atr_val=atr_val,
                    sl=lhr_result.sl,
                    tp=lhr_result.tp,
                    ss=ss,
                    balance=self._available_balance,
                    risk_pct=risk_pct,
                    leverage=cfg.LEVERAGE,
                    zone_bottom=lhr_result.zone_bottom,
                    zone_top=lhr_result.zone_top,
                    active_trades=self.active_trades,
                    pl_callback=self._pl,
                )

    # ── 1m: Trailing + Exit (hibrit izleme) ──

    async def _on_1m_close(self, sym: str, bars_1m: list[Bar]):
        trade = self.active_trades.get(sym)
        if not trade:
            return

        sym_cfg = self.cfgs[sym]
        fvg_buf = sym_cfg["FVG_BUFFER_MULT"]
        min_fvg = sym_cfg["MIN_FVG_SIZE"]
        current = bars_1m[-1]

        # ── FVG Trailing → TrailingManager ──
        bars_15m = self.hub.get_bars(sym, "15m")
        if bars_15m:
            trail_result = TrailingManager.evaluate_trail(
                bars_15m, trade, fvg_buf, min_fvg
            )

            if trail_result.updated:
                # FIX #2: Rollback için eski değerleri yedekle
                old_sl = trade["sl"]
                old_tp = trade["tp"]
                old_trailing_count = trade["trailing_count"]

                trade["sl"] = trail_result.new_sl
                trade["tp"] = trail_result.new_tp
                trade["trailing_count"] = trail_result.trail_count

                success = await self.order_manager.update_trail_orders(sym, trade)
                if not success:
                    log.warning(
                        "[TRAIL] %s UPDATE FAIL -> in-memory SL/TP rollback yapiliyor",
                        sym,
                    )
                    trade["sl"] = old_sl
                    trade["tp"] = old_tp
                    trade["trailing_count"] = old_trailing_count

        # ── Exit kontrolü → TrailingManager ──
        exit_decision = TrailingManager.check_exit(current, trade)
        if exit_decision.triggered:
            trade["exit_price"] = exit_decision.exit_price
            trade["exit_bar"] = current.index
            trade["exit_timestamp"] = current.timestamp
            trade["result"] = exit_decision.result
            await self._exit_trade(sym, trade, current.timestamp)

    # ── Entry ──

    async def _try_entry(
        self,
        sym,
        current,
        atr_val,
        rsm,
        ss,
        sweep_dir,
        sl_atr,
        tp_rr,
        fvg_buf,
        min_fvg,
        is_retrade=False,
    ):
        if sym in self.active_trades:
            log.info("[SKIP] %s entry — aktif trade var (rsm reset)", sym)
            rsm.reset()
            return

        side = "long" if sweep_dir == "bullish" else "short"
        entry_price = current.close
        risk_pts = atr_val * sl_atr

        sl, tp = EntryManager.calculate_sl_tp(
            side=side,
            entry_price=entry_price,
            risk_pts=risk_pts,
            fvg_buf=fvg_buf,
            tp_rr=tp_rr,
            trigger_fvg=rsm.trigger_fvg,
            london_high=ss.london_high,
            london_low=ss.london_low,
        )

        # ── 1. SENKRON VALİDASYONLAR (PENDING KİLİDİNDEN ÖNCE) → EntryManager ──
        risk_dist = abs(sl - entry_price)
        valid, err_msg = EntryManager.validate_risk(risk_dist, atr_val)
        if not valid:
            log.warning("[ENTRY] %s %s — trade atlandı", sym, err_msg)
            rsm.reset()
            return

        # Entry öncesi taze availableBalance (position sizing için)
        if cfg.BINANCE_API_KEY:
            try:
                fresh_bal = await self.rest.get_balance()
                if fresh_bal > 0:
                    self._available_balance = fresh_bal
            except Exception:
                pass

        risk_map = cfg.SYMBOL_RISK_MAP.get(sym, {})
        if is_retrade:
            risk_pct = risk_map.get("retrade", RISK_PER_TRADE)
        else:
            risk_pct = risk_map.get("primary", RISK_PER_TRADE)
        qty = EntryManager.calculate_qty(
            self._available_balance, risk_pct, risk_dist, cfg.LEVERAGE, entry_price
        )
        if qty <= 0:
            log.warning("[SKIP] %s entry — qty=%.6f <= 0 (rsm reset)", sym, qty)
            rsm.reset()
            return

        # ── 2. PENDING KİLİDİ (API ÇAĞRISINDAN HEMEN ÖNCE) ──
        with PendingLock(self.active_trades, sym, logger=log) as lock:
            self._pl(
                sym,
                "entry",
                f"\U0001f7e8 ENTRY: {side.upper()} | PRICE: {entry_price:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | QTY: {qty:.4f}",
            )
            log.info(
                "[PAPER] %s %s @ %.2f sl=%.2f tp=%.2f qty=%.4f",
                sym,
                side,
                entry_price,
                sl,
                tp,
                qty,
            )

            sl_id = ""
            tp_id = ""
            if cfg.BINANCE_API_KEY and getattr(self, "_live", False):
                mkt_side = "BUY" if side == "long" else "SELL"

                assert self.entry_manager is not None
                exec_result = await self.entry_manager.execute_live_entry(
                    sym, side, qty, sl, tp, entry_price
                )

                if not exec_result.success:
                    self._pl(sym, "order_err", f"\u274c ORDER: {exec_result.error}")
                    log.warning(
                        "[ORDER] %s %s — trade kaydedilmedi", sym, exec_result.error
                    )
                    rsm.reset()
                    return

                sl_id = exec_result.sl_order_id
                tp_id = exec_result.tp_order_id
                _binance_qty = (
                    exec_result.qty
                )  # Binance onayli miktar (precision sonrasi)

                self._pl(
                    sym,
                    "order_ok",
                    f"\u2705 ORDER: MARKET {mkt_side} OK | ID: (live)",
                )

            lock.commit()  # PENDING korunur

        # NOTE: lock.commit() ile ActiveTrade ataması arasında await yok —
        # şu an race condition teorik. Eğer ActiveTrade.__init__ asenkron
        # olursa bu window kapatılmalı (PendingLock atomic blok genişletilmeli).
        # ── 3. BAŞARILI KAYIT (PENDING ÜZERİNE YAZ) ──
        self.active_trades[sym] = ActiveTrade(
            symbol=sym,
            side=side,
            entry_price=entry_price,
            entry_bar_index=current.index,
            sl=sl,
            tp=tp,
            qty=qty,
            initial_sl=sl,
            initial_tp=tp,
            risk_pts=risk_pts,
            trailing_count=0,
            is_retrade=is_retrade,
            trigger_fvg=rsm.trigger_fvg,
            sl_order_id=sl_id
            if (cfg.BINANCE_API_KEY and getattr(self, "_live", False))
            else "",
            tp_order_id=tp_id
            if (cfg.BINANCE_API_KEY and getattr(self, "_live", False))
            else "",
        )
        if is_retrade:
            clear_retrade_arm(sym)
        else:
            mark_trade_opened(sym, entry_price)
        ss.trades_today += 1
        rsm.reset()

    async def _exit_trade(self, sym, trade, exit_timestamp: int):
        diff = (
            (trade["exit_price"] - trade["entry_price"])
            if trade["side"] == "long"
            else (trade["entry_price"] - trade["exit_price"])
        )
        pnl = round(diff * trade["qty"], 2)
        self._available_balance += pnl
        self._pl(
            sym,
            f"exit_{exit_timestamp}",
            f"\U0001f7e5 EXIT: {trade['result']} | PRICE: {trade['exit_price']:.2f} | PNL: {pnl:+.2f} | AVL: {self._available_balance:.2f} | WAL: {self._wallet_balance:.2f} | TRAIL: {trade['trailing_count']}",
        )
        log.info(
            "[PAPER] %s %s exit=%s pnl=%.2f available=%.2f",
            sym,
            trade["result"],
            trade["exit_price"],
            pnl,
            self._available_balance,
        )

        # FIX #5: Manuel kapanış emri kaldırıldı.
        # SL/TP emirleri reduceOnly=true ile kurulduğundan Binance pozisyonu
        # zaten kapattı. Buradan tekrar place_market_order göndermek, sıfır pozisyon
        # üzerine emir atarak ters yönde yeni pozisyon açıyordu.
        # Yapılması gereken: karşı taraftaki bekleyen emri iptal etmek.
        await self.order_manager.cleanup_on_exit(sym, trade, trade["result"])

        # FIX #2: Retrade arm → RetradeEngine (Faz 6.1)
        RetradeEngine.arm_retrade(sym, trade, self.states[sym], self._pl)

        export_trade(sym, trade, pnl, self.states[sym])

        try:
            snap = capture_snapshot(sym, trade, pnl, self.states[sym])
            if snap:
                trade["snapshot_file"] = snap
        except Exception:
            log.warning("[SNAPSHOT] %s snapshot alinamadi", sym)

        self.trades.append(
            {
                **trade,
                "sym": sym,
                "pnl": pnl,
                "exit_bar": trade.get("exit_bar", 0),
                "close_time": exit_timestamp,
            }
        )
        del self.active_trades[sym]
        mark_trade_closed(sym)

    async def on_15m(self, sym: str, bars: list[Bar]):
        if len(bars) < 10:
            return
        await self._on_15m_close(sym, bars)

    async def on_1m(self, sym: str, bars: list[Bar]):
        if len(bars) < 2:
            return
        await self._on_1m_close(sym, bars)

    async def _prefill_bars(self, sym: str, timeframe: str = "15m") -> Result[None]:
        # P9.5: urllib.request.urlopen → BinanceRESTClient.get() (native async aiohttp)
        r = await self.rest.get(
            "/fapi/v1/klines",
            f"symbol={sym}&interval={timeframe}&limit=500",
        )
        if r.is_err:
            return Result.fail(r.error)
        data = r.value
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
        self.hub.prefill_bars(sym, timeframe, bars)
        log.info("[PREFILL] %s %s: %d bar yuklendi", sym, timeframe, len(bars))
        return Result.ok(None)

    def _warmup_cbdr(self, sym: str):
        bars = self.hub.get_bars(sym, "15m")
        if not bars or len(bars) < 10:
            return
        ss = self.states[sym]
        for bar in bars:
            try:
                dt = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC)
            except Exception:
                continue
            # FIX #1: Tüm barlar SessionState'e beslenmeli.
            # Eski filtre (sadece 22:00-02:00 CBDR saatleri) London/NY barlarını
            # atlıyordu; london_high/london_low=0 kalıyor ve TP yanlış hesaplanıyordu.
            atr = max(bar.range, bar.close * cfg.DEFAULT_ATR_FALLBACK_PCT)
            ss.update(dt, bar.open, bar.high, bar.low, bar.close, atr)
        log.info(
            "[WARMUP] %s CBDR body: lock=%s | body=[%.2f-%.2f] | sweep=%s",
            sym,
            ss.cbdr_locked,
            ss.cbdr_body_low,
            ss.cbdr_body_high,
            ss.sweep_confirmed,
        )

    async def _set_leverage(self, symbol: str) -> Result[None]:
        """POST /fapi/v1/leverage — sembol için kaldıraç ayarı.

        Returns:
            Result[None] — başarılıysa ok, hata varsa fail.
        """
        if not cfg.BINANCE_API_KEY:
            return Result.ok(None)
        r = await self.rest.post(
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": cfg.LEVERAGE},
        )
        if r.is_err:
            return Result.fail(r.error)
        effective = r.value.get("leverage", cfg.LEVERAGE)
        self._pl(symbol, "leverage", f"⚙️ LEVERAGE: {effective}x set edildi")
        log.info("[LEVERAGE] %s leverage=%dx OK", symbol, effective)
        return Result.ok(None)

    async def run(self):
        for sym in self.symbols:
            self.hub.register_callback(sym, "15m", lambda b, s=sym: self.on_15m(s, b))
            self.hub.register_callback(sym, "1m", lambda b, s=sym: self.on_1m(s, b))

        net = "TESTNET" if self.testnet else "MAINNET"
        self._pl(
            "SYSTEM",
            "start",
            f"\U0001f680 PaperTrader baslatiliyor | Semboller: {self.symbols} | {net}",
        )

        if cfg.BINANCE_API_KEY:
            try:
                bal = await self.rest.get_balance()
                if bal > 0:
                    self._available_balance = bal
                    self._wallet_balance = bal
                    self._pl(
                        "SYSTEM",
                        "balance",
                        f"\U0001f4b0 AVL: {self._available_balance:.2f} | WAL: {self._wallet_balance:.2f} USDT ({net})",
                    )
                else:
                    self._pl(
                        "SYSTEM",
                        "balance",
                        f"\u26a0\ufe0f BALANCE: 0 USDT, varsayilan {INITIAL_CAPITAL:.2f} kullaniliyor",
                    )
            except Exception as e:
                self._pl(
                    "SYSTEM",
                    "balance",
                    f"\u26a0\ufe0f BALANCE: alinamadi ({e}), varsayilan {INITIAL_CAPITAL:.2f}",
                )
        else:
            self._pl(
                "SYSTEM",
                "balance",
                f"\U0001f4b0 BALANCE: varsayilan {INITIAL_CAPITAL:.2f} USDT (API key yok)",
            )

        self._live = True

        # Leverage: her sembol için config'deki değeri set et
        if cfg.BINANCE_API_KEY:
            async with asyncio.TaskGroup() as tg:
                lev_tasks = {
                    sym: tg.create_task(self._set_leverage(sym)) for sym in self.symbols
                }
            for sym in self.symbols:
                r = lev_tasks[sym].result()
                if r.is_err:
                    log.warning("[LEVERAGE] %s hatasi (devam): %s", sym, r.error)

        await self.recovery_manager.recover_positions()
        reconcile_from_active(self.active_trades)

        # FIX #8: Restart sonrası trades_today senkronizasyonu.
        # ÖNCE disk'teki count'u oku, SONRA ghost recovery sıfırlasın.
        for sym in self.symbols:
            try:
                count = get_trade_count_today(sym)
                if count > 0:
                    self.states[sym].trades_today = count
                    log.info(
                        "[SYNC] %s trades_today disk'ten senkronize edildi: %d",
                        sym,
                        count,
                    )
            except Exception as e:
                log.warning(
                    "[SYNC] %s trades_today disk okuma hatasi (devam ediliyor, count=0 varsayilacak): %s",
                    sym,
                    e,
                )

        # FIX #3: Ghost pozisyon temizliği — trade_state.json'da "open": true
        # olup Binance'de kapalı olan pozisyonları temizle.
        # FIX #8'den SONRA çalışmalı (trades_today sıfırlaması FIX #8'i ezmesin).
        await self.recovery_manager.reconcile_ghost_positions()

        # FIX #10: Retrade state'ini diskten geri yükle (restart-proof).
        for sym in self.symbols:
            try:
                ra = load_retrade_arm(sym)
                if ra:
                    self.states[sym].retrade_armed = True
                    self.states[sym].retrade_fvg_attempts = 0
                    self.states[sym].retrade_mode = "fvg"
                    self.states[sym].retrade_side = ra["side"]
                    self.states[sym].retrade_entry_bar = ra["entry_bar"]
                    log.info(
                        "[RETRADE] %s diskten restore: side=%s bar=%d",
                        sym,
                        ra["side"],
                        ra["entry_bar"],
                    )
            except Exception as e:
                log.warning(
                    "[RETRADE] %s diskten restore hatasi (devam ediliyor, retrade state sifirlandi): %s",
                    sym,
                    e,
                )

        # User Data Stream (WS Zirhi — REST polling yok)
        if cfg.BINANCE_API_KEY:
            try:
                listen_key = await self.rest.get_listen_key()
                if listen_key:
                    self.hub.set_user_data_listen_key(listen_key)
                    # Faz 6.3: UserDataHandler DI ile callback'leri kur
                    udh = UserDataHandler(
                        active_trades=self.active_trades,
                        pl_callback=self._pl,
                        wallet_callback=lambda v: setattr(self, "_wallet_balance", v),
                        order_manager=self.order_manager,
                        exit_callback=self._exit_trade,
                    )
                    udh.register(self.hub)
                    asyncio.create_task(self.hub._listen_key_refresh_loop(self.rest))
                    log.info("[USER_DATA] Listen key aktif: %s...", listen_key[:10])
            except Exception as e:
                log.warning(
                    "[USER_DATA] Listen key alinamadi (devam ediliyor, WS kullanici verisi devre disi): %s",
                    e,
                )

        # Gecmis barlari yukle (15m + 1m)
        async with asyncio.TaskGroup() as tg:
            prefill_tasks = []
            for sym in self.symbols:
                prefill_tasks.append(tg.create_task(self._prefill_bars(sym, "15m")))
                prefill_tasks.append(tg.create_task(self._prefill_bars(sym, "1m")))
        for t in prefill_tasks:
            r = t.result()
            if r.is_err:
                log.warning("[PREFILL] bar yukleme hatasi (devam): %s", r.error)

        for sym in self.symbols:
            self._warmup_cbdr(sym)

        for sym in self.symbols:
            bars = self.hub.get_bars(sym, "15m")
            if bars and len(bars) >= 10:
                await self.on_15m(sym, bars)
                log.info("[INIT] %s ilk analiz tamam (%d bar)", sym, len(bars))

        log.info("Gecmis barlar yuklendi, WS baslatiliyor...")
        try:
            await self.hub.run()
        finally:
            await self.rest.close()
            _close_ohlc_writers()


def main():
    """Bot giriş noktası."""
    bot = PaperTrader(sys.argv[1:] if len(sys.argv) > 1 else None)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Kullanici tarafindan durduruldu.")
        bot.hub.stop()
        _close_ohlc_writers()


if __name__ == "__main__":
    main()
