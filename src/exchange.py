"""
binance_http.py — NEXUS V2
──────────────────────────
Binance Futures için ham HTTP istemcisi (ccxt bağımlılığı olmadan).

Tüm REST endpoint'leri buradan geçer:
  • İmzalı / imzasız GET, POST, DELETE
  • Market bilgisi (exchangeInfo) — önbellekli
  • Kline (OHLCV) çekme
  • Precision yardımcıları (tick size, step size, min qty)
  • Emir gönderme, iptal, pozisyon sorgulama

Kullanım
────────
    from binance_http import BinanceHTTPClient

    client = BinanceHTTPClient(
        api_key="...",
        api_secret="...",
        base_url="https://demo-fapi.binance.com",
    )
    klines = client.get_klines("BTCUSDT", "1d", 150)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from threading import Lock
from typing import Any

log = logging.getLogger("nexus.http")

# ═══════════════════════════════════════════════════════════
# Yardımcılar
# ═══════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════
# Ana HTTP istemcisi
# ═══════════════════════════════════════════════════════════


class BinanceHTTPClient:
    """
    Binance Futures REST API için ham HTTP istemcisi.

    Parametreler
    ------------
    api_key    : Binance API anahtarı
    api_secret : Binance API secret
    base_url   : Futures REST base URL (testnet için demo-fapi.binance.com)
    timeout    : HTTP istek zaman aşımı (saniye)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://fapi.binance.com",
        timeout: int = 15,
        portfolio_margin: bool = False,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Portfolio Margin modu: demo-fapi.binance.com /papi/v1/um/* endpoint'leri kullanır
        self.portfolio_margin = portfolio_margin

        # ── Önbellek ──
        self._exchange_info: dict | None = None
        self._exchange_info_ts: float = 0.0
        self._symbol_info: dict[str, dict] = {}
        self._info_lock = Lock()

    # Portfolio Margin endpoint haritası
    _PM_ENDPOINT_MAP: dict[str, str | None] = {
        "/fapi/v1/order": "/papi/v1/um/order",
        "/fapi/v1/algoOrder": "/papi/v1/um/conditional/order",
        "/fapi/v1/marginType": None,  # PM'de desteklenmiyor
        "/fapi/v1/leverage": "/papi/v1/um/leverage",
        "/fapi/v2/positionRisk": "/papi/v1/um/positionRisk",
        "/fapi/v2/account": "/papi/v1/account",
        "/fapi/v1/openOrders": "/papi/v1/um/openOrders",
        "/fapi/v1/openAlgoOrders": "/papi/v1/um/conditional/openOrders",
        "/fapi/v1/order/cancelReplace": "/papi/v1/um/order/cancelReplace",
    }

    def _ep(self, fapi_path: str) -> str | None:
        """
        Portfolio Margin modunda /fapi/v1/* ve /fapi/v2/* yollarını
        /papi/v1/um/* karşılığına çevirir.
        Desteklenmeyen endpointler (marginType) None döner.
        """
        if not self.portfolio_margin:
            return fapi_path
        return self._PM_ENDPOINT_MAP.get(fapi_path, fapi_path)

    # ── İmza ──────────────────────────────────────

    def _sign(self, params: dict[str, Any]) -> str:
        query = urllib.parse.urlencode(params)
        return hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    # ── HTTP istek ────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        log_errors: bool = True,
        max_retries: int = 0,
    ) -> Any:
        """
        Senkron HTTP isteği. Async context'te run_in_executor ile çağrılmalı.

        Dönüş: JSON parse edilmiş dict veya list (endpoint'e göre değişir)
        Hata: HTTPError veya JSONDecodeError fırlatır

        Parametreler
        ------------
        max_retries : 429 (rate limit) ve URLError (timeout) için tekrar sayısı.
                      Diğer HTTP hatalarında (4xx/5xx) retry yapılmaz.
        """
        params = dict(params or {})

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            if not self.base_url.startswith("https://demo-fapi"):
                params["recvWindow"] = 10000
            params["signature"] = self._sign(params)

        url = f"{self.base_url}{path}"

        if method == "GET":
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
            req = urllib.request.Request(url, method="GET")
        elif method == "DELETE":
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
            req = urllib.request.Request(url, method="DELETE")
        else:  # POST
            encoded_body = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(url, data=encoded_body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

        req.add_header("X-MBX-APIKEY", self.api_key)

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    data = res.read().decode().strip()
                    if not data:
                        return {}
                    # Portfolio Margin bazı endpointler JSON değil düz "ok" döner
                    if data.lower() == "ok":
                        return {"status": "ok"}
                    return json.loads(data)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode() if e.fp else ""

                # ── Binance hata body'sini parse et ──
                bn_code = 0
                bn_msg = ""
                try:
                    err_json = json.loads(err_body) if err_body else {}
                    bn_code = err_json.get("code", 0)
                    bn_msg = err_json.get("msg", "")
                except (json.JSONDecodeError, ValueError):
                    pass

                # ── FATAL: retry yapma, iş mantığı hatası ──
                _fatal_codes = {-1013, -2010, -2015, -2019, -4061}
                if bn_code in _fatal_codes:
                    log.error(
                        "[HTTP] FATAL %s %s → %d | Binance[%d] %s",
                        method,
                        path,
                        e.code,
                        bn_code,
                        bn_msg,
                    )
                    raise urllib.error.HTTPError(
                        e.url,
                        e.code,
                        f"{e.reason} [Binance {bn_code}: {bn_msg}]",
                        e.hdrs,
                        e.fp,
                    ) from None

                # ── 429 veya -1003 Rate Limit ──
                if (e.code == 429 or bn_code == -1003) and attempt < max_retries:
                    retry_after = e.hdrs.get("Retry-After") if e.hdrs else None
                    if retry_after:
                        try:
                            wait_s = float(retry_after)
                        except ValueError:
                            wait_s = 5.0
                    else:
                        wait_s = 2.0 * (attempt + 1)
                    log.warning(
                        "[HTTP] RATE LIMIT %s %s → %.1fs bekle (attempt %d/%d)",
                        method,
                        path,
                        wait_s,
                        attempt + 1,
                        max_retries + 1,
                    )
                    time.sleep(wait_s)
                    last_error = e
                    continue

                # ── 5xx Server Error → retry ──
                if 500 <= e.code < 600 and attempt < max_retries:
                    wait_s = 2.0 * (attempt + 1)
                    log.warning(
                        "[HTTP] 5xx %s %s → %d | Binance[%d] %s | %.1fs bekle (attempt %d/%d)",
                        method,
                        path,
                        e.code,
                        bn_code,
                        bn_msg,
                        wait_s,
                        attempt + 1,
                        max_retries + 1,
                    )
                    time.sleep(wait_s)
                    last_error = e
                    continue

                # ── Diğer 4xx: retry yap (geçici olabilir) ──
                if 400 <= e.code < 500 and attempt < max_retries:
                    wait_s = 1.5 * (attempt + 1)
                    log.warning(
                        "[HTTP] 4xx %s %s → %d | Binance[%d] %s | %.1fs bekle (attempt %d/%d)",
                        method,
                        path,
                        e.code,
                        bn_code,
                        bn_msg,
                        wait_s,
                        attempt + 1,
                        max_retries + 1,
                    )
                    time.sleep(wait_s)
                    last_error = e
                    continue

                if log_errors:
                    log.error(
                        "[HTTP] %s %s → %d %s | Binance[%d] %s | body=%.300s | params=%s",
                        method,
                        path,
                        e.code,
                        e.reason,
                        bn_code,
                        bn_msg,
                        err_body,
                        {k: v for k, v in params.items() if k != "signature"},
                    )
                raise urllib.error.HTTPError(
                    e.url,
                    e.code,
                    f"{e.reason} [Binance {bn_code}: {bn_msg}]",
                    e.hdrs,
                    e.fp,
                ) from None
            except (urllib.error.URLError, OSError) as e:
                last_error = e
                if attempt < max_retries:
                    wait_s = 1.0 * (attempt + 1)
                    log.warning(
                        "[HTTP] URLError %s %s → %.1fs bekle (attempt %d/%d): %s",
                        method,
                        path,
                        wait_s,
                        attempt + 1,
                        max_retries + 1,
                        e,
                    )
                    time.sleep(wait_s)
                    continue
                raise

        # Tüm retry'ler tükendi
        if isinstance(last_error, urllib.error.HTTPError):
            raise last_error
        raise last_error if last_error else Exception(f"{method} {path}: max_retries exhausted")

    # ── Exchange Info (önbellekli) ────────────────

    def _load_exchange_info(self, force: bool = False) -> dict:
        """Exchange info'yu yükler, 5 dakika önbellekte tutar."""
        now = time.time()
        with self._info_lock:
            if not force and self._exchange_info and (now - self._exchange_info_ts) < 300:
                return self._exchange_info

            data = self._request("GET", "/fapi/v1/exchangeInfo")
            self._exchange_info = data
            self._exchange_info_ts = now

            # Sembol bilgilerini indeksle
            self._symbol_info.clear()
            for s in data.get("symbols", []):
                self._symbol_info[s["symbol"]] = s

            log.info("[EXCHANGE_INFO] %d sembol yüklendi", len(self._symbol_info))
            return data

    def get_symbol_info(self, symbol: str) -> dict | None:
        """Tek bir sembolün exchange info'sunu döner (önbellekten)."""
        self._load_exchange_info()
        with self._info_lock:
            return self._symbol_info.get(symbol)

    # ── Precision yardımcıları ───────────────────

    def get_tick_size(self, symbol: str) -> float:
        """Sembolün tick size'ını döner (fiyat hassasiyeti)."""
        info = self.get_symbol_info(symbol)
        if not info:
            return 0.0001
        for f in info.get("filters", []):
            if f["filterType"] == "PRICE_FILTER":
                return float(f.get("tickSize", 0.0001))
        return 0.0001

    def get_step_size(self, symbol: str) -> float:
        """Sembolün step size'ını döner (miktar hassasiyeti)."""
        info = self.get_symbol_info(symbol)
        if not info:
            return 0.001
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                return float(f.get("stepSize", 0.001))
        return 0.001

    def get_min_qty(self, symbol: str) -> float:
        """Sembolün minimum işlem miktarını döner."""
        info = self.get_symbol_info(symbol)
        if not info:
            return 0.0
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                return float(f.get("minQty", 0.0))
        return 0.0

    def apply_price_precision(self, symbol: str, price: float) -> float:
        """Fiyatı tick size'a göre yuvarla."""
        if price is None or price == 0:
            return price
        return _round_to_tick(price, self.get_tick_size(symbol))

    def apply_amount_precision(self, symbol: str, amount: float) -> float:
        """Miktarı step size'a göre yuvarla."""
        if amount is None or amount == 0:
            return amount
        return _round_step(amount, self.get_step_size(symbol))

    # ── Kline (OHLCV) ────────────────────────────

    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
        max_retries: int = 2,
    ) -> list[list[float]]:
        """
        OHLCV verisi çeker.

        Dönüş: [[timestamp, open, high, low, close, volume], ...]
        Timestamp milisaniye, diğerleri float.
        """
        raw = self._request(
            "GET",
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
            signed=False,
            max_retries=max_retries,
        )
        # Binance kline: [t, o, h, l, c, v, T, q, n, V, Q, B]
        return [
            [
                int(k[0]),  # timestamp (ms)
                float(k[1]),  # open
                float(k[2]),  # high
                float(k[3]),  # low
                float(k[4]),  # close
                float(k[5]),  # volume
            ]
            for k in raw
        ]

    # ── Emirler ──────────────────────────────────

    def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Emir gönderir.

        Parametreler
        ------------
        symbol     : İşlem çifti (BTCUSDT)
        order_type : MARKET, STOP_MARKET, TAKE_PROFIT_MARKET
        side       : BUY, SELL
        amount     : Miktar (lot)
        price      : Limit fiyat (opsiyonel)
        params     : Ek parametreler (stopPrice, reduceOnly, vs.)
        """
        req_params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": amount,
        }

        if price is not None:
            req_params["price"] = price

        # Ek parametreleri ekle (stopPrice, reduceOnly, timeInForce, ...)
        if params:
            req_params.update(params)

        log.info("[ORDER-DEBUG] %s %s %s req_params=%s", symbol, side, order_type, req_params)
        ep = self._ep("/fapi/v1/order")
        if ep is None:
            log.warning("[ORDER] Portfolio Margin'de bu endpoint desteklenmiyor")
            return {}
        result = self._request("POST", ep, req_params, signed=True, max_retries=2)
        # PM demo endpoint'i "ok" döndürür — open orders'tan son emri al
        if self.portfolio_margin and result.get("status") == "ok":
            import time as _time

            _time.sleep(0.3)
            try:
                orders = self.get_open_orders(symbol)
                if orders:
                    return orders[-1]
            except Exception as e:
                log.exception("[ORDER_PM_EXC] %s get_open_orders başarısız: %s", symbol, e)
        # Demo API orderId dönmüyorsa GET ile çek
        if not result.get("orderId") and not result.get("id"):
            import time as _time

            _time.sleep(0.5)

            def _match_order(o: dict) -> bool:
                return (
                    o.get("symbol") == symbol
                    and (o.get("type") or o.get("orderType") or "") == order_type.upper()
                    and (o.get("side") or "").upper() == side.upper()
                )

            def _get_id(o: dict) -> str | None:
                return o.get("orderId") or o.get("id") or o.get("algoId")

            # 1. Deneme: PM mapping'li get_open_orders
            try:
                orders = self.get_open_orders(symbol)
                log.info("[ORDER_FALLBACK_DEBUG] method=get_open_orders symbol=%s orders=%s", symbol, orders)
                if isinstance(orders, list) and orders:
                    match = next((o for o in orders if _match_order(o)), orders[-1])
                    oid = _get_id(match)
                    log.info(
                        "[ORDER_FALLBACK] match via get_open_orders, fields=%s id=%s",
                        list(match.keys()) if isinstance(match, dict) else "?",
                        oid,
                    )
                    if oid:
                        return match
            except Exception as e:
                log.warning("[ORDER_FALLBACK] get_open_orders başarısız: %s", e)

            # 2. Deneme: PM=True ise standart /fapi/v1/openOrders'ı da dene
            if self.portfolio_margin:
                try:
                    orders = self._request(
                        "GET",
                        "/fapi/v1/openOrders",
                        {"symbol": symbol},
                        signed=True,
                    )
                    log.info("[ORDER_FALLBACK_DEBUG] method=std_openOrders symbol=%s orders=%s", symbol, orders)
                    if isinstance(orders, list) and orders:
                        match = next((o for o in orders if _match_order(o)), orders[-1])
                        oid = _get_id(match)
                        log.info(
                            "[ORDER_FALLBACK] match via std openOrders, fields=%s id=%s",
                            list(match.keys()) if isinstance(match, dict) else "?",
                            oid,
                        )
                        if oid:
                            return match
                except Exception as e:
                    log.warning("[ORDER_FALLBACK] std openOrders başarısız: %s", e)

            log.warning("[ORDER_FALLBACK_EMPTY] %s için hiçbir GET kaynağında eşleşen emir bulunamadı", symbol)

        return result

    def create_algo_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        stop_price: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Algo emir gönderir (STOP / TAKE_PROFIT).
        """
        req_params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "algoType": "CONDITIONAL",
            "workingType": "MARK_PRICE",
        }

        if amount is not None and amount > 0:
            req_params["quantity"] = amount

        req_params["triggerPrice"] = str(stop_price)

        if price is not None:
            req_params["price"] = str(price)

        if params:
            if params.get("closePosition", True) or params.get("reduceOnly", True):
                req_params["closePosition"] = "true"
            cleaned_params = {k: v for k, v in params.items() if k not in ["reduceOnly", "closePosition"]}
            req_params.update(cleaned_params)
        else:
            req_params["closePosition"] = "true"

        ep = self._ep("/fapi/v1/algoOrder")
        if ep is None:
            log.warning("[ALGO-ORDER] Portfolio Margin'de bu endpoint desteklenmiyor")
            return {}
        result = self._request("POST", ep, req_params, signed=True, max_retries=2)
        log.info("[ALGO_RESPONSE] raw=%s", result)

        # ═══ DEMO API: algoId/orderId yoksa GET ile emri bul ═══
        if not result.get("algoId") and not result.get("orderId") and not result.get("id"):
            import time as _time

            _time.sleep(0.5)

            def _match_order(o: dict) -> bool:
                """Gönderdiğimiz emirle GET'ten dönen emri eşleştir."""
                return (
                    o.get("symbol") == symbol
                    and (o.get("type") or o.get("orderType") or "") == order_type.upper()
                    and (o.get("side") or "").upper() == side.upper()
                )

            def _get_id(o: dict) -> str | None:
                return o.get("algoId") or o.get("orderId") or o.get("id") or o.get("algoOrderId")

            # 1. Deneme: PM mapping'li get_algo_orders
            try:
                orders = self.get_algo_orders(symbol)
                log.info("[ALGO_FALLBACK_DEBUG] method=get_algo_orders symbol=%s orders=%s", symbol, orders)
                if isinstance(orders, list) and orders:
                    match = next((o for o in orders if _match_order(o)), orders[-1])
                    oid = _get_id(match)
                    log.info(
                        "[ALGO_FALLBACK] match via get_algo_orders, fields=%s id=%s",
                        list(match.keys()) if isinstance(match, dict) else "?",
                        oid,
                    )
                    if oid:
                        return match
            except Exception as e:
                log.warning("[ALGO_FALLBACK] get_algo_orders başarısız: %s", e)

            # 2. Deneme: PM=True ise standart /fapi/v1/openAlgoOrders'ı da dene
            if self.portfolio_margin:
                try:
                    orders = self._request(
                        "GET",
                        "/fapi/v1/openAlgoOrders",
                        {"symbol": symbol},
                        signed=True,
                    )
                    log.info("[ALGO_FALLBACK_DEBUG] method=std_openAlgoOrders symbol=%s orders=%s", symbol, orders)
                    if isinstance(orders, list) and orders:
                        match = next((o for o in orders if _match_order(o)), orders[-1])
                        oid = _get_id(match)
                        log.info(
                            "[ALGO_FALLBACK] match via std openAlgoOrders, fields=%s id=%s",
                            list(match.keys()) if isinstance(match, dict) else "?",
                            oid,
                        )
                        if oid:
                            return match
                except Exception as e:
                    log.warning("[ALGO_FALLBACK] std openAlgoOrders başarısız: %s", e)

            # 3. Deneme: get_open_orders (normal order olarak düşmüş olabilir)
            try:
                orders = self.get_open_orders(symbol)
                log.info("[ALGO_FALLBACK_DEBUG] method=get_open_orders symbol=%s orders=%s", symbol, orders)
                if isinstance(orders, list) and orders:
                    match = next((o for o in orders if _match_order(o)), orders[-1])
                    oid = _get_id(match) or match.get("orderId")
                    log.info(
                        "[ALGO_FALLBACK] match via get_open_orders, fields=%s id=%s",
                        list(match.keys()) if isinstance(match, dict) else "?",
                        oid,
                    )
                    if oid:
                        return match
            except Exception as e:
                log.warning("[ALGO_FALLBACK] get_open_orders başarısız: %s", e)

            log.warning("[ALGO_FALLBACK_EMPTY] %s için hiçbir GET kaynağında eşleşen emir bulunamadı", symbol)

        return result

    # ── Standart STOP/TP emri (testnet için /fapi/v1/order üzerinden) ──

    def create_stop_order_standard(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        stop_price: float,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """
        STOP_MARKET / TAKE_PROFIT_MARKET emrini /fapi/v1/order üzerinden gönderir.
        Demo testnet'te /fapi/v1/algoOrder çalışmadığı için bu fallback kullanılır.
        """
        req_params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),  # STOP_MARKET veya TAKE_PROFIT_MARKET
            "quantity": amount,
            "stopPrice": stop_price,
            "reduceOnly": True,
            "closePosition": False,
        }
        if params:
            req_params.update(params)

        log.info(
            "[STOP_ORDER_STANDARD] %s %s %s stopPrice=%.8f qty=%.8f params=%s",
            symbol,
            side,
            order_type,
            stop_price,
            amount,
            params,
        )
        ep = self._ep("/fapi/v1/order")
        if ep is None:
            log.warning("[STOP_ORDER_STANDARD] Portfolio Margin'de bu endpoint desteklenmiyor")
            return {}
        return self._request("POST", ep, req_params, signed=True, max_retries=2)

    # ── Emir sorgulama ve iptal ──────────────────

    def query_order(self, symbol: str, order_id: str | int) -> dict[str, Any]:
        """Belirli bir emri sorgular (GET /fapi/v1/order).

        Parametreler
        ------------
        symbol   : İşlem çifti (BTCUSDT)
        order_id : Emir ID (orderId)
        """
        return self._request(
            "GET",
            self._ep("/fapi/v1/order"),
            {"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def cancel_order(self, symbol: str, order_id: str, is_algo: bool = False) -> dict[str, Any]:
        """Emir iptal eder (normal veya algo).

        Parametreler
        ------------
        symbol   : İşlem çifti (BTCUSDT)
        order_id : Emir ID (orderId veya algoId)
        is_algo  : True ise direkt /fapi/v1/algoOrder endpoint'ine gider,
                   False ise önce normal order endpoint'ini dener.
        """
        if is_algo:
            # Algo order olduğu biliniyor → direkt algo endpoint
            try:
                return self._request(
                    "DELETE",
                    self._ep("/fapi/v1/algoOrder"),
                    {"symbol": symbol, "algoId": order_id},
                    signed=True,
                )
            except Exception as e:
                log.warning(
                    "[CANCEL] %s algoId=%s algo endpoint başarısız: %s",
                    symbol,
                    order_id,
                    e,
                )
                raise

        # Normal order olduğu varsayılır
        # Önce normal order olarak dene (log_errors=False: algo ID ise 400 hatası beklenir)
        try:
            return self._request(
                "DELETE",
                self._ep("/fapi/v1/order"),
                {"symbol": symbol, "orderId": order_id},
                signed=True,
                log_errors=False,
            )
        except Exception:
            pass  # Algo order olabilir

        # Algo order olarak dene
        try:
            return self._request(
                "DELETE",
                self._ep("/fapi/v1/algoOrder"),
                {"symbol": symbol, "algoId": order_id},
                signed=True,
            )
        except Exception as e:
            log.warning(
                "[CANCEL] %s order_id=%s her iki endpoint'te başarısız: %s",
                symbol,
                order_id,
                e,
            )
            raise

    # ── Pozisyonlar ──────────────────────────────

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """
        Açık pozisyonları döner.

        Parametreler
        ------------
        symbol : İsteğe bağlı, belirtilirse sadece o sembol döner.
        """
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", self._ep("/fapi/v2/positionRisk"), params, signed=True)

    def get_account(self) -> dict[str, Any]:
        """Hesap bilgilerini döner (bakiye, margin, uPnL)."""
        return self._request("GET", self._ep("/fapi/v2/account"), signed=True)

    # ── Margin ───────────────────────────────────

    def set_margin_mode(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """Margin modunu ayarlar (ISOLATED / CROSSED).
        Portfolio Margin modunda desteklenmez — sessizce atlanır."""
        ep = self._ep("/fapi/v1/marginType")
        if ep is None:
            log.info("[MARGIN-MODE] Portfolio Margin modunda marginType ayarı desteklenmiyor — atlanıyor.")
            return {}
        return self._request(
            "POST",
            ep,
            {"symbol": symbol, "marginType": margin_type},
            signed=True,
        )

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Kaldıraç ayarlar."""
        return self._request(
            "POST",
            self._ep("/fapi/v1/leverage"),
            {"symbol": symbol, "leverage": leverage},
            signed=True,
        )

    # ── Açık emirler ─────────────────────────────

    def get_open_orders(self, symbol: str) -> list[dict]:
        """Açık emirleri döner (normal orders)."""
        result = self._request("GET", self._ep("/fapi/v1/openOrders"), {"symbol": symbol}, signed=True)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("orders") or result.get("openOrders") or []
        return []

    def get_algo_orders(self, symbol: str) -> list[dict]:
        """Açık algo emirleri döner (SL/TP)."""
        result = self._request("GET", self._ep("/fapi/v1/openAlgoOrders"), {"symbol": symbol}, signed=True)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("orders") or result.get("algoOrders") or []
        return []

    def get_all_open_orders(self, symbol: str) -> list[dict]:
        """
        Hem openOrders hem algoOrders'ı birleştirir.
        Recovery sırasında tüm açık emirleri görmek için.
        """
        orders = []
        try:
            orders.extend(self.get_open_orders(symbol))
        except Exception as e:
            log.error("[ORDERS] openOrders alınamadı %s: %s", symbol, e)
        try:
            orders.extend(self.get_algo_orders(symbol))
        except Exception as e:
            log.error("[ORDERS] algoOrders alınamadı %s: %s", symbol, e)
        return orders

    # ── Listen Key (User Data Stream) ──────────────

    def new_listen_key(self) -> str:
        """Yeni bir listen key oluşturur (user data stream için)."""
        ep = self._ep("/fapi/v1/listenKey") or "/fapi/v1/listenKey"
        result = self._request("POST", ep, signed=False)
        return result.get("listenKey", "")

    def renew_listen_key(self, listen_key: str) -> bool:
        """Listen key'in süresini uzatır (30 dk daha)."""
        try:
            ep = self._ep("/fapi/v1/listenKey") or "/fapi/v1/listenKey"
            self._request("PUT", ep, {"listenKey": listen_key}, signed=False)
            return True
        except Exception as e:
            log.warning("[LISTEN_KEY] Yenileme hatası: %s", e)
            return False

    def delete_listen_key(self, listen_key: str) -> bool:
        """Listen key'i kapatır."""
        try:
            ep = self._ep("/fapi/v1/listenKey") or "/fapi/v1/listenKey"
            self._request("DELETE", ep, {"listenKey": listen_key}, signed=False)
            return True
        except Exception as e:
            log.warning("[LISTEN_KEY] Kapatma hatası: %s", e)
            return False


# ═══════════════════════════════════════════════════════════
# Hızlı test
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()

    client = BinanceHTTPClient(
        api_key=os.getenv("TESTNET_API_KEY", ""),
        api_secret=os.getenv("TESTNET_API_SECRET", ""),
        base_url=os.getenv("TESTNET_BASE_URL", "https://demo-fapi.binance.com"),
    )

    # Exchange info testi
    info = client.get_symbol_info("BTCUSDT")
    print(
        f"BTCUSDT tick={client.get_tick_size('BTCUSDT')} "
        f"step={client.get_step_size('BTCUSDT')} "
        f"minQty={client.get_min_qty('BTCUSDT')}"
    )

    # Kline testi
    klines = client.get_klines("BTCUSDT", "5m", 3)
    for k in klines:
        print(f"  ts={k[0]} o={k[1]} h={k[2]} l={k[3]} c={k[4]} v={k[5]}")

    # Pozisyon testi
    try:
        positions = client.get_positions()
        active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        print(f"Aktif pozisyon: {len(active)}")
    except Exception as e:
        print(f"Pozisyon hatası (beklenen - testnet API key yoksa): {e}")

    print("✓ BinanceHTTPClient testleri tamamlandı")
