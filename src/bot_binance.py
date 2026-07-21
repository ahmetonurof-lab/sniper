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


def _get_precision_places(value: float) -> int:
    """Bir sayının ondalık hassasiyetini (kaç sıfır olduğunu) döner."""
    s = f"{value:.8f}".rstrip("0")
    if "." not in s:
        return 0
    return len(s) - s.index(".") - 1


def _round_to_tick(value: float, tick: float) -> float:
    """Değeri tick size'a yuvarla."""
    if tick <= 0:
        return value
    decimals = max(_get_precision_places(tick), 8)
    return round(round(value / tick) * tick, decimals)


def _round_step(value: float, step: float) -> float:
    """Değeri step size'a göre aşağı yuvarla (lot hesapları için).

    NOT: floor division (value // step) kullanilmaz — floating-point
    precision hatasiyla 1 step eksik hesaplanabilir (7275.8 // 0.1 = 72757,
    72757*0.1 = 7275.7). Bunun yerine once step-sayisina cevir (int),
    sonra geri carp. int() truncate eder = floor (pozitif degerler icin).
    """
    if step <= 0:
        return value
    decimals = max(_get_precision_places(step), 8)
    steps = int(value / step)
    result = round(steps * step, decimals)
    return result


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

    async def get_max_qty(self, symbol: str) -> float:
        """Sembolün maksimum işlem miktarını döner (LOT_SIZE.maxQty).

        Bu değer, tek bir emir için izin verilen maksimum miktardır.
        STOP_MARKET/TAKE_PROFIT_MARKET gibi algo emirlerinde de aynı
        limit uygulanır. Aşılması -4005 "Quantity greater than max quantity"
        hatasına yol açar.
        """
        info = await self.get_symbol_info(symbol)
        if not info:
            return 0.0
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                return float(f.get("maxQty", 0.0))
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

    async def get_min_notional(self, symbol: str) -> float:
        """Sembolün minimum notional değerini döner (USDT cinsinden)."""
        info = await self.get_symbol_info(symbol)
        if not info:
            return 5.0
        for f in info.get("filters", []):
            if f["filterType"] == "MIN_NOTIONAL":
                return float(f.get("notional", 5.0))
        return 5.0

    async def validate_min_notional(
        self, symbol: str, amount: float, price: float
    ) -> float:
        """amount × price >= minNotional kontrolü. Geçemezse 0.0 döner."""
        if amount <= 0 or price <= 0:
            return 0.0
        notional = amount * price
        min_notional = await self.get_min_notional(symbol)
        if notional < min_notional:
            log.warning(
                "[MINNOTIONAL] %s notional=%.2f < min_notional=%.2f "
                "(amount=%.8f, price=%.2f)",
                symbol,
                notional,
                min_notional,
                amount,
                price,
            )
            return 0.0
        return amount

    async def estimate_market_price(self, symbol: str) -> float:
        """MARKET emri için tahmini işlem fiyatı (mark price)."""
        r = await self.get(f"/fapi/v1/ticker/price?symbol={symbol}")
        if r.is_err:
            return 0.0
        return float(r.value.get("price", 0))

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

    async def _emergency_post(self, endpoint: str, params: dict) -> Result[dict]:
        """Circuit breaker'ı BYPASS eden acil durum POST isteği.

        Acil market close/force close gibi kritik işlemler için kullanılır.
        Circuit breaker açıkken bile isteğin geçmesini sağlar.
        Retry/log mekanizması aynıdır, sadece circuit breaker atlanır.
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
                            "[EMERGENCY-POST] %s → %s (attempt %d/%d, %.1fs backoff)",
                            endpoint,
                            last_error,
                            attempt + 1,
                            rc.max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                raise Exception(last_error or "unknown HTTP error")

        try:
            result = await _do_post()
            return Result.ok(result)
        except Exception as e:
            return Result.fail(str(e))

    @staticmethod
    def _parse_error_code(error_msg: str) -> str:
        """Binance hata mesajından hata kodunu çıkar.

        Örn: "HTTP 400: {"code":-4005,"msg":"Quantity greater than max quantity."}"
        → "-4005"
        """
        if not error_msg:
            return ""
        try:
            # "{...}" kısmını bul
            brace_start = error_msg.find("{")
            if brace_start >= 0:
                import json as _json

                parsed = _json.loads(error_msg[brace_start:])
                return str(parsed.get("code", ""))
        except (json.JSONDecodeError, Exception):
            pass
        # Regex'siz fallback: -XXXX kalıbını ara
        import re as _re

        m = _re.search(r'"code"\s*:\s*(-?\d+)', error_msg)
        if m:
            return m.group(1)
        # "max quantity" gibi metin bazlı
        if (
            "max quantity" in error_msg.lower()
            or "max_market_order_qty" in error_msg.lower()
        ):
            return "-4005"
        return ""

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
                return float(asset.get("availableBalance", 0))
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
        Precision uygular, filtrelerden gecer, demo API fallback yapar.
        """
        step = await self.get_step_size(symbol)
        rounded_qty = await self.apply_amount_precision(symbol, qty)
        valid_qty = await self.validate_min_amount(symbol, rounded_qty)
        if valid_qty <= 0:
            log.warning("[MARKET] %s qty=%.8f minQty altinda, iptal", symbol, qty)
            return {"_status": "REJECTED"}

        # MIN_NOTIONAL kontrolü entry_manager._bump_to_min_notional() tarafından
        # yapılıyor. Burada tekrar kontrol etmek farklı anlık fiyat nedeniyle
        # yanlış {} dönmesine sebep oluyordu — emir hiç gitmiyordu.

        decimals = max(_get_precision_places(step), 8)
        qty_str = f"{valid_qty:.{decimals}f}".rstrip("0").rstrip(".")
        if not qty_str or qty_str == "0":
            log.warning("[MARKET] %s qty format hatasi: %s", symbol, qty_str)
            return {"_status": "REJECTED"}

        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": qty_str,
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        r = await self.post("/fapi/v1/order", params)
        if r.is_err:
            log.warning("[MARKET] %s MARKET hatasi: %s", symbol, r.error)
            return {"_status": "REQUEST_SENT", "error": r.error}
        result = r.value
        if result.get("orderId") or result.get("id"):
            result["_status"] = "EXECUTION_CONFIRMED"
            return result
        # Demo API: responder bazen orderId dönmez, 1 sn bekle sonra dene
        for attempt in range(2):
            await asyncio.sleep(0.5)
            try:
                orders = await self.get_open_orders(symbol)
                for o in orders if isinstance(orders, list) else []:
                    if (
                        o.get("symbol") == symbol
                        and o.get("side", "").upper() == side.upper()
                    ):
                        if o.get("type", "").upper() == "MARKET":
                            o["_status"] = "EXECUTION_CONFIRMED"
                            return o
                        if o.get("origType", "").upper() == "MARKET":
                            o["_status"] = "EXECUTION_CONFIRMED"
                            return o
            except Exception:
                pass
        log.warning(
            "[MARKET] %s POST OK fakat orderId bulunamadi — demo API gecikmesi. resp=%s",
            symbol,
            {
                k: v
                for k, v in result.items()
                if k in ("clientOrderId", "status", "executedQty")
            },
        )
        result["_status"] = "ORDER_ACKNOWLEDGED"
        return result

    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        stop_price: float,
        client_id: str = "",
        close_position: bool = False,
    ) -> dict:
        """STOP_MARKET emri — Algo endpoint (/fapi/v1/algoOrder) kullanir.

        İki mod:
          - close_position=False (default): reduceOnly=True + quantity ile.
            Birden fazla emre izin verir. Ancak qty > LOT_SIZE.maxQty ise
            -4005 "Quantity greater than max quantity" hatası alınabilir.
          - close_position=True: quantity olmadan closePosition=true ile.
            Max-qty limitinden muaftır. Pozisyonun tamamını kapatır.
            SADECE tek bir emre izin verir (ikincisi reddedilir).

        Hata durumunda dönen dict içinde _error_code alanı olabilir:
          "-4005" → Quantity greater than max quantity (miktar küçültülmeli)
        """
        if close_position:
            # closePosition=True modu: quantity gönderme, max-qty limiti yok
            rounded_price = await self.apply_price_precision(symbol, stop_price)
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "STOP_MARKET",
                "algoType": "CONDITIONAL",
                "workingType": "MARK_PRICE",
                "triggerPrice": str(rounded_price),
                "closePosition": "true",
                "timeInForce": "GTC",
                "newClientOrderId": client_id
                or f"sl_close_{symbol}_{int(time.time())}",
            }
            r = await self.post("/fapi/v1/algoOrder", params)
            if r.is_err:
                log.warning(
                    "[SL] %s closePosition STOP_MARKET hatasi: %s", symbol, r.error
                )
                return {"_error_code": self._parse_error_code(r.error)}
            result = r.value
            if result.get("algoId") or result.get("orderId") or result.get("id"):
                return result
            return result

        # reduceOnly modu: quantity ile
        step = await self.get_step_size(symbol)
        rounded_qty = await self.apply_amount_precision(symbol, qty)
        valid_qty = await self.validate_min_amount(symbol, rounded_qty)
        if valid_qty <= 0:
            log.warning("[SL] %s qty=%.8f minQty altinda, iptal", symbol, qty)
            return {}

        rounded_price = await self.apply_price_precision(symbol, stop_price)
        valid_qty = await self.validate_min_notional(symbol, valid_qty, rounded_price)
        if valid_qty <= 0:
            log.warning("[SL] %s qty=%.8f minNotional altinda, iptal", symbol, qty)
            return {}
        decimals = max(_get_precision_places(step), 8)
        qty_str = f"{valid_qty:.{decimals}f}".rstrip("0").rstrip(".")

        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "STOP_MARKET",
            "algoType": "CONDITIONAL",
            "workingType": "MARK_PRICE",
            "quantity": qty_str,
            "triggerPrice": str(rounded_price),
            "reduceOnly": "true",
            "timeInForce": "GTC",
            "newClientOrderId": client_id or f"sl_{symbol}_{int(time.time())}",
        }
        r = await self.post("/fapi/v1/algoOrder", params)
        if r.is_err:
            log.warning("[SL] %s STOP_MARKET hatasi: %s", symbol, r.error)
            return {"_error_code": self._parse_error_code(r.error)}
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
        self,
        symbol: str,
        side: str,
        qty: float,
        stop_price: float,
        client_id: str = "",
        close_position: bool = False,
    ) -> dict:
        """TAKE_PROFIT_MARKET emri — Algo endpoint (/fapi/v1/algoOrder) kullanir.

        İki mod (place_stop_order ile aynı):
          - close_position=False (default): reduceOnly=True + quantity ile.
          - close_position=True: quantity olmadan closePosition=true ile.

        Hata durumunda dönen dict içinde _error_code alanı olabilir:
          "-4005" → Quantity greater than max quantity.
        """
        if close_position:
            rounded_price = await self.apply_price_precision(symbol, stop_price)
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "TAKE_PROFIT_MARKET",
                "algoType": "CONDITIONAL",
                "workingType": "MARK_PRICE",
                "triggerPrice": str(rounded_price),
                "closePosition": "true",
                "timeInForce": "GTC",
                "newClientOrderId": client_id
                or f"tp_close_{symbol}_{int(time.time())}",
            }
            r = await self.post("/fapi/v1/algoOrder", params)
            if r.is_err:
                log.warning(
                    "[TP] %s closePosition TAKE_PROFIT_MARKET hatasi: %s",
                    symbol,
                    r.error,
                )
                return {"_error_code": self._parse_error_code(r.error)}
            result = r.value
            if result.get("algoId") or result.get("orderId") or result.get("id"):
                return result
            return result

        step = await self.get_step_size(symbol)
        rounded_qty = await self.apply_amount_precision(symbol, qty)
        valid_qty = await self.validate_min_amount(symbol, rounded_qty)
        if valid_qty <= 0:
            log.warning("[TP] %s qty=%.8f minQty altinda, iptal", symbol, qty)
            return {}

        rounded_price = await self.apply_price_precision(symbol, stop_price)
        valid_qty = await self.validate_min_notional(symbol, valid_qty, rounded_price)
        if valid_qty <= 0:
            log.warning("[TP] %s qty=%.8f minNotional altinda, iptal", symbol, qty)
            return {}
        decimals = max(_get_precision_places(step), 8)
        qty_str = f"{valid_qty:.{decimals}f}".rstrip("0").rstrip(".")

        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "TAKE_PROFIT_MARKET",
            "algoType": "CONDITIONAL",
            "workingType": "MARK_PRICE",
            "quantity": qty_str,
            "triggerPrice": str(rounded_price),
            "reduceOnly": "true",
            "timeInForce": "GTC",
            "newClientOrderId": client_id or f"tp_{symbol}_{int(time.time())}",
        }
        r = await self.post("/fapi/v1/algoOrder", params)
        if r.is_err:
            log.warning("[TP] %s TAKE_PROFIT_MARKET hatasi: %s", symbol, r.error)
            return {"_error_code": self._parse_error_code(r.error)}
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

    async def place_market_order_priority(
        self, symbol: str, side: str, qty: float, reduce_only: bool = False
    ) -> dict:
        """ACİL DURUM: Circuit breaker'ı BYPASS eden MARKET emri.

        place_market_order() ile aynı mantık, ancak circuit breaker
        kontrolünü atlar. SL/TP denemeleri circuit breaker'ı açtıysa
        bile acil kapanış emrinin geçmesini sağlar.

        Kullanım: YALNIZCA acil durum / emergency close senaryolarında.
        """
        step = await self.get_step_size(symbol)
        rounded_qty = await self.apply_amount_precision(symbol, qty)
        valid_qty = await self.validate_min_amount(symbol, rounded_qty)
        if valid_qty <= 0:
            log.warning(
                "[EMERGENCY] %s qty=%.8f minQty altinda, closePosition deneniyor...",
                symbol,
                qty,
            )
            # qty çok küçükse closePosition=True dene
            mkt_side = side.upper()
            pos_side = "long" if side.upper() == "SELL" else "short"
            forced = await self.place_force_close_order(symbol, mkt_side, pos_side)
            if forced:
                return {"_status": "EXECUTION_CONFIRMED", "closePosition": True}
            return {"_status": "REJECTED"}

        decimals = max(_get_precision_places(step), 8)
        qty_str = f"{valid_qty:.{decimals}f}".rstrip("0").rstrip(".")
        if not qty_str or qty_str == "0":
            return {"_status": "REJECTED"}

        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": qty_str,
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        # _emergency_post circuit breaker'ı atlar
        r = await self._emergency_post("/fapi/v1/order", params)
        if r.is_err:
            log.warning("[EMERGENCY] %s MARKET hata (CB bypass): %s", symbol, r.error)
            return {"_status": "REQUEST_SENT", "error": r.error}
        result = r.value
        if result.get("orderId") or result.get("id"):
            result["_status"] = "EXECUTION_CONFIRMED"
            return result
        result["_status"] = "ORDER_ACKNOWLEDGED"
        return result

    async def place_force_close_order(
        self, symbol: str, mkt_side: str, position_side: str
    ) -> bool:
        """closePosition=true ile STOP_MARKET emri gonderir.

        Miktar gonderilmez — LOT_SIZE/MIN_NOTIONAL filtrelerinden muaftir.
        SELL STOP_MARKET fiyat <= trigger'da, BUY STOP_MARKET fiyat >= trigger'da
        tetiklenir. Aninda tetiklenmesi icin trigger, LONG kapanisinda (SELL)
        mevcut fiyatin HEMEN USTUNE, SHORT kapanisinda (BUY) HEMEN ALTINA konur.
        Bu yontem, dust (minNotional alti) pozisyonlari kapatmak icin
        place_market_order'daki minQty/minNotional engelini asar.

        Returns: True if accepted, False otherwise.
        """
        try:
            cur_price = await self.estimate_market_price(symbol)
            if cur_price <= 0:
                log.warning("[FORCE_CLOSE] %s fiyat alinamadi", symbol)
                return False
            # Aninda trigger icin dogru yonde, kucuk bir marjla koy
            # (PERCENT_PRICE filtreleri asiri uzak trigger'lari reddeder)
            if position_side == "long":
                trigger_price = await self.apply_price_precision(
                    symbol, cur_price * 1.01
                )
            else:
                trigger_price = await self.apply_price_precision(
                    symbol, cur_price * 0.99
                )
            params = {
                "symbol": symbol,
                "side": mkt_side.upper(),
                "type": "STOP_MARKET",
                "algoType": "CONDITIONAL",
                "workingType": "MARK_PRICE",
                "triggerPrice": str(trigger_price),
                "closePosition": "true",
                "timeInForce": "GTC",
                "newClientOrderId": f"force_close_{symbol}_{int(time.time())}",
            }
            r = await self._emergency_post("/fapi/v1/algoOrder", params)
            if r.is_err:
                log.warning("[FORCE_CLOSE] %s basarisiz: %s", symbol, r.error)
                return False
            log.info(
                "[FORCE_CLOSE] %s closePosition emri kabul edildi (trigger=%s)",
                symbol,
                trigger_price,
            )
            return True
        except Exception as e:
            log.warning("[FORCE_CLOSE] %s hata: %s", symbol, e)
            return False
