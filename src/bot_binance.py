"""
bot_binance.py — NEXUS V4
──────────────────────────
İmzalı Binance REST çağrıları: GET / POST / DELETE + retry + semaphore.
LiveTradingBot instance state'ini bilmez — bağımsız test edilebilir.

Orijinal konum: sonnet/src/main.py
  _fetch_binance_signed      satır 489
  _fetch_binance_signed_post satır 534
  _fetch_binance_signed_delete satır 964
  _cancel_order_by_id        satır 913
  _get_open_orders_async     satır 580
  _get_order_type / _get_order_price / _safe_order_timestamp (statik)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("nexus.live")


class BinanceRESTClient:
    """
    İmzalı Binance Futures REST istemcisi.
    Rate limiter + semaphore + retry zinciri içerir.
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
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url
        self._rate_limiter = rate_limiter
        self._semaphore = semaphore

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
    # Transport katmanı
    # ─────────────────────────────────────────────────────────────────

    async def get(self, endpoint: str, params: str = "", max_retries: int = 3) -> dict:
        """İmzalı GET isteği — retry + backoff + semaphore."""
        await self._rate_limiter.acquire()
        async with self._semaphore:
            key = self._api_key
            secret = self._api_secret
            last_error = None
            for attempt in range(max_retries):
                ts = int(time.time() * 1000)
                full_params = f"{params}&timestamp={ts}" if params else f"timestamp={ts}"
                sig = hmac.new(secret.encode(), full_params.encode(), hashlib.sha256).hexdigest()
                url = f"{self._base_url}{endpoint}?{full_params}&signature={sig}"
                req = urllib.request.Request(url, headers={"X-MBX-APIKEY": key})
                loop = asyncio.get_running_loop()
                try:
                    raw = await loop.run_in_executor(
                        None,
                        lambda req=req: urllib.request.urlopen(req).read().decode(),
                    )
                    return json.loads(raw)
                except urllib.error.HTTPError as e:
                    body = e.read().decode() if hasattr(e, "read") else str(e)
                    last_error = f"HTTP {e.code}: {body[:200]}"
                    log.warning(
                        "[HTTP] %s → %s (attempt %d/%d, url=%s)",
                        endpoint,
                        last_error,
                        attempt + 1,
                        max_retries,
                        url[:120],
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
                except Exception as e:
                    last_error = str(e)[:200]
                    log.warning(
                        "[HTTP] %s → %s (attempt %d/%d)",
                        endpoint,
                        last_error,
                        attempt + 1,
                        max_retries,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
            raise Exception(last_error or "unknown HTTP error")

    async def post(self, endpoint: str, params: dict, max_retries: int = 3) -> dict:
        """İmzalı POST isteği — retry + backoff + semaphore."""
        await self._rate_limiter.acquire()
        async with self._semaphore:
            key = self._api_key
            secret = self._api_secret
            last_error = None
            for attempt in range(max_retries):
                params["timestamp"] = int(time.time() * 1000)
                query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                sig = hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
                query_string += f"&signature={sig}"
                url = f"{self._base_url}{endpoint}"
                data = query_string.encode()
                req = urllib.request.Request(url, data=data, headers={"X-MBX-APIKEY": key})
                loop = asyncio.get_running_loop()
                try:
                    raw = await loop.run_in_executor(
                        None,
                        lambda req=req: urllib.request.urlopen(req).read().decode(),
                    )
                    return json.loads(raw)
                except urllib.error.HTTPError as e:
                    body = e.read().decode() if hasattr(e, "read") else str(e)
                    last_error = f"HTTP {e.code}: {body[:200]}"
                    log.warning(
                        "[HTTP-POST] %s → %s (attempt %d/%d)",
                        endpoint,
                        last_error,
                        attempt + 1,
                        max_retries,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
                except Exception as e:
                    last_error = str(e)[:200]
                    log.warning(
                        "[HTTP-POST] %s → %s (attempt %d/%d)",
                        endpoint,
                        last_error,
                        attempt + 1,
                        max_retries,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
            raise Exception(last_error or "unknown HTTP error")

    async def delete(self, endpoint: str, params: str = "") -> dict:
        """İmzalı DELETE isteği."""
        await self._rate_limiter.acquire()
        async with self._semaphore:
            key = self._api_key
            secret = self._api_secret
            ts = int(time.time() * 1000)
            full_params = f"{params}&timestamp={ts}" if params else f"timestamp={ts}"
            sig = hmac.new(secret.encode(), full_params.encode(), hashlib.sha256).hexdigest()
            url = f"{self._base_url}{endpoint}?{full_params}&signature={sig}"
            req = urllib.request.Request(url, headers={"X-MBX-APIKEY": key}, method="DELETE")
            loop = asyncio.get_running_loop()
            try:
                raw = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req).read().decode())
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                log.debug("DELETE %s → HTTP %s: %s", endpoint, e.code, body)
                raise Exception(f"HTTP {e.code}: {body}") from e

    # ─────────────────────────────────────────────────────────────────
    # Emir sorgu / iptal
    # ─────────────────────────────────────────────────────────────────

    async def get_open_orders(self, symbol: str) -> list:
        """Sembol için açık normal emirleri döner (list)."""
        try:
            result = await self.get("/fapi/v1/openOrders", f"symbol={symbol}")
            return result if isinstance(result, list) else []
        except Exception as e:
            log.error("[ORDERS] Açık emirler alınamadı %s: %s", symbol.ljust(12), e)
            return []

    async def get_all_orders(self, symbol: str) -> list:
        """Normal + algo emirleri birleşik olarak döner."""
        orders = await self.get_open_orders(symbol)
        try:
            algo_raw = await self.get("/fapi/v1/openAlgoOrders", f"symbol={symbol}")
            if isinstance(algo_raw, list):
                orders.extend(algo_raw)
        except Exception as e:
            log.debug("[ORDERS] algoOrders alınamadı %s (önemsiz): %s", symbol.ljust(12), e)
        return orders

    async def get_balance(self) -> float:
        """Futures cüzdan bakiyesini döner (USDT)."""
        try:
            result = await self.get("/fapi/v2/account")
            for asset in result.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("walletBalance", 0))
            return 0.0
        except Exception as e:
            log.warning("[BALANCE] Bakiye alınamadı: %s", e)
            return 0.0

    async def cancel_order(
        self,
        order_id: Any,
        symbol: str,
        reason: str = "",
        is_algo: bool = False,
    ) -> bool:
        """Tek bir emri Binance REST API ile iptal et (DELETE)."""
        if is_algo:
            try:
                params = f"symbol={symbol}&algoId={order_id}"
                await self.delete("/fapi/v1/algoOrder", params)
                log.info("🧹 İPTAL (algo) | %s algoId=%s reason=%s", symbol.ljust(12), order_id, reason)
                return True
            except Exception as e:
                err = str(e)
                if "Unknown order" in err or "-2011" in err:
                    log.info("🧹 İPTAL (algo) | %s algoId=%s zaten yok (ok)", symbol.ljust(12), order_id)
                    return True
                log.warning("🧹 İPTAL hatası (algo) %s algoId=%s: %s", symbol.ljust(12), order_id, e)
                return False
        else:
            try:
                params = f"symbol={symbol}&orderId={order_id}"
                await self.delete("/fapi/v1/order", params)
                log.info("🧹 İPTAL | %s orderId=%s reason=%s", symbol.ljust(12), order_id, reason)
                return True
            except Exception as e:
                err = str(e)
                if "Unknown order" in err or "-2011" in err:
                    log.info("🧹 İPTAL | %s orderId=%s zaten yok (ok)", symbol.ljust(12), order_id)
                    return True
                # Algo endpoint'ini dene
                try:
                    params = f"symbol={symbol}&algoId={order_id}"
                    await self.delete("/fapi/v1/algoOrder", params)
                    log.info("🧹 İPTAL (algo fallback) | %s algoId=%s reason=%s", symbol.ljust(12), order_id, reason)
                    return True
                except Exception as e2:
                    log.warning(
                        "🧹 İPTAL hatası %s orderId=%s (normal+algo): %s / %s",
                        symbol.ljust(12).ljust(12),
                        order_id,
                        e,
                        e2,
                    )
                    return False
