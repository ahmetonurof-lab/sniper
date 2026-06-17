"""
bot.py — NEXUS V4 Orchestrator
───────────────────────────────
LiveTradingBot: 4 bileşeni init eden ve birbirine bağlayan yapıştırıcı.
İş mantığı YOK — init + run + API server + callback kaydı.

Orijinal: sonnet/src/main.py → LiveTradingBot.__init__ + run + _start_api_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse

import config
import monitor
import performance
from analyzer import MarketAnalyzer
from bot_binance import BinanceRESTClient
from bot_infra import (
    DailyDataCache,
    TradeEntry,
    _close_ohlc_writers,
    export_ohlc_1m,
    rate_limiter,
)
from bot_pipeline import TradingPipeline
from bot_positions import PositionManager
from dotenv import load_dotenv
from event_router import EventRouter
from exchange import BinanceHTTPClient
from models import Bar
from risk_manager import RiskManager
from state_machine import StateMachine
from trader import ExchangeClient, LiveExecutor
from websocket import BinanceWSHub

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
os.makedirs("output/trading", exist_ok=True)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.TimedRotatingFileHandler(
            filename="output/trading/live_trading.log",
            when="midnight",
            backupCount=10,
            encoding="utf-8-sig",
        ),
    ],
)
log = logging.getLogger("nexus.live")

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# .env + HTTP Client
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()
API_KEY = os.getenv("TESTNET_API_KEY")
API_SECRET = os.getenv("TESTNET_API_SECRET")
TESTNET = os.getenv("TESTNET", "True").lower() == "true"
BASE_URL = os.getenv("BASE_URL", "https://demo-fapi.binance.com") if TESTNET else "https://fapi.binance.com"
WS_BASE_URL = (
    os.getenv("TESTNET_WS_URL", "wss://fstream.binancefuture.com/stream?streams=")
    if TESTNET
    else "wss://fstream.binance.com/stream?streams="
)

if TESTNET:
    log.info("Futures DEMO modu → %s", BASE_URL)
else:
    log.warning("⚠️  CANLI FUTURES MODU — DİKKAT!")

http_client = BinanceHTTPClient(
    api_key=API_KEY,
    api_secret=API_SECRET,
    base_url=BASE_URL,
    timeout=30,
    portfolio_margin=False,
)
log.info("BinanceHTTPClient oluşturuldu → %s", BASE_URL)


# ─────────────────────────────────────────────────────────────────────────────
# LiveTradingBot
# ─────────────────────────────────────────────────────────────────────────────


class LiveTradingBot:
    """
    Orchestrator — bileşenleri init eder ve birbirine bağlar.
    İş mantığı PositionManager ve TradingPipeline'da yaşar.
    """

    def __init__(self) -> None:
        # ── WebSocket hub ──
        self.hub = BinanceWSHub(
            symbols=config.SYMBOLS,
            timeframes=["1m", "15m", "1h", "4h"],
            max_bars=500,
            base_url=WS_BASE_URL,
        )

        # ── Infra ──
        self.daily_cache = DailyDataCache()
        self._api_semaphore = asyncio.Semaphore(5)
        self._rate_limiter = rate_limiter
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # ── Binance REST katmanı ──
        self.rest = BinanceRESTClient(
            api_key=API_KEY,
            api_secret=API_SECRET,
            base_url=BASE_URL,
            rate_limiter=self._rate_limiter,
            semaphore=self._api_semaphore,
        )

        # ── Exchange + Executor ──
        self.exchange_client = ExchangeClient(http_client)
        self.executor = LiveExecutor(self.exchange_client)

        # ── Trading logic ──
        self.active_trades: dict[str, TradeEntry] = {}
        self.state_machine = StateMachine()
        self.event_router = EventRouter(self.state_machine)
        self.analyzers = {sym: MarketAnalyzer(sym) for sym in config.SYMBOLS}
        self.risk_managers: dict[str, RiskManager] = {}

        # ── PositionManager (injection) ──
        self.positions = PositionManager(
            rest=self.rest,
            executor=self.executor,
            state_machine=self.state_machine,
            analyzers=self.analyzers,
            active_trades=self.active_trades,  # mutable ref
            risk_managers=self.risk_managers,
            hub=self.hub,
            symbols=config.SYMBOLS,
            http_client=http_client,
        )

        # ── TradingPipeline (injection) ──
        self.pipeline = TradingPipeline(
            hub=self.hub,
            state_machine=self.state_machine,
            event_router=self.event_router,
            analyzers=self.analyzers,
            active_trades=self.active_trades,  # aynı ref
            executor=self.executor,
            positions=self.positions,
            daily_cache=self.daily_cache,
        )

    # ─────────────────────────────────────────────────────────────────
    # Bakiye property proxies (API server için)
    # ─────────────────────────────────────────────────────────────────

    @property
    def _balance(self) -> float:
        return self.positions.balance

    @property
    def _wallet_balance(self) -> float:
        return self.positions.wallet_balance

    @property
    def _unrealized_pnl(self) -> float:
        return self.positions.unrealized_pnl

    @property
    def _margin_balance(self) -> float:
        return self.positions.margin_balance

    @property
    def _available_balance(self) -> float:
        return self.positions.available_balance

    @property
    def _used_margin(self) -> float:
        return self.positions.used_margin

    # ─────────────────────────────────────────────────────────────────
    # Ana döngü
    # ─────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        # ADIM 0: Bakiye
        try:
            await self.positions.sync_balance()
        except Exception as e:
            log.critical("⚠️ Bakiye alınamadı: %s — varsayılan 1000 USDT", e)
            self.positions._balance = 1000.0

        # ADIM 0.5: State dosyasından devam
        try:
            self.positions.load_state()
        except Exception as e:
            log.warning("[STATE] load_state hatası — temiz başlangıç: %s", e)

        # ADIM 1: Pozisyonları yükle (cleanup'tan ÖNCE)
        try:
            await self.positions.load_existing_positions()
        except Exception as e:
            log.critical("⚠️ Pozisyon yükleme başarısız: %s", e)

        self.positions.flush_state()

        # ADIM 2: Startup cleanup
        try:
            await self.positions.startup_cleanup()
        except Exception as e:
            log.critical("⚠️ Cleanup başarısız: %s", e)

        self.positions.flush_state()
        self.executor.mark_startup_complete()

        # ADIM 2.5: User Data Stream
        try:
            listen_key = http_client.new_listen_key()
            if listen_key:
                parsed = urlparse(WS_BASE_URL)
                ws_base = f"{parsed.scheme}://{parsed.netloc}"
                self.hub.set_user_data_listen_key(listen_key, ws_base_url=ws_base)
                log.info("[USER_DATA] Listen key oluşturuldu: %s...", listen_key[:10])
                self._register_user_data_callbacks()
        except Exception as e:
            log.warning("[USER_DATA] Listen key oluşturulamadı (devam): %s", e)

        # ADIM 3: Buffer prefill
        await self.positions.prefill_buffers()

        # ── WS callback'lerini kaydet ──
        self._register_ws_callbacks()

        # ── D1 cache ön ısıtma ──
        await asyncio.gather(*[self.daily_cache.get(sym) for sym in config.SYMBOLS])
        log.info("Başlangıç tamamlandı, WebSocket hub başlatılıyor...")

        asyncio.create_task(self._start_api_server())
        asyncio.create_task(self._health_loop())
        await self.hub.run()

    def _register_ws_callbacks(self) -> None:
        """Her sembol için 1m callback'i hub'a kaydet."""
        for sym in config.SYMBOLS:

            def make_callback(s: str):
                async def cb(bars: list[Bar]) -> None:
                    if bars:
                        export_ohlc_1m(bars[-1], s)
                    await self.pipeline.on_1m_close(s, bars)

                return cb

            self.hub.register_callback(sym, "1m", make_callback(sym))

    def _register_user_data_callbacks(self) -> None:
        """ORDER_TRADE_UPDATE + ACCOUNT_UPDATE callback'leri."""

        @self.hub.on_user_data("ORDER_TRADE_UPDATE")
        async def on_order_update(msg: dict) -> None:
            order_data = msg.get("o", {})
            sym = order_data.get("s", "")
            status = order_data.get("X", "")
            log.info("[USER_DATA] ORDER_TRADE_UPDATE | %s | status=%s | type=%s", sym, status, order_data.get("o", ""))

        @self.hub.on_user_data("ACCOUNT_UPDATE")
        async def on_account_update(msg: dict) -> None:
            update_data = msg.get("a", {})
            reason = update_data.get("m", "")
            balances = update_data.get("B", [])
            positions_data = update_data.get("P", [])

            for bal in balances:
                asset = bal.get("a", "")
                if asset in ("USDT", "FDUSD", "USDC"):
                    self.positions._wallet_balance = float(bal.get("wb", self.positions._wallet_balance))
                    self.positions._available_balance = float(bal.get("bc", self.positions._available_balance))
                    self.positions._balance = self.positions._available_balance
            if balances:
                log.debug("[USER_DATA] ACCOUNT_UPDATE | reason=%s | %d balance", reason, len(balances))

            for pos in positions_data:
                sym = pos.get("s", "")
                if sym in self.active_trades:
                    self.active_trades[sym]["pnl"] = float(pos.get("up", 0))
                    self.active_trades[sym]["last_price"] = float(pos.get("ep", 0))
            if positions_data:
                log.debug("[USER_DATA] ACCOUNT_UPDATE | reason=%s | %d pozisyon", reason, len(positions_data))

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.positions.sync_balance()
            except Exception as e:
                log.warning("[HEALTH] Bakiye sync hatası: %s", e)
            try:
                h = monitor.get_health()
                log.info("[HEALTH] %s", json.dumps(h))
            except Exception as e:
                log.debug("[HEALTH] get_health hatası: %s", e)

    # ─────────────────────────────────────────────────────────────────
    # API Server
    # ─────────────────────────────────────────────────────────────────

    async def _start_api_server(self) -> None:
        from aiohttp import web  # noqa: PLC0415

        async def api_health(request):
            return web.json_response(monitor.get_health())

        async def api_balance(request):
            return web.json_response(
                {
                    "balance": self._balance,
                    "wallet_balance": self._wallet_balance,
                    "unrealized_pnl": self._unrealized_pnl,
                    "margin_balance": self._margin_balance,
                    "available_balance": self._available_balance,
                    "used_margin": self._used_margin,
                    "currency": "USDT/FDUSD",
                    "updated": datetime.now(UTC).isoformat(),
                }
            )

        async def api_positions(request):
            trades = [
                {
                    "symbol": sym,
                    "direction": t.get("direction", "").upper(),
                    "entry": t.get("entry"),
                    "sl": t.get("current_sl", t.get("initial_sl")),
                    "tp": t.get("tp"),
                    "lot": t.get("lot"),
                    "pnl": round(t.get("pnl", 0), 4),
                    "last_price": t.get("last_price"),
                    "leverage": config.LEVERAGE,
                }
                for sym, t in self.active_trades.items()
            ]
            return web.json_response(trades)

        async def api_prices(request):
            prices = {}
            for sym in config.SYMBOLS:
                bars = self.hub.get_bars(sym, "1m")
                if bars:
                    b = bars[-1]
                    prices[sym] = {"close": b.close, "open": b.open, "high": b.high, "low": b.low, "volume": b.volume}
            return web.json_response(prices)

        async def api_stats(request):
            h = monitor.get_health()
            sym_data = h.get("symbols", {}).values()
            return web.json_response(
                {
                    "total_signals": sum(v.get("signal_count", 0) for v in sym_data),
                    "total_rejects": sum(v.get("rejected_count", 0) for v in sym_data),
                    "total_orders": sum(v.get("order_count", 0) for v in sym_data),
                    "total_fills": sum(v.get("fill_count", 0) for v in sym_data),
                    "live_symbols": sum(1 for v in sym_data if v.get("status") == "LIVE"),
                    "total_symbols": len(config.SYMBOLS),
                    "active_trades": len(self.active_trades),
                    "balance": self._balance,
                }
            )

        async def api_breakeven_stats(request):
            bl = self.positions.breakeven_log
            total_be = sum(v["count"] for v in bl.values())
            total_adx35 = sum(v["adx_gt_35"] for v in bl.values())
            corr_pct = (total_adx35 / total_be * 100) if total_be > 0 else 0.0
            return web.json_response(
                {
                    "total_breakeven": total_be,
                    "adx_gt_35_breakeven": total_adx35,
                    "correlation_pct": round(corr_pct, 1),
                    "symbols": {
                        sym: {
                            "count": info["count"],
                            "adx_gt_35": info["adx_gt_35"],
                            "adx_pct": round((info["adx_gt_35"] / info["count"]) * 100, 1)
                            if info["count"] > 0
                            else 0.0,
                        }
                        for sym, info in bl.items()
                    },
                }
            )

        async def api_performance(request):
            return web.json_response(performance.get_leaderboard())

        async def api_trades(request):
            formatted = []
            for t in performance.get_trade_log():
                row = t.copy()
                ts_str = row.get("ts", "")
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        row["close_time"] = int(dt.timestamp() * 1000)
                    except Exception:
                        row["close_time"] = None
                else:
                    row["close_time"] = None
                row["exit_price"] = row.get("exit")
                row["gross_rr"] = row.get("rr")
                formatted.append(row)
            return web.json_response(formatted)

        async def dashboard(request):
            filepath = os.path.join(os.path.dirname(__file__), "..", "web", "dashboard.html")
            if os.path.exists(filepath):
                return web.FileResponse(filepath)
            return web.Response(text="dashboard.html bulunamadı", status=404)

        app = web.Application()
        app.router.add_get("/", dashboard)
        app.router.add_get("/api/health", api_health)
        app.router.add_get("/api/balance", api_balance)
        app.router.add_get("/api/positions", api_positions)
        app.router.add_get("/api/prices", api_prices)
        app.router.add_get("/api/stats", api_stats)
        app.router.add_get("/api/performance", api_performance)
        app.router.add_get("/api/breakeven", api_breakeven_stats)
        app.router.add_get("/api/trades", api_trades)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        log.info("Dashboard API başlatıldı → http://0.0.0.0:8080")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    performance.initialize()
    bot = LiveTradingBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Kullanıcı tarafından durduruldu.")
        bot.hub.stop()
        _close_ohlc_writers()
