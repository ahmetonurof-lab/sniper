"""
test_bot_binance.py — P9 sonrası BinanceRESTClient birim testleri
─────────────────────────────────────────────────────────────────
Kapsam: HTTP transport, retry, backoff, imza, static helpers
Mock: aiohttp.ClientSession (gerçek ağ çağrısı yok)
"""

import asyncio
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from bot_binance import (
    BinanceRESTClient,
    _round_step,
    _round_to_tick,
)
from bot_infra import CircuitBreaker, RetryConfig, _RateLimiter


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def client():
    """Temel BinanceRESTClient fixture — gerçek ağ çağrısı yapmaz."""
    return BinanceRESTClient(
        api_key="test_api_key",
        api_secret="test_api_secret",
        base_url="https://testnet.binancefuture.com",
        rate_limiter=_RateLimiter(max_per_minute=120000),  # çok hızlı
        semaphore=asyncio.Semaphore(10),
        retry_config=RetryConfig(max_retries=2, base_delay=0.01, jitter=False),
        circuit_breaker=CircuitBreaker(failure_threshold=10),  # test'te açılmasın
    )


class _MockResponse:
    """aiohttp response mock — async context manager uyumlu."""

    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body

    async def text(self):
        if isinstance(self._body, dict):
            return json.dumps(self._body)
        return self._body or "{}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _mock_response(status=200, body=None):
    """aiohttp response mock'u (async context manager)."""
    return _MockResponse(status, body)


def _inject_session(client, get_fn=None, post_fn=None, delete_fn=None):
    """Client'a mock session enjekte et.

    get_fn/post_fn/delete_fn: async def fn(...) -> _MockResponse
    Dönüş değeri otomatik async context manager'a sarılır.

    Not: aiohttp'te session.get/post/delete COROUTINE DEĞİLDİR,
    direkt async context manager döner. Bu yüzden wrapper async olamaz.
    """
    session = MagicMock()
    session.closed = False

    def _wrap(fn):
        # fn async olabilir, ama session.get() senkron çağrıdır.
        # Bu yüzden wrapper bir async context manager döndürmeli,
        # coroutine değil.
        class _Ctx:
            def __init__(self, fn, *args, **kwargs):
                self._fn = fn
                self._args = args
                self._kwargs = kwargs

            async def __aenter__(self):
                return await self._fn(*self._args, **self._kwargs)

            async def __aexit__(self, *args):
                pass

        def wrapper(*args, **kwargs):
            return _Ctx(fn, *args, **kwargs)

        return wrapper

    if get_fn:
        session.get = _wrap(get_fn)
    if post_fn:
        session.post = _wrap(post_fn)
    if delete_fn:
        session.delete = _wrap(delete_fn)
    client._session = session


# ═══════════════════════════════════════════════════════════════
# Precision yardımcıları (pure functions)
# ═══════════════════════════════════════════════════════════════


class TestRoundToTick:
    def test_basic(self):
        assert _round_to_tick(105.12345, 0.01) == 105.12

    def test_round_up(self):
        assert _round_to_tick(105.129, 0.01) == 105.13

    def test_zero_tick(self):
        assert _round_to_tick(105.123, 0.0) == 105.123

    def test_negative_tick(self):
        assert _round_to_tick(105.123, -0.01) == 105.123

    def test_small_tick(self):
        assert _round_to_tick(0.12345678, 0.0001) == 0.1235


class TestRoundStep:
    def test_basic(self):
        assert _round_step(1.75, 0.5) == 1.5

    def test_exact(self):
        assert _round_step(2.0, 0.5) == 2.0

    def test_zero_step(self):
        assert _round_step(1.75, 0) == 1.75

    def test_negative_step(self):
        assert _round_step(1.75, -0.5) == 1.75


# ═══════════════════════════════════════════════════════════════
# Static helpers
# ═══════════════════════════════════════════════════════════════


class TestGetOrderType:
    def test_standard_type(self):
        assert BinanceRESTClient.get_order_type({"type": "MARKET"}) == "MARKET"

    def test_algo_order_type(self):
        assert (
            BinanceRESTClient.get_order_type({"orderType": "STOP_MARKET"})
            == "STOP_MARKET"
        )

    def test_standard_priority(self):
        """type alanı orderType'dan öncelikli."""
        assert (
            BinanceRESTClient.get_order_type({"type": "LIMIT", "orderType": "STOP"})
            == "LIMIT"
        )

    def test_empty(self):
        assert BinanceRESTClient.get_order_type({}) == ""


class TestGetOrderPrice:
    def test_trigger_price(self):
        assert BinanceRESTClient.get_order_price({"triggerPrice": "105.5"}) == 105.5

    def test_stop_price_fallback(self):
        assert BinanceRESTClient.get_order_price({"stopPrice": "200.0"}) == 200.0

    def test_trigger_priority(self):
        assert (
            BinanceRESTClient.get_order_price(
                {"triggerPrice": "105.5", "stopPrice": "200.0"}
            )
            == 105.5
        )

    def test_empty(self):
        assert BinanceRESTClient.get_order_price({}) == 0.0


class TestGetOrderTimestamp:
    def test_update_time(self):
        assert (
            BinanceRESTClient.get_order_timestamp({"updateTime": 1700000000000})
            == 1700000000000
        )

    def test_time_fallback(self):
        assert (
            BinanceRESTClient.get_order_timestamp({"time": 1600000000000})
            == 1600000000000
        )

    def test_update_priority(self):
        assert (
            BinanceRESTClient.get_order_timestamp(
                {"updateTime": 1700000000000, "time": 1600000000000}
            )
            == 1700000000000
        )

    def test_empty(self):
        assert BinanceRESTClient.get_order_timestamp({}) == 0

    def test_none_values(self):
        assert BinanceRESTClient.get_order_timestamp({"updateTime": None}) == 0

    def test_invalid_string(self):
        assert BinanceRESTClient.get_order_timestamp({"time": "invalid"}) == 0


# ═══════════════════════════════════════════════════════════════
# Constructor & Configuration
# ═══════════════════════════════════════════════════════════════


class TestConstructor:
    def test_defaults(self):
        c = BinanceRESTClient(
            "k", "s", "https://x.com", _RateLimiter(100), asyncio.Semaphore(1)
        )
        assert c._api_key == "k"
        assert c._api_secret == "s"
        assert c._base_url == "https://x.com"
        assert c._session is None
        assert isinstance(c._retry_config, RetryConfig)
        assert isinstance(c._circuit_breaker, CircuitBreaker)
        assert c._connector_limit == 10
        assert c._connector_limit_per_host == 5
        assert c._timeout_total == 30.0
        assert c._timeout_connect == 10.0

    def test_custom_params(self):
        rc = RetryConfig(max_retries=5)
        cb = CircuitBreaker(failure_threshold=3)
        c = BinanceRESTClient(
            "k",
            "s",
            "https://x.com",
            _RateLimiter(100),
            asyncio.Semaphore(1),
            retry_config=rc,
            circuit_breaker=cb,
            connector_limit=20,
            connector_limit_per_host=10,
            timeout_total=60.0,
            timeout_connect=15.0,
        )
        assert c._retry_config is rc
        assert c._circuit_breaker is cb
        assert c._connector_limit == 20
        assert c._connector_limit_per_host == 10
        assert c._timeout_total == 60.0
        assert c._timeout_connect == 15.0


# ═══════════════════════════════════════════════════════════════
# _build_headers & _sign_params (pure-ish)
# ═══════════════════════════════════════════════════════════════


class TestBuildHeaders:
    def test_api_key_header(self, client):
        headers = client._build_headers()
        assert headers == {"X-MBX-APIKEY": "test_api_key"}


class TestSignParams:
    def test_has_timestamp_and_signature(self, client):
        result = client._sign_params("symbol=BTCUSDT")
        assert "symbol=BTCUSDT" in result
        assert "timestamp=" in result
        assert "signature=" in result

    def test_empty_params(self, client):
        result = client._sign_params("")
        assert result.startswith("timestamp=")
        assert "&signature=" in result

    def test_signature_valid_hmac(self, client):
        """İmza HMAC-SHA256 ile doğru hesaplanıyor."""
        # Sabit timestamp ile test
        with patch("time.time", return_value=1700000000.0):
            result = client._sign_params("symbol=BTCUSDT")
        expected_ts = 1700000000000
        expected_str = f"symbol=BTCUSDT&timestamp={expected_ts}"
        expected_sig = hmac.new(
            b"test_api_secret", expected_str.encode(), hashlib.sha256
        ).hexdigest()
        assert result == f"{expected_str}&signature={expected_sig}"


# ═══════════════════════════════════════════════════════════════
# _should_retry
# ═══════════════════════════════════════════════════════════════


class TestShouldRetry:
    def test_retry_on_429(self, client):
        assert client._should_retry(429) is True

    def test_retry_on_5xx(self, client):
        for code in (500, 502, 503, 504):
            assert client._should_retry(code) is True, f"code={code}"

    def test_no_retry_on_4xx(self, client):
        for code in (400, 401, 403, 404):
            assert client._should_retry(code) is False, f"code={code}"

    def test_no_retry_on_200(self, client):
        assert client._should_retry(200) is False

    def test_retry_on_none(self, client):
        """None (bağlantı hatası) → retry."""
        assert client._should_retry(None) is True

    def test_custom_retry_codes(self):
        rc = RetryConfig(retry_on_http=(500,))
        c = BinanceRESTClient(
            "k",
            "s",
            "https://x.com",
            _RateLimiter(100),
            asyncio.Semaphore(1),
            retry_config=rc,
        )
        assert c._should_retry(500) is True
        assert c._should_retry(502) is False


# ═══════════════════════════════════════════════════════════════
# _compute_backoff
# ═══════════════════════════════════════════════════════════════


class TestComputeBackoff:
    def test_first_attempt(self, client):
        delay = client._compute_backoff(0)
        assert delay == 0.01  # base_delay * multiplier^0 = 0.01

    def test_second_attempt(self, client):
        delay = client._compute_backoff(1)
        assert delay == 0.02  # 0.01 * 2^1

    def test_third_attempt(self, client):
        delay = client._compute_backoff(2)
        assert delay == 0.04  # 0.01 * 2^2

    def test_respects_max_delay(self):
        rc = RetryConfig(
            base_delay=1.0, max_delay=5.0, backoff_multiplier=10.0, jitter=False
        )
        c = BinanceRESTClient(
            "k",
            "s",
            "https://x.com",
            _RateLimiter(100),
            asyncio.Semaphore(1),
            retry_config=rc,
        )
        delay = c._compute_backoff(5)  # 1 * 10^5 = 100000, capped at 5
        assert delay == 5.0

    def test_jitter_adds_variation(self):
        """Jitter aktifken delay taban değerden farklı olur."""
        rc = RetryConfig(base_delay=1.0, jitter=True)
        c = BinanceRESTClient(
            "k",
            "s",
            "https://x.com",
            _RateLimiter(100),
            asyncio.Semaphore(1),
            retry_config=rc,
        )
        # 10 çağrıda en az biri 1.0'dan farklı olmalı (jitter)
        values = [c._compute_backoff(0) for _ in range(20)]
        assert any(
            abs(v - 1.0) > 0.01 for v in values
        ), f"Jitter yok gibi: {values[:5]}"

    def test_jitter_not_negative(self):
        """Jitter delay'i negatif yapamaz."""
        rc = RetryConfig(base_delay=0.001, jitter=True)
        c = BinanceRESTClient(
            "k",
            "s",
            "https://x.com",
            _RateLimiter(100),
            asyncio.Semaphore(1),
            retry_config=rc,
        )
        for _ in range(50):
            delay = c._compute_backoff(0)
            assert delay >= 0, f"Negatif delay: {delay}"


# ═══════════════════════════════════════════════════════════════
# GET isteği (mock aiohttp)
# ═══════════════════════════════════════════════════════════════


class TestGet:
    def test_success(self, client):
        """Başarılı GET → Result.ok(data)."""

        async def mock_get(url, headers):
            return _mock_response(200, {"result": "ok"})

        _inject_session(client, get_fn=mock_get)

        r = asyncio.run(client.get("/fapi/v1/test", "symbol=BTCUSDT"))
        assert r.is_ok
        assert r.value == {"result": "ok"}

    def test_http_error_no_retry(self, client):
        """401 gibi retry yapılmayan hata → hemen Result.fail."""
        call_count = 0

        async def mock_get(url, headers):
            nonlocal call_count
            call_count += 1
            return _mock_response(401, {"code": -2015, "msg": "Invalid API-key"})

        _inject_session(client, get_fn=mock_get)
        r = asyncio.run(client.get("/fapi/v1/test"))
        assert r.is_err
        assert "401" in r.error
        assert call_count == 1  # retry yapılmadı

    def test_http_5xx_retry_then_success(self, client):
        """500 → retry → 200."""
        call_count = 0

        async def mock_get(url, headers):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_response(500, {"msg": "Internal error"})
            return _mock_response(200, {"result": "recovered"})

        _inject_session(client, get_fn=mock_get)
        r = asyncio.run(client.get("/fapi/v1/test"))
        assert r.is_ok
        assert r.value == {"result": "recovered"}
        assert call_count == 2

    def test_http_all_retries_exhausted(self, client):
        """Tüm retry'ler başarısız → Result.fail."""
        call_count = 0

        async def mock_get(url, headers):
            nonlocal call_count
            call_count += 1
            return _mock_response(503, {"msg": "Service Unavailable"})

        _inject_session(client, get_fn=mock_get)
        r = asyncio.run(client.get("/fapi/v1/test"))
        assert r.is_err
        assert "503" in r.error
        assert call_count == 2  # max_retries=2

    def test_network_error_retry(self, client):
        """aiohttp.ClientError → retry → success."""
        call_count = 0

        async def mock_get(url, headers):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Connection refused")
            return _mock_response(200, {"result": "ok"})

        _inject_session(client, get_fn=mock_get)
        r = asyncio.run(client.get("/fapi/v1/test"))
        assert r.is_ok
        assert call_count == 2

    def test_circuit_breaker_blocks(self):
        """Açık circuit breaker → direkt Result.fail, ağ çağrısı yok."""
        cb = CircuitBreaker(failure_threshold=1)
        c = BinanceRESTClient(
            "k",
            "s",
            "https://x.com",
            _RateLimiter(100),
            asyncio.Semaphore(1),
            circuit_breaker=cb,
            retry_config=RetryConfig(max_retries=1, base_delay=0.01, jitter=False),
        )
        asyncio.run(cb.record_failure())  # devreyi aç
        assert cb.is_open is True

        async def mock_get(url, headers):
            pytest.fail("should not be called")

        _inject_session(c, get_fn=mock_get)

        r = asyncio.run(c.get("/fapi/v1/test"))
        assert r.is_err
        assert "Circuit breaker open" in r.error


# ═══════════════════════════════════════════════════════════════
# POST isteği (mock aiohttp)
# ═══════════════════════════════════════════════════════════════


class TestPost:
    def test_success(self, client):
        async def mock_post(url, data, headers):
            return _mock_response(200, {"orderId": 12345, "status": "NEW"})

        _inject_session(client, post_fn=mock_post)
        r = asyncio.run(
            client.post("/fapi/v1/order", {"symbol": "BTCUSDT", "side": "BUY"})
        )
        assert r.is_ok
        assert r.value["orderId"] == 12345

    def test_http_error(self, client):
        async def mock_post(url, data, headers):
            return _mock_response(400, {"code": -1100, "msg": "Bad request"})

        _inject_session(client, post_fn=mock_post)
        r = asyncio.run(client.post("/fapi/v1/order", {"symbol": "INVALID"}))
        assert r.is_err
        assert "400" in r.error

    def test_network_error_then_success(self, client):
        call_count = 0

        async def mock_post(url, data, headers):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Timeout")
            return _mock_response(200, {"orderId": 999})

        _inject_session(client, post_fn=mock_post)
        r = asyncio.run(client.post("/fapi/v1/order", {"symbol": "BTCUSDT"}))
        assert r.is_ok
        assert r.value["orderId"] == 999
        assert call_count == 2


# ═══════════════════════════════════════════════════════════════
# DELETE isteği (mock aiohttp)
# ═══════════════════════════════════════════════════════════════


class TestDelete:
    def test_success(self, client):
        async def mock_delete(url, headers):
            return _mock_response(200, {"status": "CANCELED"})

        _inject_session(client, delete_fn=mock_delete)
        r = asyncio.run(client.delete("/fapi/v1/order", "symbol=BTCUSDT&orderId=123"))
        assert r.is_ok
        assert r.value["status"] == "CANCELED"

    def test_not_found(self, client):
        async def mock_delete(url, headers):
            return _mock_response(400, {"code": -2011, "msg": "Unknown order"})

        _inject_session(client, delete_fn=mock_delete)
        r = asyncio.run(client.delete("/fapi/v1/order", "symbol=BTCUSDT&orderId=999"))
        assert r.is_err
        assert "-2011" in r.error

    def test_5xx_retry(self, client):
        call_count = 0

        async def mock_delete(url, headers):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return _mock_response(502, {})
            return _mock_response(200, {"status": "CANCELED"})

        _inject_session(client, delete_fn=mock_delete)
        r = asyncio.run(client.delete("/fapi/v1/order", "symbol=BTCUSDT"))
        assert r.is_ok
        assert call_count == 2


# ═══════════════════════════════════════════════════════════════
# Session yönetimi
# ═══════════════════════════════════════════════════════════════


class TestSessionManagement:
    def test_ensure_session_creates_once(self, client):
        assert client._session is None
        s1 = asyncio.run(client._ensure_session())
        s2 = asyncio.run(client._ensure_session())
        assert s1 is s2  # aynı session

    def test_close_cleans_up(self, client):
        s = asyncio.run(client._ensure_session())
        assert client._session is not None
        assert not s.closed

        asyncio.run(client.close())
        # close çağrıldı, ama MagicMock kullanmadığımız için gerçek session
        # _ensure_session gerçek bir aiohttp session oluşturur, close çalışır
        assert client._session.closed

    def test_ensure_session_recreates_after_close(self, client):
        s1 = asyncio.run(client._ensure_session())
        asyncio.run(client.close())
        s2 = asyncio.run(client._ensure_session())
        assert s1 is not s2  # yeni session


# ═══════════════════════════════════════════════════════════════
# Yüksek seviye metodlar (mock get/post/delete)
# ═══════════════════════════════════════════════════════════════


class TestCancelOrder:
    def test_cancel_normal_success(self, client):
        async def mock_delete(url, headers):
            return _mock_response(200, {})

        _inject_session(client, delete_fn=mock_delete)

        result = asyncio.run(client.cancel_order("123", "BTCUSDT", reason="test"))
        assert result is True

    def test_cancel_algo_success(self, client):
        async def mock_delete(url, headers):
            return _mock_response(200, {})

        _inject_session(client, delete_fn=mock_delete)

        result = asyncio.run(
            client.cancel_order("A123", "BTCUSDT", reason="test", is_algo=True)
        )
        assert result is True

    def test_cancel_unknown_order_returns_true(self, client):
        """-2011 Unknown order → True (zaten yok, sorun değil)."""

        async def mock_delete(url, headers):
            return _mock_response(400, {"code": -2011, "msg": "Unknown order"})

        _inject_session(client, delete_fn=mock_delete)

        result = asyncio.run(client.cancel_order("999", "BTCUSDT"))
        assert result is True  # "zaten yok" durumu başarılı sayılır

    def test_cancel_real_error_returns_false(self, client):
        async def mock_delete(url, headers):
            return _mock_response(500, {"msg": "Internal error"})

        _inject_session(client, delete_fn=mock_delete)

        result = asyncio.run(client.cancel_order("123", "BTCUSDT"))
        assert result is False
