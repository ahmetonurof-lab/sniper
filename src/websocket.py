"""
websocket_hub.py
────────────────
Binance Futures/Spot multi-symbol WebSocket hub.

Özellikler
──────────
• Tek bağlantıda N sembol × M timeframe (combined stream)
• Bar tamamlanınca kayıtlı callback'leri çağırır
• Her symbol/timeframe için bar buffer tutar (max_bars adet)
• Otomatik yeniden bağlanma (exponential back-off)
• asyncio tabanlı, temiz kapatma (graceful shutdown)

Kullanım
────────
    from websocket_hub import BinanceWSHub
    from analyzer     import MarketAnalyzer, Bar

    hub = BinanceWSHub(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=["5m", "15m", "1h"],
    )

    @hub.on_bar("BTCUSDT", "5m")
    async def handle_btc_m5(bars: list[Bar]) -> None:
        print(f"BTCUSDT 5m kapandı: {bars[-1].close}")

    asyncio.run(hub.run())
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

import websockets
from models import Bar
from websockets.exceptions import ConnectionClosed, InvalidStatus

# ──────────────────────────────────────────────
# İşlem Cooldown (Soğuma) Mekanizması
# ──────────────────────────────────────────────
last_trade_time: dict[str, float] = {}
COOLDOWN_MINUTES = 15


def is_cooldown_active(symbol: str) -> bool:
    current_time = time.time()
    if symbol in last_trade_time:
        time_elapsed = (current_time - last_trade_time[symbol]) / 60
        if time_elapsed < COOLDOWN_MINUTES:
            return True
    return False


def register_trade(symbol: str) -> None:
    last_trade_time[symbol] = time.time()


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

log = logging.getLogger("ws_hub")

# ──────────────────────────────────────────────
# Tipler
# ──────────────────────────────────────────────

BarCallback = Callable[[list[Bar]], Awaitable[None]]

_TF_TO_STREAM = {  # Binance kline stream adları
    "1m": "kline_1m",
    "3m": "kline_3m",
    "5m": "kline_5m",
    "15m": "kline_15m",
    "30m": "kline_30m",
    "1h": "kline_1h",
    "4h": "kline_4h",
    "1d": "kline_1d",
}

# ──────────────────────────────────────────────
# Bar builder — tek symbol/timeframe için
# ──────────────────────────────────────────────


class _BarBuffer:
    """
    Gelen kline mesajlarını Bar nesnelerine çevirir.
    Bar kapandığında (is_closed=True) callback'i tetikler.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        callbacks: list[BarCallback],
        max_bars: int = 500,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.callbacks = callbacks
        self.max_bars = max_bars
        self._bars: list[Bar] = []
        self._next_index: int = 0

    def _kline_to_bar(self, k: dict, is_closed: bool) -> Bar:
        bar = Bar(
            index=self._next_index if is_closed else max(0, self._next_index - 1),
            timestamp=int(k["t"]),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            is_closed=is_closed,
        )
        if is_closed:
            self._next_index += 1
        return bar

    async def feed(self, kline_payload: dict) -> None:
        k = kline_payload["k"]
        is_closed = k["x"]
        bar = self._kline_to_bar(k, is_closed)

        if is_closed:
            # 🛡️ Duplicate bar koruması: WS reconnect sonrası aynı kapalı bar tekrar gelebilir
            if self._bars and bar.timestamp == self._bars[-1].timestamp:
                log.debug(
                    "🛡️ Duplicate bar atlandı: %s %s ts=%d",
                    self.symbol,
                    self.timeframe,
                    bar.timestamp,
                )
                return

            _log = log.debug if self.timeframe == "1m" else log.info
            _log(
                "Bar kapandı: %s %s | close=%.4f",
                self.symbol,
                self.timeframe,
                bar.close,
            )
            self._bars.append(bar)
            if len(self._bars) > self.max_bars:
                self._bars = self._bars[-self.max_bars :]

            snapshot = list(self._bars)
            for cb in self.callbacks:
                try:
                    await cb(snapshot)
                except Exception:
                    log.exception("Callback hatası: %s %s", self.symbol, self.timeframe)


# ──────────────────────────────────────────────
# WebSocket hub
# ──────────────────────────────────────────────


class BinanceWSHub:
    """
    Multi-symbol Binance WebSocket hub.

    Parameters
    ----------
    symbols    : İşlem çiftleri listesi, örn. ["BTCUSDT", "ETHUSDT"]
    timeframes : Zaman dilimleri,        örn. ["5m", "15m", "1h"]
    max_bars   : Her buffer'da saklanacak maksimum bar sayısı
    base_url   : Binance combined stream URL (spot varsayılan)
    reconnect_delay : İlk yeniden bağlanma bekleme süresi (sn)
    max_reconnect_delay : Maksimum bekleme süresi (sn)
    """

    BASE_URL = "wss://stream.binancefuture.com/stream?streams="

    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str] | None = None,
        max_bars: int = 500,
        base_url: str | None = None,
        reconnect_delay: float = 2.0,
        max_reconnect_delay: float = 60.0,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self.timeframes = list(timeframes or ["1m", "5m", "15m", "1h"])
        self.max_bars = max_bars
        self.base_url = base_url or self.BASE_URL
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay

        # (symbol, timeframe) → _BarBuffer
        self._buffers: dict[tuple[str, str], _BarBuffer] = {}
        # (symbol, timeframe) → [callback, ...]
        self._callbacks: dict[tuple[str, str], list[BarCallback]] = defaultdict(list)

        self._stop_event = asyncio.Event()
        self._reconnect_count = 0

        # ── User Data Stream ─────────────────────────
        self._user_data_listen_key: str | None = None
        self._user_data_ws_url: str | None = None
        self._user_data_ws: websockets.WebSocketClientProtocol | None = None
        self._user_data_task: asyncio.Task | None = None
        self._user_data_callbacks: dict[str, list[Callable[[dict], Awaitable[None]]]] = defaultdict(list)

        # ── Heartbeat / timeout izleme ────────────────
        self._last_seen: dict[tuple[str, str], float] = {}  # (symbol, timeframe) → timestamp
        self._heartbeat_task: asyncio.Task | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None
        # Her timeframe için maksimum beklenen tick aralığı + %50 tolerans
        self._tf_timeouts: dict[str, int] = {
            "1m": 90,  # 60 sn  + %50
            "3m": 270,  # 180 sn + %50
            "5m": 450,  # 300 sn + %50
            "15m": 1350,  # 900 sn + %50
            "30m": 2700,  # 1800 sn + %50
            "1h": 5400,  # 3600 sn + %50
            "4h": 21600,  # 14400 sn + %50
            "1d": 129600,  # 86400 sn + %50
        }
        self._heartbeat_check_interval = 30  # 30 sn'de bir kontrol

    # ── Callback kaydı ──────────────────────────

    def on_bar(
        self,
        symbol: str,
        timeframe: str,
    ) -> Callable[[BarCallback], BarCallback]:
        """
        Dekoratör olarak kullanılan callback kaydedici.

            @hub.on_bar("BTCUSDT", "5m")
            async def handler(bars): ...
        """

        def decorator(fn: BarCallback) -> BarCallback:
            self.register_callback(symbol.upper(), timeframe, fn)
            return fn

        return decorator

    def register_callback(
        self,
        symbol: str,
        timeframe: str,
        callback: BarCallback,
    ) -> None:
        """Programatik callback kaydı."""
        key = (symbol.upper(), timeframe)
        self._callbacks[key].append(callback)

    # ── User Data Stream ────────────────────────

    def set_user_data_listen_key(
        self,
        listen_key: str,
        ws_base_url: str = "wss://stream.binancefuture.com",
    ) -> None:
        """User data stream için listen key ve WebSocket URL'ini ayarlar."""
        self._user_data_listen_key = listen_key
        self._user_data_ws_url = f"{ws_base_url.rstrip('/')}/ws/{listen_key}"

    def on_user_data(
        self,
        event_type: str,
    ) -> Callable[[Callable[[dict], Awaitable[None]]], Callable[[dict], Awaitable[None]]]:
        """
        User data event callback dekoratörü.

        event_type: 'ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE', 'listenKeyExpired'

            @hub.on_user_data("ORDER_TRADE_UPDATE")
            async def handle_order(msg): ...
        """

        def decorator(fn: Callable[[dict], Awaitable[None]]) -> Callable[[dict], Awaitable[None]]:
            self._user_data_callbacks[event_type].append(fn)
            return fn

        return decorator

    async def _user_data_listen_loop(self) -> None:
        """User data stream WebSocket bağlantısını yönetir."""
        while not self._stop_event.is_set():
            if not self._user_data_listen_key or not self._user_data_ws_url:
                await asyncio.sleep(5)
                continue

            try:
                async with websockets.connect(
                    self._user_data_ws_url,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=10,
                    open_timeout=30,
                ) as ws:
                    self._user_data_ws = ws
                    log.info("[USER_DATA] User data stream bağlantısı kuruldu")
                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        msg = raw.decode() if isinstance(raw, bytes) else raw
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        event_type = data.get("e", "")
                        callbacks = self._user_data_callbacks.get(event_type, [])
                        for cb in callbacks:
                            try:
                                await cb(data)
                            except Exception:
                                log.exception("[USER_DATA] Callback hatası: %s", event_type)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._stop_event.is_set():
                    log.warning("[USER_DATA] Bağlantı hatası (%.1f sn sonra yeniden): %s", 5, e)
                    await asyncio.sleep(5)

    async def _listen_key_refresh_loop(self, http_client) -> None:
        """30 dakikada bir listen key'i yeniler."""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(1800)  # 30 dakika
                if self._user_data_listen_key:
                    try:
                        http_client.renew_listen_key(self._user_data_listen_key)
                        log.info("[USER_DATA] Listen key yenilendi")
                    except Exception as e:
                        log.warning("[USER_DATA] Listen key yenileme hatası: %s", e)
        except asyncio.CancelledError:
            pass

    # ── Stream URL ──────────────────────────────

    def _build_url(self) -> str:
        streams = []
        for sym in self.symbols:
            for tf in self.timeframes:
                stream_name = _TF_TO_STREAM.get(tf)
                if stream_name is None:
                    raise ValueError(f"Desteklenmeyen timeframe: {tf!r}")
                streams.append(f"{sym.lower()}@{stream_name}")
        return self.base_url + "/".join(streams)

    # ── Buffer erişimi ──────────────────────────

    def _get_buffer(self, symbol: str, timeframe: str) -> _BarBuffer:
        key = (symbol.upper(), timeframe)
        if key not in self._buffers:
            self._buffers[key] = _BarBuffer(
                symbol=symbol,
                timeframe=timeframe,
                callbacks=self._callbacks[key],
                max_bars=self.max_bars,
            )
        return self._buffers[key]

    def get_bars(self, symbol: str, timeframe: str) -> list[Bar]:
        """Anlık bar snapshot'ı döner (thread-safe değil, sadece asyncio loop'ta çağırın)."""
        buf = self._buffers.get((symbol.upper(), timeframe))
        return list(buf._bars) if buf else []

    # ── Mesaj yönlendirme ───────────────────────

    async def _dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("JSON parse hatası: %.120s", raw)
            return

        # Combined stream formatı: {"stream": "btcusdt@kline_5m", "data": {...}}
        stream = msg.get("stream", "")
        data = msg.get("data", msg)  # tekil stream için fallback

        if data.get("e") != "kline":
            return

        # stream → "btcusdt@kline_5m"
        parts = stream.split("@")
        if len(parts) != 2:
            return

        symbol = parts[0].upper()

        # kline_1h → 1h
        tf_raw = parts[1]  # "kline_5m"
        tf = tf_raw.replace("kline_", "")

        buf = self._get_buffer(symbol, tf)
        await buf.feed(data)

        # ── Heartbeat: sadece gelen tf'yi güncelle ──
        self._last_seen[(symbol, tf)] = time.time()

    # ── Ana döngü ───────────────────────────────

    async def _heartbeat_monitor(self) -> None:
        """
        Her `_heartbeat_check_interval` saniyede bir tüm sembollerin
        son tick zamanını kontrol eder. Eğer bir sembol `_heartbeat_timeout`
        saniyedir tick almamışsa bağlantıyı kapatır (yeniden bağlanma tetiklenir).
        """
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(self._heartbeat_check_interval)
                now = time.time()
                timed_out_symbols = []
                for (sym, tf), last_ts in list(self._last_seen.items()):
                    elapsed = now - last_ts
                    timeout = self._tf_timeouts.get(tf, 120)
                    if elapsed > timeout:
                        timed_out_symbols.append(sym)
                        log.warning(
                            "⏰ Heartbeat timeout | %s %s | son tick=%.0f sn önce (limit=%dsn)",
                            sym,
                            tf,
                            elapsed,
                            timeout,
                        )

                if timed_out_symbols:
                    unique_symbols = sorted(set(timed_out_symbols))
                    log.warning(
                        "❤️‍🔥 Heartbeat timeout olan semboller: %s — bağlantı yenileniyor...",
                        unique_symbols,
                    )
                    # Bağlantıyı kapat → _connect_and_listen çıkış yapar → run() reconnect başlatır
                    if self._ws is not None:
                        await self._ws.close()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Heartbeat monitor hatası")

    async def _connect_and_listen(self) -> None:
        url = self._build_url()
        log.info("Bağlanıyor: %s", url)

        # Bağlantı öncesi _last_seen sıfırla (tüm sembol/timeframe çiftleri)
        now = time.time()
        for sym in self.symbols:
            for tf in self.timeframes:
                self._last_seen[(sym, tf)] = now

        # Heartbeat monitor'ü başlat (önceki varsa iptal et)
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

        try:
            async with websockets.connect(
                url,
                ping_interval=60,
                ping_timeout=40,
                close_timeout=10,
                open_timeout=30,
            ) as ws:
                self._ws = ws
                log.info(
                    "Bağlantı kuruldu (%d stream) | toplam reconnect: %d",
                    len(self.symbols) * len(self.timeframes),
                    self._reconnect_count,
                )
                try:
                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        msg = raw.decode() if isinstance(raw, bytes) else raw
                        await self._dispatch(msg)
                finally:
                    self._ws = None
        except InvalidStatus as e:
            # 502/503 → sunucu tarafı geçici hata, üste fırlat ki run() uzun beklesin
            log.warning("WS handshake reddedildi: %s | url=%s", e, url)
            raise

    async def run(self) -> None:
        """
        Hub'ı başlatır; bağlantı kesilirse otomatik yeniden bağlanır.
        User data stream varsa onu da arka planda başlatır.
        Durdurmak için `stop()` çağırın.
        """
        # ── User data stream arka plan task'ları ──
        if self._user_data_listen_key:
            self._user_data_task = asyncio.create_task(self._user_data_listen_loop())

        delay = self.reconnect_delay
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
                delay = self.reconnect_delay
            except InvalidStatus as exc:
                if self._stop_event.is_set():
                    break
                self._reconnect_count += 1
                # 502/503 sunucu hatası → uzun bekle, kısa aralıklarla sunucuyu yorma
                delay = max(delay, 15.0)
                log.warning(
                    "WS sunucu hatası (%s). %.1f sn sonra yeniden bağlanılacak. [toplam reconnect: %d]",
                    exc,
                    delay,
                    self._reconnect_count,
                )
            except ConnectionClosed as exc:
                if self._stop_event.is_set():
                    break
                self._reconnect_count += 1
                log.warning(
                    "Bağlantı kapandı (%s). %.1f sn sonra yeniden bağlanılacak. [toplam reconnect: %d]",
                    exc,
                    delay,
                    self._reconnect_count,
                )
            except (TimeoutError, OSError):
                if self._stop_event.is_set():
                    break
                self._reconnect_count += 1
                log.warning(
                    "Bağlantı zaman aşımı / ağ hatası. %.1f sn sonra yeniden bağlanılacak. [toplam reconnect: %d]",
                    delay,
                    self._reconnect_count,
                )
            except Exception:
                if self._stop_event.is_set():
                    break
                self._reconnect_count += 1
                log.exception(
                    "Beklenmeyen hata. %.1f sn sonra yeniden bağlanılacak. [toplam reconnect: %d]",
                    delay,
                    self._reconnect_count,
                )

            if not self._stop_event.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_reconnect_delay)

        log.info("Hub durduruldu. Toplam reconnect: %d", self._reconnect_count)

    def stop(self) -> None:
        """Hub'ı düzgünce durdurur."""
        self._stop_event.set()
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._user_data_task is not None and not self._user_data_task.done():
            self._user_data_task.cancel()
        if self._user_data_ws is not None:
            self._user_data_ws = None
        if self._ws is not None:
            pass


# ──────────────────────────────────────────────
# Örnek kullanim  (python websocket.py)
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    SYMBOLS = ["BTCUSDT", "ETHUSDT"]
    TIMEFRAMES = ["1m", "5m", "15m", "1h"]

    hub = BinanceWSHub(symbols=SYMBOLS, timeframes=TIMEFRAMES)

    @hub.on_bar("BTCUSDT", "15m")
    async def btc_m15_handler(bars: list[Bar]) -> None:
        log.info("BTCUSDT 15m | bar=%d | close=%.2f | high=%.2f | low=%.2f",
                 len(bars), bars[-1].close, bars[-1].high, bars[-1].low)

    @hub.on_bar("ETHUSDT", "15m")
    async def eth_m15_handler(bars: list[Bar]) -> None:
        log.info("ETHUSDT 15m | bar=%d | close=%.2f", len(bars), bars[-1].close)

    async def main() -> None:
        try:
            await hub.run()
        except KeyboardInterrupt:
            hub.stop()

    asyncio.run(main())
