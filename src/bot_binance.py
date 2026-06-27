"""
bot_binance.py — NEXUS V4 / P9
────────────────────────────────
İmzalı Binance REST çağrıları: GET / POST / DELETE + retry + circuit breaker.
aiohttp tabanlı native async HTTP — urllib tamamen kaldırıldı.
LiveTradingBot instance state'ini bilmez — bağımsız test edilebilir.

P9 değişiklikleri:
  - urllib.request → aiohttp.ClientSession (native async, connection pooling)
  - RetryConfig + CircuitBreaker entegrasyonu
  - get()/post()/delete() → Result[dict]
  - ClientTimeout yapılandırması
  - _prefill_bars artık BinanceRESTClient üzerinden çalışıyor

Orijinal konum: sonnet/src/main.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from typing import Any

import aiohttp

from bot_infra import CircuitBreaker, RetryConfig
from models import Result

log = logging.getLogger("nexus.live")


# ─────────────────────────────────────────────────────────────────
# Precision yardımcıları (sonnet exchange.py'den)
# ─────────────────────────────────────────────────────────────────


def _round_to_tick(value: float, tick: float) -> float:
    """Değeri tick size'a yuvarla."""
    if tick <= 0:
        return value
    return round(round(value / tick) * tick, 8)


def _round_step(value: float, step: float) -> float:
    """Değeri step size'a göre aşağı yuvarla (lot hesapları için)."""
    if step <= 0:
        return value
    return round((value // step) * step, 8)


class BinanceRESTClient:
    """
    İmzalı Binance Futures REST istemcisi (P9: aiohttp tabanlı).
    Rate limiter + retry (exponential backoff + jitter) + circuit breaker içerir.
    LiveTradingBot'un dış API iletişim katmanı.

    Bağımlılıklar enjekte edilir — test'te mock kullanılabilir.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        rate_limiter: Any,
        semaphore: asyncio.Semaphore,
        retry_config: RetryConfig | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        connector_limit: int = 10,
        connector_limit_per_host: int = 5,
        timeout_total: float = 30.0,
        timeout_connect: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url
        self._rate_limiter = rate_limiter
        self._semaphore = semaphore
        self._retry_config = retry_config or RetryConfig()
        self._circuit_breaker = circuit_breaker or CircuitBreaker()

        # aiohttp session (lazy init)
        self._session: aiohttp.ClientSession | None = None
        self._connector_limit = connector_limit
        self._connector_limit_per_host = connector_limit_per_host
        self._timeout_total = timeout_total
        self._timeout_connect = timeout_connect

        # Cache
        self._exchange_info: dict | None = None
        self._exchange_info_ts: float = 0.0
        self._symbol_info: dict[str, dict] = {}

    # ─────────────────────────────────────────────────────────────
    # Session yönetimi (P9.2)
    # ─────────────────────────────────────────────────────────────

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """aiohttp session'ı lazy olarak oluştur veya yeniden bağlan.

        Windows'ta aiodns (pycares) DNS çözümleyici sorunu nedeniyle
        ThreadedResolver kullanılır — getaddrinfo'yu thread pool'da çalıştırır.
        """
        if self._session is None or self._session.closed:
            from aiohttp.resolver import ThreadedResolver

            connector = aiohttp.TCPConnector(
                limit=self._connector_limit,
                limit_per_host=self._connector_limit_per_host,
                ttl_dns_cache=300,
                resolver=ThreadedResolver(),
            )
            timeout = aiohttp.ClientTimeout(
                total=self._timeout_total,
                connect=self._timeout_connect,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
            )
            log.debug(
                "[HTTP] aiohttp session oluşturuldu (limit=%d, limit_per_host=%d)",
                self._connector_limit,
                self._connector_limit_per_host,
            )
        return self._session

    async def close(self) -> None:
        """Session'ı kapat. Graceful shutdown için."""
        if self._session and not self._session.closed:
            await self._session.close()
            log.debug("[HTTP] aiohttp session kapatıldı")

    # ─────────────────────────────────────────────────────────────────
    # Statik yardımcılar (main.py'de @staticmethod olarak tanımlıydı)
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def get_order_type(order: dict) -> str:
        """Standard endpoint (`type`) ve algo endpoint (`orderType`) response alanını birleştirir."""
        return order.get("type") or order.get("orderType") or ""

    @staticmethod
    def get_order_price(order: dict) -> float:
        """Algo emirlerinde `triggerPrice`, normal emirlerde `stopPrice` kullanılır."""
        return float(order.get("triggerPrice") or order.get("stopPrice") or 0)

    @staticmethod
    def get_order_timestamp(order: dict) -> int:
        """Güvenli timestamp çıkarma. None/geçersiz değerlerde 0 döner."""
        try:
            raw = order.get("updateTime") or order.get("time") or 0
            return int(raw)
        except (ValueError, TypeError):
            return 0

    # ─────────────────────────────────────────────────────────────────
    # Exchange Info (önbellekli) — sonnet exchange.py'den
    # ─────────────────────────────────────────────────────────────────

    async def _load_exchange_info(self, force: bool = False) -> dict:
        """Exchange info'yu yükler, 5 dakika önbellekte tutar."""
        now = time.time()
        if not force and self._exchange_info and (now - self._exchange_info_ts) < 300:
            return self._exchange_info
        r = await self.get("/fapi/v1/exchangeInfo")
        if r.is_err:
            log.warning("[EXCHANGE_INFO] Yüklenemedi: %s", r.error)
            return self._exchange_info or {}
        data = r.value
        self._exchange_info = data
        self._exchange_info_ts = now
        self._symbol_info.clear()
        for s in data.get("symbols", []):
            self._symbol_info[s["symbol"]] = s
        log.info("[EXCHANGE_INFO] %d sembol yüklendi", len(self._symbol_info))
        return data

    async def get_symbol_info(self, symbol: str) -> dict | None:
        """Tek bir sembolün exchange info'sunu döner (önbellekten)."""
        await self._load_exchange_info()
        return self._symbol_info.get(symbol)

    # ─────────────────────────────────────────────────────────────────
    # Precision yardımcıları — sonnet exchange.py'den
    # ─────────────────────────────────────────────────────────────────

    async def get_tick_size(self, symbol: str) -> float:
        """Sembolün tick size'ını döner (fiyat hassasiyeti)."""
        info = await self.get_symbol_info(symbol)
        if not info:
            return 0.0001
        for f in info.get("filters", []):
            if f["filterType"] == "PRICE_FILTER":
                return float(f.get("tickSize", 0.0001))
        return 0.0001

    async def get_step_size(self, symbol: str) -> float:
        """Sembolün step size'ını döner (miktar hassasiyeti)."""
        info = await self.get_symbol_info(symbol)
        if not info:
            return 0.001
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                return float(f.get("stepSize", 0.001))
        return 0.001

    async def get_min_qty(self, symbol: str) -> float:
        """Sembolün minimum işlem miktarını döner."""
        info = await self.get_symbol_info(symbol)
        if not info:
            return 0.0
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                return float(f.get("minQty", 0.0))
        return 0.0

    async def apply_price_precision(self, symbol: str, price: float) -> float:
        """Fiyatı tick size'a göre yuvarla."""
        if price is None or price == 0:
            return price
        return _round_to_tick(price, await self.get_tick_size(symbol))

    async def apply_amount_precision(self, symbol: str, amount: float) -> float:
        """Miktarı step size'a göre yuvarla."""
        if amount is None or amount == 0:
            return amount
        return _round_step(amount, await self.get_step_size(symbol))

    async def validate_min_amount(self, symbol: str, amount: float) -> float:
        """Amount < minQty ise 0.0 döner, yoksa amount'u döner."""
        if amount <= 0:
            return 0.0
        min_qty = await self.get_min_qty(symbol)
        if min_qty > 0 and amount < min_qty:
            log.warning(
                "[MINQTY] %s amount=%.8f < min_qty=%.8f", symbol, amount, min_qty
            )
            return 0.0
        return amount

    # ─────────────────────────────────────────────────────────────
    # Transport katmanı (P9.2: aiohttp + P9.3: Result[dict])
    # ─────────────────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        """API key header'ı."""
        return {"X-MBX-APIKEY": self._api_key}

    def _sign_params(self, params_str: str) -> str:
        """Query string'e timestamp + signature ekle."""
        ts = int(time.time() * 1000)
        full = f"{params_str}&timestamp={ts}" if params_str else f"timestamp={ts}"
        sig = hmac.new(
            self._api_secret.encode(), full.encode(), hashlib.sha256
        ).hexdigest()
        return f"{full}&signature={sig}"

    def _compute_backoff(self, attempt: int) -> float:
        """Exponential backoff + optional jitter hesapla."""
        rc = self._retry_config
        delay = rc.base_delay * (rc.backoff_multiplier**attempt)
        delay = min(delay, rc.max_delay)
        if rc.jitter:
            jitter = delay * 0.25 * (2 * random.random() - 1)
            delay = max(0, delay + jitter)
        return delay

    def _should_retry(self, status: int | None) -> bool:
        """Bu HTTP status kodunda retry yapılmalı mı?"""
        if status is None:
            return True  # bağlantı hatası → retry
        return status in self._retry_config.retry_on_http

    async def get(self, endpoint: str, params: str = "") -> Result[dict]:
        """İmzalı GET isteği — exponential backoff + jitter + circuit breaker.

        Returns:
            Result[dict]: Başarılıysa ok(value=parsed_json), hata varsa fail(error=msg)
        """

        async def _do_get() -> dict:
            await self._rate_limiter.acquire()
            async with self._semaphore:
                session = await self._ensure_session()
                signed = self._sign_params(params)
                url = f"{self._base_url}{endpoint}?{signed}"
                headers = self._build_headers()
                last_error = None
                rc = self._retry_config
                for attempt in range(rc.max_retries):
                    try:
                        async with session.get(url, headers=headers) as resp:
                            text = await resp.text()
                            if resp.status == 200:
                                return json.loads(text)
                            last_error = f"HTTP {resp.status}: {text[:200]}"
                            if not self._should_retry(resp.status):
                                break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        last_error = f"{type(e).__name__}: {e}"[:200]
                        if not self._should_retry(None):
                            break
                    except Exception as e:
                        last_error = str(e)[:200]
                        if not self._should_retry(None):
                            break
                    if attempt < rc.max_retries - 1:
                        delay = self._compute_backoff(attempt)
                        log.warning(
                            "[HTTP] %s → %s (attempt %d/%d, %.1fs backoff)",
                            endpoint,
                            last_error,
                            attempt + 1,
                            rc.max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                raise Exception(last_error or "unknown HTTP error")

        try:
            result = await self._circuit_breaker.call(_do_get)
            if isinstance(result, Result):
                return result  # Circuit breaker fail'i
            return Result.ok(result)
        except Exception as e:
            return Result.fail(str(e))

    async def post(self, endpoint: str, params: dict) -> Result[dict]:
        """İmzalı POST isteği — exponential backoff + jitter + circuit breaker.

        Returns:
            Result[dict]: Başarılıysa ok(value=parsed_json), hata varsa fail(error=msg)
        """

        async def _do_post() -> dict:
            await self._rate_limiter.acquire()
            async with self._semaphore:
                session = await self._ensure_session()
                params["timestamp"] = int(time.time() * 1000)
                query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                sig = hmac.new(
                    self._api_secret.encode(),
                    query_string.encode(),
                    hashlib.sha256,
                ).hexdigest()
                query_string += f"&signature={sig}"
                url = f"{self._base_url}{endpoint}"
                headers = self._build_headers()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                last_error = None
                rc = self._retry_config
                for attempt in range(rc.max_retries):
                    try:
                        async with session.post(
                            url, data=query_string, headers=headers
                        ) as resp:
                            text = await resp.text()
                            if resp.status == 200:
                                return json.loads(text)
                            last_error = f"HTTP {resp.status}: {text[:200]}"
                            if not self._should_retry(resp.status):
                                break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        last_error = f"{type(e).__name__}: {e}"[:200]
                        if not self._should_retry(None):
                            break
                    except Exception as e:
                        last_error = str(e)[:200]
                        if not self._should_retry(None):
                            break
                    if attempt < rc.max_retries - 1:
                        delay = self._compute_backoff(attempt)
                        log.warning(
                            "[HTTP-POST] %s → %s (attempt %d/%d, %.1fs backoff)",
                            endpoint,
                            last_error,
                            attempt + 1,
                            rc.max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                raise Exception(last_error or "unknown HTTP error")

        try:
            result = await self._circuit_breaker.call(_do_post)
            if isinstance(result, Result):
                return result
            return Result.ok(result)
        except Exception as e:
            return Result.fail(str(e))

    async def delete(self, endpoint: str, params: str = "") -> Result[dict]:
        """İmzalı DELETE isteği — exponential backoff + jitter + circuit breaker.

        Returns:
            Result[dict]: Başarılıysa ok(value=parsed_json), hata varsa fail(error=msg)
        """

        async def _do_delete() -> dict:
            await self._rate_limiter.acquire()
            async with self._semaphore:
                session = await self._ensure_session()
                signed = self._sign_params(params)
                url = f"{self._base_url}{endpoint}?{signed}"
                headers = self._build_headers()
                last_error = None
                rc = self._retry_config
                for attempt in range(rc.max_retries):
                    try:
                        async with session.delete(url, headers=headers) as resp:
                            text = await resp.text()
                            if resp.status == 200:
                                return json.loads(text)
                            last_error = f"HTTP {resp.status}: {text[:200]}"
                            if not self._should_retry(resp.status):
                                break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        last_error = f"{type(e).__name__}: {e}"[:200]
                        if not self._should_retry(None):
                            break
                    except Exception as e:
                        last_error = str(e)[:200]
                        if not self._should_retry(None):
                            break
                    if attempt < rc.max_retries - 1:
                        delay = self._compute_backoff(attempt)
                        log.warning(
                            "[HTTP-DEL] %s → %s (attempt %d/%d, %.1fs backoff)",
                            endpoint,
                            last_error,
                            attempt + 1,
                            rc.max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                raise Exception(last_error or "unknown HTTP error")

        try:
            result = await self._circuit_breaker.call(_do_delete)
            if isinstance(result, Result):
                return result
            return Result.ok(result)
        except Exception as e:
            return Result.fail(str(e))

    # ─────────────────────────────────────────────────────────────────
    # Emir sorgu / iptal
    # ─────────────────────────────────────────────────────────────────

    async def get_open_orders(self, symbol: str) -> list:
        """Sembol için açık normal emirleri döner (list)."""
        r = await self.get("/fapi/v1/openOrders", f"symbol={symbol}")
        if r.is_err:
            log.error(
                "[ORDERS] Açık emirler alınamadı %s: %s", symbol.ljust(12), r.error
            )
            return []
        result = r.value
        return result if isinstance(result, list) else []

    async def get_all_orders(self, symbol: str) -> list:
        """Normal + algo emirleri birleşik olarak döner."""
        orders = await self.get_open_orders(symbol)
        r = await self.get("/fapi/v1/openAlgoOrders", f"symbol={symbol}")
        if r.is_ok and isinstance(r.value, list):
            orders.extend(r.value)
        elif r.is_err:
            log.debug(
                "[ORDERS] algoOrders alınamadı %s (önemsiz): %s",
                symbol.ljust(12),
                r.error,
            )
        return orders

    async def get_balance(self) -> float:
        r = await self.get("/fapi/v2/account")
        if r.is_err:
            log.warning("[BALANCE] Bakiye alınamadı: %s", r.error)
            return 0.0
        for asset in r.value.get("assets", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("walletBalance", 0))
        return 0.0

    async def get_positions(self) -> list[dict]:
        r = await self.get("/fapi/v2/account")
        if r.is_err:
            log.warning("[POSITIONS] Pozisyonlar alınamadı: %s", r.error)
            return []
        return [
            p
            for p in r.value.get("positions", [])
            if float(p.get("positionAmt", 0)) != 0
        ]

    async def place_market_order(
        self, symbol: str, side: str, qty: float, reduce_only: bool = False
    ) -> dict:
        """
        MARKET emri gonderir (pozisyon acmak/kapatmak icin).
        Precision uygular, demo API fallback yapar.
        """
        rounded_qty = await self.apply_amount_precision(symbol, qty)
        valid_qty = await self.validate_min_amount(symbol, rounded_qty)
        if valid_qty <= 0:
            log.warning("[MARKET] %s qty=%.8f minQty altinda, iptal", symbol, qty)
            return {}

        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": rounded_qty,
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        r = await self.post("/fapi/v1/order", params)
        if r.is_err:
            log.warning("[MARKET] %s MARKET hatasi: %s", symbol, r.error)
            return {}
        result = r.value
        if result.get("orderId") or result.get("id"):
            return result
        # Demo API: orderId dönmezse GET ile bul
        await asyncio.sleep(0.5)
        try:
            orders = await self.get_open_orders(symbol)
            for o in orders if isinstance(orders, list) else []:
                if (
                    o.get("symbol") == symbol
                    and o.get("side", "").upper() == side.upper()
                    and o.get("type", "").upper() == "MARKET"
                ):
                    return o
        except Exception:
            pass
        return result

    async def place_stop_order(
        self, symbol: str, side: str, qty: float, stop_price: float, client_id: str = ""
    ) -> dict:
        """
        STOP_MARKET emri — Algo endpoint (/fapi/v1/algoOrder) kullanir.
        reduceOnly=True ile closePosition yerine — birden fazla emre izin verir.
        """
        rounded_qty = await self.apply_amount_precision(symbol, qty)
        valid_qty = await self.validate_min_amount(symbol, rounded_qty)
        if valid_qty <= 0:
            log.warning("[SL] %s qty=%.8f minQty altinda, iptal", symbol, qty)
            return {}

        rounded_price = await self.apply_price_precision(symbol, stop_price)

        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "STOP_MARKET",
            "algoType": "CONDITIONAL",
            "workingType": "MARK_PRICE",
            "quantity": rounded_qty,
            "triggerPrice": str(rounded_price),
            "reduceOnly": "true",
            "timeInForce": "GTC",
            "newClientOrderId": client_id or f"sl_{symbol}_{int(time.time())}",
        }
        r = await self.post("/fapi/v1/algoOrder", params)
        if r.is_err:
            log.warning("[SL] %s STOP_MARKET hatasi: %s", symbol, r.error)
            return {}
        result = r.value
        if result.get("algoId") or result.get("orderId") or result.get("id"):
            return result
        # Demo API fallback
        await asyncio.sleep(0.5)
        try:
            orders_r = await self.get("/fapi/v1/openAlgoOrders", f"symbol={symbol}")
            orders = orders_r.value if orders_r.is_ok else []
            for o in orders if isinstance(orders, list) else []:
                if (
                    o.get("symbol") == symbol
                    and o.get("side", "").upper() == side.upper()
                    and (o.get("type") or o.get("orderType", "")).upper()
                    == "STOP_MARKET"
                ):
                    return o
        except Exception:
            pass
        return result

    async def place_tp_order(
        self, symbol: str, side: str, qty: float, stop_price: float, client_id: str = ""
    ) -> dict:
        """
        TAKE_PROFIT_MARKET emri — Algo endpoint (/fapi/v1/algoOrder) kullanir.
        """
        rounded_qty = await self.apply_amount_precision(symbol, qty)
        valid_qty = await self.validate_min_amount(symbol, rounded_qty)
        if valid_qty <= 0:
            log.warning("[TP] %s qty=%.8f minQty altinda, iptal", symbol, qty)
            return {}

        rounded_price = await self.apply_price_precision(symbol, stop_price)

        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "TAKE_PROFIT_MARKET",
            "algoType": "CONDITIONAL",
            "workingType": "MARK_PRICE",
            "quantity": rounded_qty,
            "triggerPrice": str(rounded_price),
            "reduceOnly": "true",
            "timeInForce": "GTC",
            "newClientOrderId": client_id or f"tp_{symbol}_{int(time.time())}",
        }
        r = await self.post("/fapi/v1/algoOrder", params)
        if r.is_err:
            log.warning("[TP] %s TAKE_PROFIT_MARKET hatasi: %s", symbol, r.error)
            return {}
        result = r.value
        if result.get("algoId") or result.get("orderId") or result.get("id"):
            return result
        # Demo API fallback
        await asyncio.sleep(0.5)
        try:
            orders_r = await self.get("/fapi/v1/openAlgoOrders", f"symbol={symbol}")
            orders = orders_r.value if orders_r.is_ok else []
            for o in orders if isinstance(orders, list) else []:
                if (
                    o.get("symbol") == symbol
                    and o.get("side", "").upper() == side.upper()
                    and (o.get("type") or o.get("orderType", "")).upper()
                    == "TAKE_PROFIT_MARKET"
                ):
                    return o
        except Exception:
            pass
        return result

    async def cancel_order(
        self,
        order_id: Any,
        symbol: str,
        reason: str = "",
        is_algo: bool = False,
    ) -> bool:
        """Tek bir emri Binance REST API ile iptal et (DELETE)."""

        def _check_unknown(err: str) -> bool:
            return "Unknown order" in err or "-2011" in err

        if is_algo:
            params = f"symbol={symbol}&algoId={order_id}"
            r = await self.delete("/fapi/v1/algoOrder", params)
            if r.is_ok:
                log.info(
                    "🧹 İPTAL (algo) | %s algoId=%s reason=%s",
                    symbol.ljust(12),
                    order_id,
                    reason,
                )
                return True
            if _check_unknown(r.error):
                log.info(
                    "🧹 İPTAL (algo) | %s algoId=%s zaten yok (ok)",
                    symbol.ljust(12),
                    order_id,
                )
                return True
            log.warning(
                "🧹 İPTAL hatası (algo) %s algoId=%s: %s",
                symbol.ljust(12),
                order_id,
                r.error,
            )
            return False
        else:
            params = f"symbol={symbol}&orderId={order_id}"
            r = await self.delete("/fapi/v1/order", params)
            if r.is_ok:
                log.info(
                    "🧹 İPTAL | %s orderId=%s reason=%s",
                    symbol.ljust(12),
                    order_id,
                    reason,
                )
                return True
            err = r.error
            if _check_unknown(err):
                log.info(
                    "🧹 İPTAL | %s orderId=%s zaten yok (ok)",
                    symbol.ljust(12),
                    order_id,
                )
                return True
            # Algo endpoint'ini dene
            params2 = f"symbol={symbol}&algoId={order_id}"
            r2 = await self.delete("/fapi/v1/algoOrder", params2)
            if r2.is_ok:
                log.info(
                    "🧹 İPTAL (algo fallback) | %s algoId=%s reason=%s",
                    symbol.ljust(12),
                    order_id,
                    reason,
                )
                return True
            log.warning(
                "🧹 İPTAL hatası %s orderId=%s (normal+algo): %s / %s",
                symbol.ljust(12),
                order_id,
                err,
                r2.error,
            )
            return False

    # ─────────────────────────────────────────────────────────────
    # Listen Key (User Data Stream) — imzasız, sadece API Key header
    # P9.2: aiohttp native async — artık blocking değil
    # ─────────────────────────────────────────────────────────────

    async def _unsigned_post(self, endpoint: str) -> Result[dict]:
        """İmzasız POST isteği (listen key oluşturma için)."""
        try:
            session = await self._ensure_session()
            url = f"{self._base_url}{endpoint}"
            headers = self._build_headers()
            async with session.post(url, headers=headers) as resp:
                text = await resp.text()
                if resp.status == 200:
                    return Result.ok(json.loads(text))
                return Result.fail(f"HTTP {resp.status}: {text[:200]}")
        except Exception as e:
            return Result.fail(str(e))

    async def _unsigned_put(self, endpoint: str) -> Result[dict]:
        """İmzasız PUT isteği (listen key yenileme için)."""
        try:
            session = await self._ensure_session()
            url = f"{self._base_url}{endpoint}"
            headers = self._build_headers()
            async with session.put(url, headers=headers) as resp:
                text = await resp.text()
                if resp.status == 200:
                    return Result.ok(json.loads(text))
                return Result.fail(f"HTTP {resp.status}: {text[:200]}")
        except Exception as e:
            return Result.fail(str(e))

    async def get_listen_key(self) -> str:
        """Yeni bir listen key oluştur (native async)."""
        r = await self._unsigned_post("/fapi/v1/listenKey")
        if r.is_err:
            raise Exception(f"Listen key alınamadı: {r.error}")
        key = r.value.get("listenKey", "")
        if not key:
            raise Exception("Listen key alınamadı: boş yanıt")
        log.info("[LISTEN_KEY] Yeni listen key olusturuldu")
        return key

    async def renew_listen_key(self, listen_key: str) -> None:
        """30 dakikada bir listen key'i yenile (native async)."""
        r = await self._unsigned_put(f"/fapi/v1/listenKey?listenKey={listen_key}")
        if r.is_ok:
            log.debug("[LISTEN_KEY] Key yenilendi")
        else:
            log.warning("[LISTEN_KEY] Yenileme hatasi: %s", r.error)
