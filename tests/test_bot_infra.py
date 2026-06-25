"""
test_bot_infra.py — P9 sonrası bot_infra birim testleri
─────────────────────────────────────────────────────────
Kapsam: RetryConfig, CircuitBreaker, _RateLimiter, yardımcı fonksiyonlar
"""

import asyncio
import time

import pytest

from bot_infra import (
    CircuitBreaker,
    RetryConfig,
    _RateLimiter,
    _round_price,
    extract_order_id,
    fmt_bool,
    get_lock,
)
from models import Result


# ═══════════════════════════════════════════════════════════════
# RetryConfig
# ═══════════════════════════════════════════════════════════════


class TestRetryConfig:
    def test_defaults(self):
        rc = RetryConfig()
        assert rc.max_retries == 3
        assert rc.base_delay == 1.0
        assert rc.max_delay == 30.0
        assert rc.backoff_multiplier == 2.0
        assert rc.jitter is True
        assert rc.retry_on_http == (429, 500, 502, 503, 504)

    def test_custom_values(self):
        rc = RetryConfig(
            max_retries=5,
            base_delay=2.0,
            max_delay=60.0,
            backoff_multiplier=3.0,
            jitter=False,
            retry_on_http=(500, 502),
        )
        assert rc.max_retries == 5
        assert rc.base_delay == 2.0
        assert rc.max_delay == 60.0
        assert rc.backoff_multiplier == 3.0
        assert rc.jitter is False
        assert rc.retry_on_http == (500, 502)

    def test_retry_on_http_empty(self):
        """Boş tuple → hiçbir HTTP kodunda retry yapılmaz."""
        rc = RetryConfig(retry_on_http=())
        assert rc.retry_on_http == ()


# ═══════════════════════════════════════════════════════════════
# CircuitBreaker
# ═══════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    def test_initially_closed(self):
        cb = CircuitBreaker()
        assert cb.is_open is False

    def test_stays_closed_below_threshold(self):
        """Eşik altında failure → devre hala kapalı."""
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            asyncio.run(cb.record_failure())
        assert cb.is_open is False
        assert cb._failure_count == 2

    def test_opens_at_threshold(self):
        """Eşik sayıda failure → devre açılır."""
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            asyncio.run(cb.record_failure())
        assert cb.is_open is True

    def test_opens_above_threshold(self):
        """Eşik üstü failure → devre açık kalır."""
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(5):
            asyncio.run(cb.record_failure())
        assert cb.is_open is True
        assert cb._failure_count == 5

    def test_success_resets_counter(self):
        """record_success → failure sayacı sıfırlanır."""
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            asyncio.run(cb.record_failure())
        asyncio.run(cb.record_success())
        assert cb._failure_count == 0
        assert cb.is_open is False

    def test_success_after_open_closes_circuit(self):
        """Devre açıkken success → sayac sıfırlanır, devre kapanır."""
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            asyncio.run(cb.record_failure())
        assert cb.is_open is True
        asyncio.run(cb.record_success())
        assert cb.is_open is False

    def test_recovery_timeout_half_open(self):
        """Recovery süresi dolunca devre half-open olur."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        for _ in range(2):
            asyncio.run(cb.record_failure())
        assert cb.is_open is True
        time.sleep(0.1)  # recovery_timeout'tan uzun bekle
        assert cb.is_open is False  # half-open

    def test_recovery_timeout_not_yet(self):
        """Recovery süresi dolmadan devre hala açık."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10.0)
        for _ in range(2):
            asyncio.run(cb.record_failure())
        assert cb.is_open is True
        # 10 saniye beklemedik → hala açık
        assert cb.is_open is True

    def test_call_passes_through_when_closed(self):
        """Devre kapalı → call() fn'i çağırır, sonucu döner."""
        cb = CircuitBreaker()

        async def dummy():
            return "ok"

        result = asyncio.run(cb.call(dummy))
        assert result == "ok"
        assert cb._failure_count == 0  # success kaydedildi

    def test_call_returns_fail_when_open(self):
        """Devre açık → call() fn'i çağırmaz, Result.fail döner."""
        cb = CircuitBreaker(failure_threshold=1)
        asyncio.run(cb.record_failure())
        assert cb.is_open is True

        call_count = 0

        async def dummy():
            nonlocal call_count
            call_count += 1
            return "should not reach"

        result = asyncio.run(cb.call(dummy))
        assert call_count == 0  # fn çağrılmadı
        assert isinstance(result, Result)
        assert result.is_err
        assert "Circuit breaker open" in result.error

    def test_call_exception_records_failure(self):
        """call() içinde exception → failure kaydedilir, exception yükselir."""
        cb = CircuitBreaker(failure_threshold=3)

        async def fail():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            asyncio.run(cb.call(fail))
        assert cb._failure_count == 1

    def test_call_exception_opens_circuit(self):
        """Yeterli sayıda failure → devre açılır."""
        cb = CircuitBreaker(failure_threshold=2)

        async def fail():
            raise ValueError("boom")

        for _ in range(2):
            with pytest.raises(ValueError):
                asyncio.run(cb.call(fail))

        assert cb.is_open is True

        # Sonraki çağrı direkt Result.fail
        async def should_not_call():
            pytest.fail("should not be called")

        result = asyncio.run(cb.call(should_not_call))
        assert result.is_err

    def test_call_with_args_kwargs(self):
        """call() argümanları fn'e iletir."""
        cb = CircuitBreaker()

        async def add(a, b, multiplier=1):
            return (a + b) * multiplier

        result = asyncio.run(cb.call(add, 2, 3, multiplier=10))
        assert result == 50

    def test_call_success_resets_after_failures(self):
        """Birkaç failure'dan sonra success → sayaç sıfırlanır."""
        cb = CircuitBreaker(failure_threshold=3)

        async def fail():
            raise ValueError("x")

        async def ok():
            return "recovered"

        # 2 failure → hala kapalı
        for _ in range(2):
            with pytest.raises(ValueError):
                asyncio.run(cb.call(fail))
        assert cb._failure_count == 2

        # 1 success → sıfırlanır
        result = asyncio.run(cb.call(ok))
        assert result == "recovered"
        assert cb._failure_count == 0

    def test_concurrent_failures(self):
        """Eşzamanlı failure'lar doğru sayılır."""
        cb = CircuitBreaker(failure_threshold=5)

        async def fail():
            raise ValueError("concurrent")

        async def run_failures(n):
            for _ in range(n):
                with pytest.raises(ValueError):
                    await cb.call(fail)

        asyncio.run(run_failures(5))
        assert cb._failure_count == 5
        assert cb.is_open is True

    def test_multiple_record_failure_opens(self):
        """Doğrudan record_failure çağrıları devreyi açar."""
        cb = CircuitBreaker(failure_threshold=4)
        for _ in range(4):
            asyncio.run(cb.record_failure())
        assert cb.is_open is True
        assert cb._failure_count == 4


# ═══════════════════════════════════════════════════════════════
# _RateLimiter
# ═══════════════════════════════════════════════════════════════


class TestRateLimiter:
    def test_acquire_first_call_no_delay(self):
        """İlk acquire gecikmesiz geçer."""
        rl = _RateLimiter(max_per_minute=1200)

        async def measure():
            t0 = time.time()
            await rl.acquire()
            return time.time() - t0

        elapsed = asyncio.run(measure())
        assert elapsed < 0.1  # neredeyse anında

    def test_acquire_second_call_waits(self):
        """Arka arkaya acquire → interval kadar bekler."""
        rl = _RateLimiter(max_per_minute=60)  # 1 istek/saniye

        async def measure():
            await rl.acquire()
            t0 = time.time()
            await rl.acquire()
            return time.time() - t0

        elapsed = asyncio.run(measure())
        # interval = 1.0s, en az 0.9s beklemeli
        assert elapsed >= 0.9, f"Beklenen >= 0.9s, gerçek: {elapsed:.2f}s"

    def test_custom_max_per_minute(self):
        """Özel max_per_minute → doğru interval."""
        rl = _RateLimiter(max_per_minute=30)
        assert rl._interval == 2.0  # 60/30 = 2.0s

    def test_high_limit_no_wait(self):
        """Çok yüksek limit → interval çok küçük, bekleme yok."""
        rl = _RateLimiter(max_per_minute=120000)

        async def measure():
            await rl.acquire()
            t0 = time.time()
            await rl.acquire()
            return time.time() - t0

        elapsed = asyncio.run(measure())
        assert elapsed < 0.1


# ═══════════════════════════════════════════════════════════════
# Yardımcı fonksiyonlar
# ═══════════════════════════════════════════════════════════════


class TestExtractOrderId:
    def test_algo_id_first(self):
        assert extract_order_id({"algoId": "A1", "orderId": "O1"}) == "A1"

    def test_order_id_fallback(self):
        assert extract_order_id({"orderId": "O1", "id": "I1"}) == "O1"

    def test_id_last_resort(self):
        assert extract_order_id({"id": "I1"}) == "I1"

    def test_empty_response(self):
        assert extract_order_id({}) == ""

    def test_none_fields(self):
        assert extract_order_id({"algoId": None, "orderId": None}) == ""


class TestRoundPrice:
    def test_round_to_tick(self):
        assert _round_price(105.123, 0.01) == 105.12

    def test_round_up(self):
        assert _round_price(105.129, 0.01) == 105.13

    def test_zero_tick(self):
        assert _round_price(105.123, 0) == 105.123

    def test_negative_tick_returns_original(self):
        assert _round_price(105.123, -0.01) == 105.123


class TestFmtBool:
    def test_true(self):
        assert fmt_bool(True) == "✅"

    def test_false(self):
        assert fmt_bool(False) == "❌"


class TestGetLock:
    def test_returns_asyncio_lock(self):
        lock = get_lock("BTCUSDT")
        assert isinstance(lock, asyncio.Lock)

    def test_same_symbol_same_lock(self):
        lock1 = get_lock("ETHUSDT")
        lock2 = get_lock("ETHUSDT")
        assert lock1 is lock2

    def test_different_symbols_different_locks(self):
        lock1 = get_lock("BTCUSDT")
        lock2 = get_lock("ETHUSDT")
        assert lock1 is not lock2
