"""
bot_positions.py — NEXUS V4
─────────────────────────────
Pozisyon yaşam döngüsü:
  startup_cleanup, load_existing_positions,
  safe_sync / sync, repair_protection, create_protection,
  update_sl_order, manage_open_trades, state persistence,
  balance sync, buffer prefill, risk manager factory.

Orijinal konum: sonnet/src/main.py satır 678–1863
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
from typing import TYPE_CHECKING

import config
import performance
from models import Bar
from risk_manager import RiskManager
from state_machine import SetupState, StateMachine

if TYPE_CHECKING:
    from analyzer import MarketAnalyzer
    from bot_binance import BinanceRESTClient
    from bot_infra import TradeEntry
    from trader import LiveExecutor
    from websocket import BinanceWSHub

log = logging.getLogger("nexus.live")


class PositionManager:
    """
    Tüm pozisyon yönetimi sorumluluklarını taşır.

    Bağımlılıklar constructor'a inject edilir — test'te mock kullanılabilir.
    active_trades mutable dict referansı bot.py ile paylaşılır.
    """

    STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "nexus_state.json")

    def __init__(
        self,
        rest: BinanceRESTClient,
        executor: LiveExecutor,
        state_machine: StateMachine,
        analyzers: dict[str, MarketAnalyzer],
        active_trades: dict[str, TradeEntry],
        risk_managers: dict[str, RiskManager],
        hub: BinanceWSHub,
        symbols: list[str],
        http_client: object,  # BinanceHTTPClient — prefill için
    ) -> None:
        self.rest = rest
        self.executor = executor
        self.state_machine = state_machine
        self.analyzers = analyzers
        self.active_trades = active_trades
        self.risk_managers = risk_managers
        self.hub = hub
        self.symbols = symbols
        self.http_client = http_client

        # İç state
        self._balance: float = 0.0
        self._wallet_balance: float = 0.0
        self._unrealized_pnl: float = 0.0
        self._margin_balance: float = 0.0
        self._available_balance: float = 0.0
        self._used_margin: float = 0.0
        self._last_pos_sync_time: float = 0.0
        self._last_protection_check: dict[str, float] = {}
        self._breakeven_log: dict[str, dict] = {}
        self._last_be_summary: float = 0.0

    # ─────────────────────────────────────────────────────────────────
    # Bakiye
    # ─────────────────────────────────────────────────────────────────

    async def sync_balance(self) -> None:
        try:
            acc = await self.rest.get("/fapi/v2/account")
            self._wallet_balance = float(acc.get("totalWalletBalance", 0))
            self._unrealized_pnl = float(acc.get("totalUnrealizedProfit", 0))
            self._margin_balance = float(acc.get("totalMarginBalance", 0))
            self._available_balance = float(acc.get("availableBalance", 0))
            self._used_margin = float(acc.get("totalInitialMargin", 0))
            self._balance = self._available_balance

            for rm in self.risk_managers.values():
                rm.balance = self._balance
                rm.available_margin = self._available_balance

            log.info(
                "Bakiye — wallet=%.2f margin=%.2f uPnL=%.2f available=%.2f used_margin=%.2f",
                self._wallet_balance,
                self._margin_balance,
                self._unrealized_pnl,
                self._available_balance,
                self._used_margin,
            )
        except Exception as e:
            log.error("Bakiye alınamadı: %s", e)

    # ─────────────────────────────────────────────────────────────────
    # Buffer prefill
    # ─────────────────────────────────────────────────────────────────

    async def prefill_buffers(self) -> None:
        from bot_infra import _get_tick_size, rate_limiter  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        for sym in self.symbols:
            await loop.run_in_executor(None, lambda s=sym: _get_tick_size(s))

        prefill_sem = asyncio.Semaphore(3)

        async def _prefill_one(s: str, t: str, limit: int) -> None:
            async with prefill_sem:
                try:
                    await rate_limiter.acquire()
                    ohlcv = await loop.run_in_executor(
                        None,
                        lambda: self.http_client.get_klines(s, interval=t, limit=limit, max_retries=2),
                    )
                    bars = [
                        Bar(
                            index=i,
                            open=k[1],
                            high=k[2],
                            low=k[3],
                            close=k[4],
                            volume=k[5],
                            timestamp=k[0],
                        )
                        for i, k in enumerate(ohlcv)
                    ]
                    buf = self.hub._get_buffer(s, t)
                    buf._bars = bars
                    log.info("[PREFILL] %s %s %d bar yüklendi", s, t, len(bars))
                except Exception as e:
                    log.error("[PREFILL] %s %s hata: %s", s, t, e)
                finally:
                    await asyncio.sleep(0.2)

        prefill_tasks = [
            _prefill_one(sym, tf, limit)
            for tf, limit in [
                ("4h", 210),
                ("1h", config.H1_BARS),
                ("15m", config.M15_BARS),
                ("1m", config.M1_BARS),
            ]
            for sym in self.symbols
        ]
        results = await asyncio.gather(*prefill_tasks, return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            log.warning("[PREFILL] %d sembol/timeframe yüklenemedi", len(errors))
        else:
            log.info("[PREFILL] Tüm buffer'lar başarıyla yüklendi")

    # ─────────────────────────────────────────────────────────────────
    # State persistence
    # ─────────────────────────────────────────────────────────────────

    def flush_state(self) -> None:
        """active_trades + symbol_states → nexus_state.json yaz."""
        try:
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            symbol_states = {}
            for sym in self.symbols:
                st = self.state_machine.get(sym)
                if st and st.state and st.state.value != "IDLE":
                    symbol_states[sym] = {
                        "setup_id": f"{sym}_{st.created_at}_{st.direction}",
                        "state": st.state.value,
                        "direction": st.direction,
                        "fvg_upper": st.fvg_upper,
                        "fvg_lower": st.fvg_lower,
                        "fvg_time": st.fvg_time,
                        "sweep_level": st.sweep_level,
                        "mss_break_level": st.mss_level,
                        "created_at": st.created_at,
                        "expires_at": st.expires_at,
                        "htf_bias": st.htf_bias,
                        "h4_swing_level": st.h4_swing_level,
                        "h1_liquidity_level": st.h1_liquidity_level,
                        "entry_price": st.entry_price,
                        "fvg_missed": st.fvg_missed,
                        "displacement_origin": st.displacement_origin,
                        "poi_anchor": st.poi_anchor,
                    }
            data = {
                "active_trades": self.active_trades,
                "symbol_states": symbol_states,
            }
            with open(self.STATE_FILE, "w", encoding="utf-8-sig") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.debug("[STATE] Flush: %d trade, %d state", len(self.active_trades), len(symbol_states))
        except Exception as e:
            log.error("[STATE] flush_state hatası: %s", e)

    def load_state(self) -> None:
        """nexus_state.json → active_trades + symbol_states yükle (startup)."""
        if not os.path.exists(self.STATE_FILE):
            log.info("[STATE] nexus_state.json yok, temiz başlangıç")
            return
        try:
            with open(self.STATE_FILE, encoding="utf-8-sig") as f:
                data = json.load(f)
            trades = data.get("active_trades", {})
            if trades:
                self.active_trades.update(trades)
                log.info("[STATE] %d trade geri yüklendi", len(trades))
            states = data.get("symbol_states", {})
            restored = 0
            for sym, s in states.items():
                try:
                    st = self.state_machine.get(sym)
                    st.state = SetupState(s.get("state", "IDLE"))
                    st.direction = s.get("direction")
                    st.fvg_upper = s.get("fvg_upper")
                    st.fvg_lower = s.get("fvg_lower")
                    st.fvg_time = s.get("fvg_time")
                    st.sweep_level = s.get("sweep_level")
                    st.mss_level = s.get("mss_break_level")
                    st.created_at = s.get("created_at", int(time.time()))
                    st.expires_at = s.get("expires_at")
                    st.htf_bias = s.get("htf_bias")
                    st.h4_swing_level = s.get("h4_swing_level")
                    st.h1_liquidity_level = s.get("h1_liquidity_level")
                    st.entry_price = s.get("entry_price")
                    st.fvg_missed = s.get("fvg_missed", False)
                    st.displacement_origin = s.get("displacement_origin")
                    st.poi_anchor = s.get("poi_anchor")
                    restored += 1
                except Exception as e:
                    log.warning("[STATE] %s state yüklenemedi: %s", sym, e)
            if restored:
                log.info("[STATE] %d symbol state geri yüklendi", restored)
        except Exception as e:
            log.error("[STATE] load_state hatası: %s", e)

    def clear_state(self, symbol: str) -> None:
        """Trade kapanınca sembolü state'ten sil ve flush et."""
        removed = self.active_trades.pop(symbol, None)
        self.state_machine.clear(symbol)
        if removed is not None and symbol in self.analyzers:
            self.analyzers[symbol].reset_symbol_cache()
        self.flush_state()

    # ─────────────────────────────────────────────────────────────────
    # Risk manager factory
    # ─────────────────────────────────────────────────────────────────

    def get_risk_manager(self, symbol: str) -> RiskManager:
        if symbol not in self.risk_managers:
            self.risk_managers[symbol] = RiskManager(
                balance=self._balance,
                available_margin=self._available_balance,
                risk_pct=config.RISK_PER_TRADE_MAP.get(symbol, config.RISK_PER_TRADE),
                min_rr=config.MIN_RR_MAP.get(symbol, config.MIN_RR),
                min_net_rr=config.MIN_NET_RR,
                default_rr=config.DEFAULT_RR,
                taker_fee=config.TAKER_FEE,
                spread_pct=config.SPREAD_PCT,
            )
        return self.risk_managers[symbol]

    # ─────────────────────────────────────────────────────────────────
    # Startup cleanup
    # ─────────────────────────────────────────────────────────────────

    async def startup_cleanup(self) -> None:
        """🧹 SORGUSUZ İNFAZ PROTOKOLÜ — yetim/duplicate emir temizliği."""
        log.info("🧹 STARTUP CLEANUP | tüm açık emirler taranıyor...")
        try:
            loop = asyncio.get_running_loop()
            positions_raw = await loop.run_in_executor(None, lambda: self.http_client.get_positions())
            positions_list = positions_raw if isinstance(positions_raw, list) else []

            if not positions_list:
                log.warning("🧹 CLEANUP | positions_list BOŞ — hiçbir emir silinmeyecek")
                return

            symbols_with_position: set[str] = set()
            for p in positions_list:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    symbols_with_position.add(p["symbol"])

            missing_symbols = [s for s in self.active_trades if s not in symbols_with_position]
            if missing_symbols:
                log.warning(
                    "🧹 CLEANUP | %d sembol API'de eksik → 1sn bekleyip tekrar: %s",
                    len(missing_symbols),
                    missing_symbols,
                )
                await asyncio.sleep(1)
                retry_pos = await loop.run_in_executor(None, lambda: self.http_client.get_positions())
                retry_list = retry_pos if isinstance(retry_pos, list) else []
                for p in retry_list:
                    p_amt = float(p.get("positionAmt", 0))
                    if p_amt != 0:
                        symbols_with_position.add(p["symbol"])

            if not symbols_with_position and self.active_trades:
                log.warning(
                    "🧹 CLEANUP | API'de pozisyon yok ama local'de %d trade var — ATLANIYOR", len(self.active_trades)
                )
                return
            if not self.active_trades and symbols_with_position:
                log.warning(
                    "🧹 CLEANUP | Local state boş ama API'de %d pozisyon var — ATLANIYOR", len(symbols_with_position)
                )
                return
            if not symbols_with_position and not self.active_trades:
                log.warning("🧹 CLEANUP | API'de pozisyon YOK ve local state BOŞ — ATLANIYOR")
                return

            total_cancelled = 0
            all_orders_raw = await self.rest.get("/fapi/v1/openOrders")
            all_orders: list = all_orders_raw if isinstance(all_orders_raw, list) else []
            try:
                algo_raw = await self.rest.get("/fapi/v1/openAlgoOrders")
                algo_orders: list = algo_raw if isinstance(algo_raw, list) else []
                all_orders.extend(algo_orders)
                log.info(
                    "🧹 CLEANUP | %d normal + %d algo = %d toplam emir",
                    len(all_orders) - len(algo_orders),
                    len(algo_orders),
                    len(all_orders),
                )
            except Exception as e:
                log.warning("🧹 CLEANUP | algoOrders alınamadı (devam): %s", e)

            orders_by_symbol: dict = {}
            for o in all_orders:
                sym = o.get("symbol", "")
                if sym not in orders_by_symbol:
                    orders_by_symbol[sym] = []
                orders_by_symbol[sym].append(o)

            all_symbols_to_check = set(self.symbols) | set(orders_by_symbol.keys())
            for symbol in sorted(all_symbols_to_check):
                orders = orders_by_symbol.get(symbol, [])
                if not orders:
                    continue
                try:
                    if symbol not in symbols_with_position:
                        if symbol in self.active_trades:
                            log.warning(
                                "🧹 [ORPHAN-GUARD] %s API'de pozisyon yok ama local'de trade var — ATLANIYOR", symbol
                            )
                            continue
                        log.warning("🧹 [ORPHAN] %s | %d emir var ama POZİSYON YOK → iptal", symbol, len(orders))
                        for o in orders:
                            order_id = o.get("algoId") or o.get("orderId")
                            is_algo = "algoId" in o
                            if order_id:
                                await self.rest.cancel_order(order_id, symbol, reason="orphan", is_algo=is_algo)
                                total_cancelled += 1
                            await asyncio.sleep(0.15)
                    else:
                        sl_orders = [
                            o
                            for o in orders
                            if self.rest.get_order_type(o) in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                            and o.get("reduceOnly") in (True, "true", "True")
                        ]
                        tp_orders = [
                            o
                            for o in orders
                            if self.rest.get_order_type(o) in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                            and o.get("reduceOnly") in (True, "true", "True")
                        ]
                        if len(sl_orders) > 1 or len(tp_orders) > 1:
                            log.critical(
                                "🧹 [SORGUSUZ İNFAZ] %s | SL=%d TP=%d → fazlalıklar temizleniyor",
                                symbol,
                                len(sl_orders),
                                len(tp_orders),
                            )
                            if len(sl_orders) > 1:
                                sl_orders.sort(key=lambda o: self.rest.get_order_timestamp(o), reverse=True)
                                for o in sl_orders[1:]:
                                    order_id = o.get("algoId") or o.get("orderId")
                                    if order_id:
                                        try:
                                            await self.rest.cancel_order(
                                                order_id, symbol, reason="duplicate_sl_startup", is_algo="algoId" in o
                                            )
                                            total_cancelled += 1
                                        except Exception as cancel_err:
                                            log.warning(
                                                "🧹 [INFAZ-SL] %s | orderId=%s iptal BAŞARISIZ: %s",
                                                symbol,
                                                order_id,
                                                cancel_err,
                                            )
                                    await asyncio.sleep(0.15)
                            if len(tp_orders) > 1:
                                tp_orders.sort(key=lambda o: self.rest.get_order_timestamp(o), reverse=True)
                                for o in tp_orders[1:]:
                                    order_id = o.get("algoId") or o.get("orderId")
                                    if order_id:
                                        try:
                                            await self.rest.cancel_order(
                                                order_id, symbol, reason="duplicate_tp_startup", is_algo="algoId" in o
                                            )
                                            total_cancelled += 1
                                        except Exception as cancel_err:
                                            log.warning(
                                                "🧹 [INFAZ-TP] %s | orderId=%s iptal BAŞARISIZ: %s",
                                                symbol,
                                                order_id,
                                                cancel_err,
                                            )
                                    await asyncio.sleep(0.15)
                except Exception as e:
                    log.warning("🧹 CLEANUP | %s taranırken hata: %s", symbol, e)
                    continue

            if total_cancelled:
                log.warning("🧹 STARTUP CLEANUP | TOPLAM %d EMİR İPTAL EDİLDİ", total_cancelled)
            else:
                log.info("🧹 STARTUP CLEANUP | temiz, iptal gereken emir yok")
        except Exception as e:
            log.error("🧹 STARTUP CLEANUP hatası: %s", e)

    # ─────────────────────────────────────────────────────────────────
    # Mevcut pozisyonları yükle
    # ─────────────────────────────────────────────────────────────────

    async def load_existing_positions(self) -> None:
        """Cleanup sonrası kalan pozisyonları API'den okuyup envantere al."""
        try:
            log.info("🔄 RESTART | pozisyonlar yükleniyor (API)...")
            loop = asyncio.get_running_loop()
            positions_raw = await loop.run_in_executor(None, lambda: self.http_client.get_positions())
            positions = positions_raw if isinstance(positions_raw, list) else []

            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt == 0:
                    continue

                symbol = pos["symbol"]
                direction = "long" if amt > 0 else "short"
                entry = float(pos.get("entryPrice", 0))
                pnl = float(pos.get("unRealizedProfit", 0))
                mark_price = float(pos.get("markPrice", 0))

                open_orders: list = []
                for attempt in range(3):
                    open_orders = await self.rest.get_open_orders(symbol)
                    try:
                        algo_raw = await self.rest.get("/fapi/v1/openAlgoOrders", f"symbol={symbol}")
                        if isinstance(algo_raw, list):
                            open_orders.extend(algo_raw)
                    except Exception as e:
                        log.debug("[RECOVER] %s openAlgoOrders hatası: %s", symbol, e)
                    if open_orders:
                        break
                    if attempt < 2:
                        log.warning("[RECOVER] %s openOrders BOŞ (attempt %d/3) — 1.5s", symbol, attempt + 1)
                await asyncio.sleep(1.5)

                sl_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o) in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                    and o.get("reduceOnly") in (True, "true", "True")
                ]
                tp_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o) in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                    and o.get("reduceOnly") in (True, "true", "True")
                ]
                n_sl = len(sl_orders)
                n_tp = len(tp_orders)
                log.info("[RECOVER] %s pozisyon=%s giriş=%.4f SL=%d TP=%d", symbol, direction, entry, n_sl, n_tp)

                if n_sl == 1 and n_tp == 1:
                    sl_price = self.rest.get_order_price(sl_orders[0])
                    tp_price = self.rest.get_order_price(tp_orders[0])
                    sl_id = sl_orders[0].get("algoId") or sl_orders[0].get("orderId") or ""
                    tp_id = tp_orders[0].get("algoId") or tp_orders[0].get("orderId") or ""
                    self.active_trades[symbol] = {
                        "symbol": symbol,
                        "direction": direction,
                        "entry": entry,
                        "initial_sl": sl_price,
                        "current_sl": sl_price,
                        "tp": tp_price,
                        "sl_order_id": sl_id,
                        "tp_order_id": tp_id,
                        "lot": abs(amt),
                        "open_time": None,
                        "status": "open",
                        "pnl": pnl,
                        "last_price": mark_price,
                        "breakeven_done": False,
                    }
                    log.info("[RECOVER] %s ✓ SL+TP mevcut", symbol)
                else:
                    log.warning("🚨 [RECOVER] %s KORUMASIZ SL=%d TP=%d → SAFE MODE", symbol, n_sl, n_tp)
                    self.active_trades[symbol] = {
                        "symbol": symbol,
                        "direction": direction,
                        "entry": entry,
                        "lot": abs(amt),
                        "status": "recovered_unprotected",
                        "protection_missing": True,
                        "pnl": pnl,
                        "last_price": mark_price,
                    }

            if self.active_trades:
                log.info("[RECOVER] %d pozisyon envantere alındı", len(self.active_trades))
            else:
                log.info("[RECOVER] Envantere alınan açık pozisyon yok")
        except Exception as e:
            log.error("Pozisyon yükleme hatası: %s", e)

    # ─────────────────────────────────────────────────────────────────
    # Pozisyon senkronizasyonu
    # ─────────────────────────────────────────────────────────────────

    async def safe_sync(self, current_bar: Bar) -> None:
        """Fire-and-forget wrapper."""
        try:
            await self.sync(current_bar)
        except Exception as e:
            log.error("[SYNC] sync hatası (yakalandı): %s", str(e), exc_info=True)

    async def sync(self, current_bar: Bar) -> None:
        """Her döngüde çağrılır. TEK GERÇEKLİK: Binance API."""
        now = time.time()
        if now - self._last_pos_sync_time < 5.0:
            return
        self._last_pos_sync_time = now
        try:
            loop = asyncio.get_running_loop()
            positions_raw = await loop.run_in_executor(None, lambda: self.http_client.get_positions())
            positions = positions_raw if isinstance(positions_raw, list) else []
            log.info("[SYNC-POSITIONS] %d pozisyon çekildi", len(positions))

            if not positions:
                log.warning("[SYNC-POSITIONS] pozisyon listesi boş — trade'ler korunuyor")
                return

            exchange_positions = {pos["symbol"]: pos for pos in positions if float(pos.get("positionAmt", 0)) != 0}
            total_upnl = 0.0

            for symbol, trade in list(self.active_trades.items()):
                if symbol not in exchange_positions:
                    continue
                pos = exchange_positions[symbol]
                trade["pnl"] = float(pos.get("unRealizedProfit", 0))
                trade["last_price"] = float(pos.get("markPrice", 0))
                total_upnl += trade["pnl"]

                open_orders = await self.rest.get_all_orders(symbol)
                sl_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o) in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                    and o.get("reduceOnly") in (True, "true", "True")
                ]
                tp_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o) in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                    and o.get("reduceOnly") in (True, "true", "True")
                ]
                n_sl = len(sl_orders)
                n_tp = len(tp_orders)

                if n_sl > 1 or n_tp > 1:
                    log.critical("🚨 [SORGUSUZ İNFAZ] %s | SL=%d TP=%d → fazlalıklar temizleniyor", symbol, n_sl, n_tp)
                    if n_sl > 1:
                        sl_orders.sort(key=lambda o: self.rest.get_order_timestamp(o), reverse=True)
                        for o in sl_orders[1:]:
                            order_id = o.get("algoId") or o.get("orderId")
                            if order_id:
                                try:
                                    await self.rest.cancel_order(
                                        order_id, symbol, reason="duplicate_sl_extra", is_algo="algoId" in o
                                    )
                                except Exception as cancel_err:
                                    log.warning(
                                        "🛡️ [INFAZ-SL] %s | orderId=%s iptal BAŞARISIZ: %s", symbol, order_id, cancel_err
                                    )
                            await asyncio.sleep(0.1)
                        trade["sl_order_id"] = str(sl_orders[0].get("algoId") or sl_orders[0].get("orderId") or "")
                        trade["current_sl"] = self.rest.get_order_price(sl_orders[0]) or trade.get("current_sl", 0)
                    if n_tp > 1:
                        tp_orders.sort(key=lambda o: self.rest.get_order_timestamp(o), reverse=True)
                        for o in tp_orders[1:]:
                            order_id = o.get("algoId") or o.get("orderId")
                            if order_id:
                                try:
                                    await self.rest.cancel_order(
                                        order_id, symbol, reason="duplicate_tp_extra", is_algo="algoId" in o
                                    )
                                except Exception as cancel_err:
                                    log.warning(
                                        "🛡️ [INFAZ-TP] %s | orderId=%s iptal BAŞARISIZ: %s", symbol, order_id, cancel_err
                                    )
                            await asyncio.sleep(0.1)
                        trade["tp_order_id"] = str(tp_orders[0].get("algoId") or tp_orders[0].get("orderId") or "")
                        trade["tp"] = self.rest.get_order_price(tp_orders[0]) or trade.get("tp", 0)

                    n_sl_now = 1 if n_sl >= 1 else 0
                    n_tp_now = 1 if n_tp >= 1 else 0
                    if n_sl_now == 0 or n_tp_now == 0:
                        trade["protection_repairing"] = True
                        try:
                            await self.repair_protection(symbol, trade, n_sl_now > 0, n_tp_now > 0)
                        except Exception as e:
                            log.critical("🚨 [SYNC] %s infaz sonrası onarım hatası: %s", symbol, e)
                        finally:
                            trade["protection_repairing"] = False
                    else:
                        trade["protection_missing"] = False
                        trade["status"] = "open"

                elif n_sl == 1 and n_tp == 1:
                    trade["sl_order_id"] = str(sl_orders[0].get("algoId") or sl_orders[0].get("orderId") or "")
                    trade["tp_order_id"] = str(tp_orders[0].get("algoId") or tp_orders[0].get("orderId") or "")
                    trade["current_sl"] = self.rest.get_order_price(sl_orders[0]) or trade.get("current_sl", 0)
                    trade["tp"] = self.rest.get_order_price(tp_orders[0]) or trade.get("tp", 0)
                    if trade.get("protection_missing"):
                        trade["protection_missing"] = False
                        trade["status"] = "open"
                        log.info("✅ [REPAIR] %s koruma API'den doğrulandı", symbol)

                else:
                    now_t = time.time()
                    last_check = self._last_protection_check.get(symbol, 0)
                    if now_t - last_check < 300:
                        continue
                    self._last_protection_check[symbol] = now_t
                    log.warning("⚠️ MISSING PROTECTION | %s | SL=%d TP=%d → Safe Mode", symbol, n_sl, n_tp)
                    trade["protection_repairing"] = True
                    try:
                        if n_sl == 0 and n_tp == 0:
                            await self.create_protection(symbol, trade)
                        else:
                            await self.repair_protection(symbol, trade, n_sl > 0, n_tp > 0)
                    except Exception as e:
                        log.critical("🚨 [SYNC] %s protection/repair KRİTİK HATA: %s", symbol, e)
                    finally:
                        trade["protection_repairing"] = False

            self._unrealized_pnl = total_upnl

            for symbol, trade in list(self.active_trades.items()):
                if symbol not in exchange_positions:
                    symbol_bars = self.hub.get_bars(symbol, "1m")
                    symbol_close = symbol_bars[-1].close if symbol_bars else None
                    fallback_price = trade.get("last_price") or symbol_close or trade.get("entry") or 0
                    exit_price = float(fallback_price)
                    pnl = trade.get("pnl", 0)
                    self._balance += pnl
                    risk_mgr = self.get_risk_manager(symbol)
                    risk_mgr.balance = self._balance

                    direction = trade.get("direction", "long")
                    tp_price = trade.get("tp", 0) or trade.get("tp_val", 0) or 0
                    if tp_price:
                        if direction == "long":
                            close_reason = "TP" if exit_price >= tp_price * 0.995 else "SL"
                        else:
                            close_reason = "TP" if exit_price <= tp_price * 1.005 else "SL"
                    else:
                        close_reason = "closed"

                    trade["exit_price"] = exit_price
                    trade["exit"] = exit_price
                    trade["close_time"] = int(time.time() * 1000)
                    trade["status"] = close_reason

                    trade.setdefault("direction", "unknown")
                    performance.record_trade(trade)
                    if trade.get("protection_missing"):
                        log.warning("🟡 SAFE MODE | %s kapandı | eksik bilgiyle kaydedildi", symbol.ljust(12))

                    try:
                        await self.executor.client.cancel_all_orders(symbol)
                    except Exception as cancel_err:
                        log.warning("[SYNC] %s cancel_all_orders hatası: %s", symbol.ljust(12), cancel_err)

                    self.clear_state(symbol)
                    self.executor.reset_cooldown(symbol)
                    log.info(
                        "EXCHANGE SYNC: %s kapandı | 🔴 CIKIS=%.4f pnl=%.2f USDT", symbol.ljust(12), exit_price, pnl
                    )

        except Exception as e:
            err_msg = str(e)
            if "-1109" not in err_msg:
                log.error("Pozisyon sync hatası: %s", err_msg, exc_info=True)

    # ─────────────────────────────────────────────────────────────────
    # Koruma onarım
    # ─────────────────────────────────────────────────────────────────

    async def repair_protection(self, symbol: str, trade: dict, has_sl: bool, has_tp: bool) -> None:
        """Eksik TP/SL'yi tamamla."""
        try:
            pos = await self.executor.client.fetch_position(symbol)
            if not pos or abs(float(pos.get("contracts", 0))) == 0:
                log.warning("🔧 [REPAIR] %s pozisyon yok, atlanıyor", symbol)
                return

            if not has_tp and trade.get("tp"):
                mark_price = float(pos.get("markPrice", 0))
                direction = trade.get("direction", "long")
                tp_price = trade["tp"]
                if (direction == "long" and mark_price >= tp_price) or (
                    direction == "short" and mark_price <= tp_price
                ):
                    log.critical(
                        "🚘 [SORGUSUZ İNFAZ] %s TP (%.5f) zaten geçildi — MARKET kapatılıyor!", symbol, tp_price
                    )
                    await self.executor.close_position(symbol, reason="tp_already_hit_repair")
                    return

            if not has_sl:
                if not trade.get("initial_sl"):
                    risk_mgr = self.get_risk_manager(symbol)
                    direction = trade.get("direction", "long")
                    entry = trade.get("entry", 0)
                    tier = risk_mgr._tier(symbol)
                    buf = tier["sl_buffer"]
                    min_dist = entry * tier["min_sl_pct"]
                    if direction == "long":
                        sl_candidate = min(entry * (1 - buf), entry - min_dist)
                    else:
                        sl_candidate = max(entry * (1 + buf), entry + min_dist)
                    trade["initial_sl"] = round(sl_candidate, 5)
                    trade["current_sl"] = trade["initial_sl"]

                sl_side = "sell" if trade["direction"] == "long" else "buy"
                sl_result = await self.executor.client.create_stop_order(
                    symbol=symbol,
                    side=sl_side,
                    amount=trade.get("lot"),
                    stop_price=trade.get("initial_sl"),
                    order_type="STOP_MARKET",
                )
                trade["sl_order_id"] = str(
                    (sl_result or {}).get("algoId")
                    or (sl_result or {}).get("orderId")
                    or (sl_result or {}).get("id")
                    or ""
                )
                log.info(
                    "🔧 [REPAIR] %s SL yeniden kuruldu: %.8f (id=%s)", symbol, trade["initial_sl"], trade["sl_order_id"]
                )

            if not has_tp and trade.get("tp"):
                tp_side = "sell" if trade["direction"] == "long" else "buy"
                tp_result = await self.executor.client.create_stop_order(
                    symbol=symbol,
                    side=tp_side,
                    amount=trade.get("lot"),
                    stop_price=trade["tp"],
                    order_type="TAKE_PROFIT_MARKET",
                )
                trade["tp_order_id"] = str(
                    (tp_result or {}).get("algoId")
                    or (tp_result or {}).get("orderId")
                    or (tp_result or {}).get("id")
                    or ""
                )
                log.info("🔧 [REPAIR] %s TP yeniden kuruldu: %.8f (id=%s)", symbol, trade["tp"], trade["tp_order_id"])

            await asyncio.sleep(0.3)
            open_orders = await self.rest.get_all_orders(symbol)
            sl_ok = any(
                self.rest.get_order_type(o) in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                and o.get("reduceOnly") in (True, "true", "True")
                for o in open_orders
            )
            tp_ok = any(
                self.rest.get_order_type(o) in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                and o.get("reduceOnly") in (True, "true", "True")
                for o in open_orders
            )
            if sl_ok and tp_ok:
                trade["protection_missing"] = False
                trade["status"] = "open"
                log.info("✅ [REPAIR] %s koruma API'den doğrulandı", symbol)
            else:
                log.warning("⚠️ [REPAIR] %s doğrulama başarısız SL_ok=%s TP_ok=%s", symbol, sl_ok, tp_ok)
        except urllib.error.HTTPError as e:
            if "-4130" in str(e):
                log.info("[REPAIR] %s zaten aktif koruma emri mevcut", symbol)
                return
            raise
        except Exception:
            log.exception("🔧 REPAIR_PROTECTION FAILED | %s", symbol)

    async def create_protection(self, symbol: str, trade: dict) -> None:
        """Sıfırdan TP/SL oluştur."""
        try:
            pos = await self.executor.client.fetch_position(symbol)
            if not pos or abs(float(pos.get("contracts", 0))) == 0:
                log.warning("🆕 [CREATE] %s pozisyon yok, atlanıyor", symbol)
                return

            risk_mgr = self.get_risk_manager(symbol)
            entry = trade["entry"]
            direction = trade["direction"]
            mark_price = float(pos.get("markPrice", 0)) or trade.get("last_price", entry)
            tier = risk_mgr._tier(symbol)
            buf = tier["sl_buffer"]
            min_dist = entry * tier["min_sl_pct"]

            sl = tp = None
            if direction == "long":
                sl_candidate = min(entry * (1 - buf), entry - min_dist)
                tp_candidate = entry + (entry - sl_candidate) * risk_mgr.default_rr
                if mark_price >= tp_candidate:
                    log.critical("🚘 [SORGUSUZ İNFAZ] %s TP zaten geçildi — MARKET kapatılıyor!", symbol)
                    await self.executor.close_position(symbol, reason="tp_already_hit")
                    return
                if mark_price <= sl_candidate:
                    log.critical("🚨 [CREATE] %s SL zaten geçildi — EMERGENCY kapatılıyor!", symbol)
                    await self.executor.close_position(symbol, reason="sl_already_hit")
                    return
                sl, tp = sl_candidate, tp_candidate
            else:
                sl_candidate = max(entry * (1 + buf), entry + min_dist)
                tp_candidate = entry - (sl_candidate - entry) * risk_mgr.default_rr
                if mark_price <= tp_candidate:
                    log.critical("🚘 [SORGUSUZ İNFAZ] %s TP zaten geçildi — MARKET kapatılıyor!", symbol)
                    await self.executor.close_position(symbol, reason="tp_already_hit")
                    return
                if mark_price >= sl_candidate:
                    log.critical("🚨 [CREATE] %s SL zaten geçildi — EMERGENCY kapatılıyor!", symbol)
                    await self.executor.close_position(symbol, reason="sl_already_hit")
                    return
                sl, tp = sl_candidate, tp_candidate

            sl_side = "sell" if direction == "long" else "buy"
            sl_resp = await self.executor.client.create_stop_order(
                symbol=symbol,
                side=sl_side,
                amount=trade.get("lot"),
                stop_price=round(sl, 5),
                order_type="STOP_MARKET",
            )
            sl_id = str(
                (sl_resp or {}).get("algoId") or (sl_resp or {}).get("orderId") or (sl_resp or {}).get("id") or ""
            )

            tp_id = ""
            if tp is not None:
                try:
                    tp_resp = await self.executor.client.create_stop_order(
                        symbol=symbol,
                        side=sl_side,
                        amount=trade.get("lot"),
                        stop_price=round(tp, 5),
                        order_type="TAKE_PROFIT_MARKET",
                    )
                    tp_id = str(
                        (tp_resp or {}).get("algoId")
                        or (tp_resp or {}).get("orderId")
                        or (tp_resp or {}).get("id")
                        or ""
                    )
                except Exception as tp_e:
                    err_str = str(tp_e)
                    if "-2021" in err_str:
                        log.warning("🟡 [CREATE] %s TP hemen tetiklenirdi — atlanıyor", symbol.ljust(12))
                    elif "-4130" in err_str:
                        log.warning("🟡 [CREATE] %s TP/SL zaten mevcut", symbol.ljust(12))
                    else:
                        raise

            trade.update(
                {
                    "initial_sl": round(sl, 5),
                    "current_sl": round(sl, 5),
                    "tp": round(tp, 5) if tp is not None else 0.0,
                    "sl_order_id": sl_id,
                    "tp_order_id": tp_id,
                    "protection_missing": False,
                    "status": "open",
                }
            )
            log.info(
                "🆕 [CREATE] %s TP/SL kuruldu: SL=%.5f (%s) TP=%s (%s)",
                symbol.ljust(12),
                sl_id,
                f"{tp:.5f}" if tp else "ATLANDI",
                tp_id or "-",
            )
        except Exception as e:
            if "-4130" in str(e):
                log.warning("🟡 [CREATE] %s TP/SL zaten mevcut", symbol.ljust(12))
                trade.setdefault("initial_sl", 0.0)
                trade.setdefault("current_sl", 0.0)
                trade.setdefault("tp", 0.0)
                trade["protection_missing"] = False
                trade["status"] = "open"
            else:
                log.exception("🆘 CREATE_PROTECTION FAILED | %s", symbol)

    # ─────────────────────────────────────────────────────────────────
    # SL güncelleme
    # ─────────────────────────────────────────────────────────────────

    async def update_sl_order(self, symbol: str, trade: dict, new_sl: float) -> None:
        """SL güncelle. API'den mevcut SL emrini bulur, cancelReplace yapar."""
        old_sl = None
        try:
            open_orders = await self.rest.get_all_orders(symbol)
            old_sl = next(
                (o for o in open_orders if self.rest.get_order_type(o) in ("STOP_MARKET", "STOP", "STOP_LIMIT")),
                None,
            )
            if not old_sl:
                sl_side = "sell" if trade["direction"] == "long" else "buy"
                sl_resp = await self.executor.client.create_stop_order(
                    symbol=symbol,
                    side=sl_side,
                    amount=trade.get("lot"),
                    stop_price=new_sl,
                    order_type="STOP_MARKET",
                )
                new_id = str(
                    (sl_resp or {}).get("algoId") or (sl_resp or {}).get("orderId") or (sl_resp or {}).get("id") or ""
                )
                trade["sl_order_id"] = new_id
                log.info("🛡️ SL UPDATE | %s | yeni SL=%.8f (id=%s)", symbol, new_sl, new_id)
                return

            if "algoId" in old_sl:
                old_id = old_sl["algoId"]
                await self.rest.cancel_order(old_id, symbol, reason="sl_update", is_algo=True)
                await asyncio.sleep(0.2)
                sl_side = "sell" if trade["direction"] == "long" else "buy"
                sl_resp = await self.executor.client.create_stop_order(
                    symbol=symbol,
                    side=sl_side,
                    amount=trade.get("lot"),
                    stop_price=new_sl,
                    order_type="STOP_MARKET",
                )
                new_id = str(
                    (sl_resp or {}).get("algoId") or (sl_resp or {}).get("orderId") or (sl_resp or {}).get("id") or ""
                )
                trade["sl_order_id"] = new_id
                trade["current_sl"] = new_sl
                log.info("🛡️ SL ALGO UPDATE | %s | yeni SL=%.8f (id=%s)", symbol, new_sl, new_id)
                return

            result = await self.rest.post(
                "/fapi/v1/order/cancelReplace",
                {
                    "symbol": symbol,
                    "cancelReplaceMode": "STOP_ON_FAILURE",
                    "cancelOrderId": old_sl["orderId"],
                    "side": "SELL" if trade["direction"] == "long" else "BUY",
                    "type": "STOP_MARKET",
                    "stopPrice": new_sl,
                    "quantity": str(abs(trade["lot"])),
                    "reduceOnly": True,
                },
            )
            new_id = str(result.get("algoId") or result.get("orderId") or result.get("id") or "")
            if new_id:
                trade["sl_order_id"] = new_id
            log.info(
                "🛡️ SL REPLACED | %s | %.8f → %.8f (new_id=%s)",
                symbol,
                float(old_sl.get("stopPrice", 0)),
                new_sl,
                new_id,
            )

        except Exception as e:
            log.critical("[SL_UPDATE] %s cancelReplace başarısız: %s — EMERGENCY FALLBACK", symbol, e)
            try:
                old_id = None
                if old_sl:
                    old_id = old_sl.get("algoId") or old_sl.get("orderId")
                if old_id:
                    await self.rest.cancel_order(
                        old_id, symbol, reason="sl_update_fallback_cancel", is_algo="algoId" in old_sl
                    )
                await asyncio.sleep(0.2)
                sl_side = "sell" if trade["direction"] == "long" else "buy"
                sl_resp = await self.executor.client.create_stop_order(
                    symbol=symbol,
                    side=sl_side,
                    amount=trade.get("lot"),
                    stop_price=new_sl,
                    order_type="STOP_MARKET",
                )
                new_id = str(
                    (sl_resp or {}).get("algoId") or (sl_resp or {}).get("orderId") or (sl_resp or {}).get("id") or ""
                )
                trade["sl_order_id"] = new_id
                trade["current_sl"] = new_sl
                log.info("🛡️ SL FALLBACK OK | %s | yeni SL=%.8f (id=%s)", symbol, new_sl, new_id)
            except Exception as fallback_err:
                log.critical("🚨 SL FALLBACK BAŞARISIZ | %s | EMERGENCY CLOSE: %s", symbol, fallback_err)
                try:
                    await self.executor.close_position(symbol, reason="emergency_sl_update_fail")
                    log.critical("🚨 EMERGENCY CLOSE BAŞARILI | %s", symbol)
                except Exception as close_err:
                    log.critical("🚨 EMERGENCY CLOSE BAŞARISIZ | %s | manuel müdahale! hata=%s", symbol, close_err)

    # ─────────────────────────────────────────────────────────────────
    # Trade yönetimi (breakeven + trailing)
    # ─────────────────────────────────────────────────────────────────

    async def safe_manage_open_trades(self, current_bar: Bar) -> None:
        """Fire-and-forget wrapper."""
        try:
            await self.manage_open_trades(current_bar)
        except Exception as e:
            log.critical("[MANAGE-SAFE] manage_open_trades hatası: %s", str(e), exc_info=True)

    async def manage_open_trades(self, current_bar: Bar) -> None:
        current_time_ms = int(time.time() * 1000)
        for symbol, trade in list(self.active_trades.items()):
            try:
                pos = await self.executor.get_position(symbol)
                if not pos or abs(float(pos.get("contracts", 0))) == 0:
                    log.warning("[MANAGE-RACE] %s pozisyon API'de bulunamadı — ATLANIYOR", symbol)
                    continue
            except Exception as e:
                log.warning("[MANAGE-RACE] %s pozisyon sorgusu başarısız: %s", symbol, e)
                continue

            if trade.get("protection_missing"):
                log.warning("🟡 SAFE MODE | %s | sadece izleme", symbol.ljust(12))
                continue
            if trade.get("protection_repairing"):
                log.warning("🟡 REPAIR MODE | %s | sadece izleme", symbol.ljust(12))
                continue
            if trade["status"] != "open":
                continue

            open_time = trade.get("open_time") or 0
            if open_time and (current_time_ms - open_time) < 300_000:
                remaining = int((300_000 - (current_time_ms - open_time)) / 1000)
                log.info("[MANAGE] %s henüz taze (kalan: %dsn) — Breakeven/Trailing atlandı", symbol, remaining)
                continue

            try:
                risk_mgr = self.get_risk_manager(symbol)
                symbol_bars = self.hub.get_bars(symbol, "1m")
                symbol_close = symbol_bars[-1].close if symbol_bars else None
                current_price = trade.get("last_price") or symbol_close or trade.get("entry", 0)
                sl_current = trade.get("current_sl", trade["initial_sl"])

                if not trade.get("breakeven_done", False) and risk_mgr.should_move_to_breakeven(trade, current_price):
                    new_sl = risk_mgr.breakeven_sl(trade)
                    trade["current_sl"] = new_sl
                    trade["breakeven_done"] = True

                    if config.BREAKEVEN_LOG_ENABLED:
                        d1_adx = trade.get("d1_adx_at_entry", 0)
                        adx_flag = "⚠️ ADX>35" if d1_adx >= config.ADX_HIGH_TP_THRESHOLD else "OK"
                        log.info(
                            "[BE] %s breakeven | yeni SL=%.8f | d1_adx=%.1f (%s)", symbol, new_sl, d1_adx, adx_flag
                        )
                    else:
                        log.info("[BE] %s breakeven, yeni SL=%.8f", symbol, new_sl)

                    if symbol not in self._breakeven_log:
                        self._breakeven_log[symbol] = {"count": 0, "adx_gt_35": 0, "last_time": current_time_ms}
                    self._breakeven_log[symbol]["count"] += 1
                    self._breakeven_log[symbol]["last_time"] = current_time_ms
                    d1_adx = trade.get("d1_adx_at_entry", 0)
                    if d1_adx >= config.ADX_HIGH_TP_THRESHOLD:
                        self._breakeven_log[symbol]["adx_gt_35"] += 1

                    if config.BREAKEVEN_LOG_ENABLED and current_time_ms - self._last_be_summary > 1_800_000:
                        self._last_be_summary = current_time_ms
                        total_be = sum(v["count"] for v in self._breakeven_log.values())
                        total_adx35 = sum(v["adx_gt_35"] for v in self._breakeven_log.values())
                        corr_pct = (total_adx35 / total_be * 100) if total_be > 0 else 0.0
                        log.info("[BE-SUMMARY] toplam=%d | ADX>35'te=%d (%.1f%%)", total_be, total_adx35, corr_pct)

                    await self.update_sl_order(symbol, trade, new_sl)

                elif trade.get("breakeven_done", False):
                    new_sl = risk_mgr.trailing_sl(
                        trade, current_price, sl_current, step_ratio=config.TRAILING_STEP_RATIO
                    )
                    if new_sl != sl_current:
                        trade["current_sl"] = new_sl
                        log.info("[TRAIL] %s SL: %.8f → %.8f", symbol, sl_current, new_sl)
                        await self.update_sl_order(symbol, trade, new_sl)

            except Exception as e:
                log.error("[MANAGE] %s yönetim hatası: %s", symbol, e)

    # ─────────────────────────────────────────────────────────────────
    # Property proxies — bot.py API server için
    # ─────────────────────────────────────────────────────────────────

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def wallet_balance(self) -> float:
        return self._wallet_balance

    @property
    def unrealized_pnl(self) -> float:
        return self._unrealized_pnl

    @property
    def margin_balance(self) -> float:
        return self._margin_balance

    @property
    def available_balance(self) -> float:
        return self._available_balance

    @property
    def used_margin(self) -> float:
        return self._used_margin

    @property
    def breakeven_log(self) -> dict:
        return self._breakeven_log
