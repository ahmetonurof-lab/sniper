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
        self._exchange_info: dict | None = None
        self._exchange_info_ts: float = 0.0
        self._symbol_info: dict[str, dict] = {}

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
        data = await self.get("/fapi/v1/exchangeInfo")
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
                full_params = (
                    f"{params}&timestamp={ts}" if params else f"timestamp={ts}"
                )
                sig = hmac.new(
                    secret.encode(), full_params.encode(), hashlib.sha256
                ).hexdigest()
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
                sig = hmac.new(
                    secret.encode(), query_string.encode(), hashlib.sha256
                ).hexdigest()
                query_string += f"&signature={sig}"
                url = f"{self._base_url}{endpoint}"
                data = query_string.encode()
                req = urllib.request.Request(
                    url, data=data, headers={"X-MBX-APIKEY": key}
                )
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
            sig = hmac.new(
                secret.encode(), full_params.encode(), hashlib.sha256
            ).hexdigest()
            url = f"{self._base_url}{endpoint}?{full_params}&signature={sig}"
            req = urllib.request.Request(
                url, headers={"X-MBX-APIKEY": key}, method="DELETE"
            )
            loop = asyncio.get_running_loop()
            try:
                raw = await loop.run_in_executor(
                    None, lambda: urllib.request.urlopen(req).read().decode()
                )
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
            log.debug(
                "[ORDERS] algoOrders alınamadı %s (önemsiz): %s", symbol.ljust(12), e
            )
        return orders

    async def get_balance(self) -> float:
        try:
            result = await self.get("/fapi/v2/account")
            for asset in result.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("walletBalance", 0))
            return 0.0
        except Exception as e:
            log.warning("[BALANCE] Bakiye alınamadı: %s", e)
            return 0.0

    async def get_positions(self) -> list[dict]:
        try:
            raw = await self.get("/fapi/v2/account")
            return [
                p
                for p in raw.get("positions", [])
                if float(p.get("positionAmt", 0)) != 0
            ]
        except Exception as e:
            log.warning("[POSITIONS] Pozisyonlar alınamadı: %s", e)
            return []

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

        try:
            result = await self.post("/fapi/v1/order", params)
            if result.get("orderId") or result.get("id"):
                return result
            # Demo API: orderId dönmezse GET ile bul
            import asyncio as _asyncio

            await _asyncio.sleep(0.5)
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
        except Exception as e:
            log.warning("[MARKET] %s MARKET hatasi: %s", symbol, e)
            return {}

    async def place_stop_order(
        self, symbol: str, side: str, qty: float, stop_price: float, client_id: str = ""
    ) -> dict:
        """
        STOP_MARKET emri — Algo endpoint (/fapi/v1/algoOrder) kullanir.
        closePosition=True ile reduceOnly yerine yeni API.
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
            "closePosition": "true",
            "timeInForce": "GTE_GTC",
            "newClientOrderId": client_id or f"sl_{symbol}_{int(time.time())}",
        }
        try:
            result = await self.post("/fapi/v1/algoOrder", params)
            if result.get("algoId") or result.get("orderId") or result.get("id"):
                return result
            # Demo API fallback
            import asyncio as _asyncio

            await _asyncio.sleep(0.5)
            try:
                orders = await self.get("/fapi/v1/openAlgoOrders", f"symbol={symbol}")
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
        except Exception as e:
            log.warning("[SL] %s STOP_MARKET hatasi: %s", symbol, e)
            return {}

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
            "closePosition": "true",
            "timeInForce": "GTE_GTC",
            "newClientOrderId": client_id or f"tp_{symbol}_{int(time.time())}",
        }
        try:
            result = await self.post("/fapi/v1/algoOrder", params)
            if result.get("algoId") or result.get("orderId") or result.get("id"):
                return result
            # Demo API fallback
            import asyncio as _asyncio

            await _asyncio.sleep(0.5)
            try:
                orders = await self.get("/fapi/v1/openAlgoOrders", f"symbol={symbol}")
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
        except Exception as e:
            log.warning("[TP] %s TAKE_PROFIT_MARKET hatasi: %s", symbol, e)
            return {}

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
                log.info(
                    "🧹 İPTAL (algo) | %s algoId=%s reason=%s",
                    symbol.ljust(12),
                    order_id,
                    reason,
                )
                return True
            except Exception as e:
                err = str(e)
                if "Unknown order" in err or "-2011" in err:
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
                    e,
                )
                return False
        else:
            try:
                params = f"symbol={symbol}&orderId={order_id}"
                await self.delete("/fapi/v1/order", params)
                log.info(
                    "🧹 İPTAL | %s orderId=%s reason=%s",
                    symbol.ljust(12),
                    order_id,
                    reason,
                )
                return True
            except Exception as e:
                err = str(e)
                if "Unknown order" in err or "-2011" in err:
                    log.info(
                        "🧹 İPTAL | %s orderId=%s zaten yok (ok)",
                        symbol.ljust(12),
                        order_id,
                    )
                    return True
                # Algo endpoint'ini dene
                try:
                    params = f"symbol={symbol}&algoId={order_id}"
                    await self.delete("/fapi/v1/algoOrder", params)
                    log.info(
                        "🧹 İPTAL (algo fallback) | %s algoId=%s reason=%s",
                        symbol.ljust(12),
                        order_id,
                        reason,
                    )
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

    # ─────────────────────────────────────────────────────────────────
    # Listen Key (User Data Stream) — imzasız, sadece API Key header
    # ─────────────────────────────────────────────────────────────────

    def _unsigned_post(self, endpoint: str, data: bytes | None = None) -> dict:
        url = f"{self._base_url}{endpoint}"
        req = urllib.request.Request(
            url, data=data, headers={"X-MBX-APIKEY": self._api_key}, method="POST"
        )
        raw = urllib.request.urlopen(req).read().decode()
        return json.loads(raw)

    def _unsigned_put(self, endpoint: str) -> dict:
        url = f"{self._base_url}{endpoint}"
        req = urllib.request.Request(
            url, headers={"X-MBX-APIKEY": self._api_key}, method="PUT"
        )
        raw = urllib.request.urlopen(req).read().decode()
        return json.loads(raw)

    async def get_listen_key(self) -> str:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._unsigned_post("/fapi/v1/listenKey")
        )
        key = result.get("listenKey", "")
        if not key:
            raise Exception("Listen key alinamadi")
        log.info("[LISTEN_KEY] Yeni listen key olusturuldu")
        return key

    def renew_listen_key(self, listen_key: str) -> None:
        try:
            self._unsigned_put(f"/fapi/v1/listenKey?listenKey={listen_key}")
            log.debug("[LISTEN_KEY] Key yenilendi")
        except urllib.error.HTTPError as e:
            body = e.read().decode() if hasattr(e, "read") else ""
            log.warning("[LISTEN_KEY] Yenileme hatasi HTTP %s: %s", e.code, body[:100])
