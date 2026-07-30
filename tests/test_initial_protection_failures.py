"""
test_initial_protection_failures.py — Binance rejection & protection failure
scenarios for EntryManager.execute_live_entry.

Each test goes through the real execute_live_entry code path, using a
deterministic FakeExchange that mimics the REST adapter contract.
No new API is invented; only existing adapter method signatures are used.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading.entry_manager import EntryManager
from failure_simulator import Expected, FailureMode, FakeExchange, Scenario, SCENARIOS


def _mock_fvg(top=105.0, bottom=103.0, direction="bullish"):
    from unittest.mock import MagicMock

    fvg = MagicMock()
    fvg.top = top
    fvg.bottom = bottom
    fvg.direction = direction
    return fvg


# ── Fixture ───────────────────────────────────────────────────────


@pytest.fixture
def make_entry_manager():
    """Factory fixture: returns a callable that creates an EntryManager
    wrapping a given FakeExchange."""

    def _make(exchange: FakeExchange) -> EntryManager:
        return EntryManager(rest_client=exchange, is_live=True)

    return _make


# ═══════════════════════════════════════════════════════════════════
# SL -2021: retry yok, emergency close, protected=false
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_sl_2021_has_no_retry_and_closes_position(make_entry_manager):
    """SL -2021 response → 1 SL attempt, emergency close, no protected state."""
    exchange = FakeExchange(
        mode=FailureMode.SL_2021,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "long",
        0.5,
        99.0,
        110.0,
    )

    assert result.success is False
    assert "EMERGENCY CLOSE" in result.error
    sl_calls = [name for name, _ in exchange.calls if name == "sl"]
    assert len(sl_calls) == 1
    close_calls = [name for name, _ in exchange.calls if name == "market"]
    assert len(close_calls) == 2  # entry + emergency
    assert exchange.protected is False


@pytest.mark.asyncio
async def test_short_sl_2021_has_no_retry_and_closes(make_entry_manager):
    """Short SL -2021: same no-retry + emergency close behavior."""
    exchange = FakeExchange(
        mode=FailureMode.SL_2021,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "short",
        0.5,
        101.0,
        90.0,
    )

    assert result.success is False
    assert "EMERGENCY CLOSE" in result.error
    sl_calls = [name for name, _ in exchange.calls if name == "sl"]
    assert len(sl_calls) == 1
    assert exchange.protected is False


# ═══════════════════════════════════════════════════════════════════
# SL generic exception: exception propagates, no protected state
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generic_sl_failure_is_not_silenced(make_entry_manager):
    """SL generic exception → exception propagates (no try/except around SL call)."""
    exchange = FakeExchange(
        mode=FailureMode.SL_GENERIC,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    with pytest.raises(RuntimeError, match="simulated stop placement failure"):
        await manager.execute_live_entry(
            "BTCUSDT",
            "long",
            0.5,
            99.0,
            110.0,
        )

    assert exchange.protected is False


# ═══════════════════════════════════════════════════════════════════
# Partial fill: actual_qty used for protection, not requested qty
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_actual_qty_is_used_for_protection(make_entry_manager):
    """PARTIAL_FILL → SL order uses actual (filled) qty, not the requested qty."""
    exchange = FakeExchange(
        mode=FailureMode.PARTIAL_FILL,
        fill_price=Decimal("100"),
        requested_qty=Decimal("1.0"),
        actual_qty=Decimal("0.37"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "long",
        0.5,
        99.0,
        110.0,
    )

    assert result.success is True
    sl_calls = [p for name, p in exchange.calls if name == "sl"]
    assert len(sl_calls) == 1
    assert sl_calls[0]["qty"] == 0.37
    assert exchange.protected is True


# ═══════════════════════════════════════════════════════════════════
# Emergency close failure: SL -2021 → emergency close → close fails
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_close_failure_is_visible_and_never_marks_protected(make_entry_manager):
    """CLOSE_FAIL mode: SL returns -2021 → emergency close → close raises → error visible."""
    exchange = FakeExchange(
        mode=FailureMode.CLOSE_FAIL,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "long",
        0.5,
        99.0,
        110.0,
    )

    assert result.success is False
    assert "BASARISIZ" in result.error
    assert exchange.protected is False


# ═══════════════════════════════════════════════════════════════════
# Direction validation: SL/TP too close to actual fill after recalc
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_direction_validation_sl_too_close_long(make_entry_manager):
    """Long SL >= actual_fill - epsilon → emergency close, no SL call."""
    exchange = FakeExchange(
        mode=FailureMode.NONE,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
        tick_size=Decimal("0.10"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "long",
        0.5,
        100.0,
        110.0,
        risk_pts=0.5,
        fvg_buf=0.3,
        tp_rr=2.0,
        trigger_fvg=_mock_fvg(top=100.5, bottom=100.3, direction="bullish"),
    )

    assert result.success is False
    assert "EMERGENCY" in result.error
    # SL order should NOT have been sent (validation fails before API call)
    sl_calls = [name for name, _ in exchange.calls if name == "sl"]
    assert len(sl_calls) == 0
    assert exchange.protected is False


@pytest.mark.asyncio
async def test_direction_validation_tp_too_close_short(make_entry_manager):
    """Short TP <= actual_fill + epsilon → emergency close, no TP call."""
    exchange = FakeExchange(
        mode=FailureMode.NONE,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
        tick_size=Decimal("0.10"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "short",
        0.5,
        102.0,
        98.0,
        risk_pts=0.5,
        fvg_buf=0.3,
        tp_rr=2.0,
        trigger_fvg=_mock_fvg(top=99.8, bottom=99.5, direction="bearish"),
    )

    assert result.success is False
    assert "EMERGENCY" in result.error
    sl_calls = [name for name, _ in exchange.calls if name == "sl"]
    assert len(sl_calls) == 0
    assert exchange.protected is False


# ═══════════════════════════════════════════════════════════════════
# Protected state only after SL response confirmed
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_protected_only_after_sl_response(make_entry_manager):
    """protected=True only after place_stop_order returns a valid id."""
    exchange = FakeExchange(
        mode=FailureMode.NONE,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    assert exchange.protected is False  # before any call

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "long",
        0.5,
        99.0,
        110.0,
    )

    assert result.success is True
    # The FakeExchange.place_stop_order sets protected=True on success
    assert exchange.protected is True


@pytest.mark.asyncio
async def test_sl_failure_never_sets_protected(make_entry_manager):
    """SL -2021 → protected stays False throughout the flow."""
    exchange = FakeExchange(
        mode=FailureMode.SL_2021,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "long",
        0.5,
        99.0,
        110.0,
    )

    assert result.success is False
    assert exchange.protected is False


# ═══════════════════════════════════════════════════════════════════
# No TP call after SL -2021 (no partial flow)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_tp_call_after_sl_2021(make_entry_manager):
    """SL -2021 → emergency close, no TP order is sent."""
    exchange = FakeExchange(
        mode=FailureMode.SL_2021,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    result = await manager.execute_live_entry(
        "BTCUSDT",
        "long",
        0.5,
        99.0,
        110.0,
    )

    assert result.success is False
    tp_calls = [name for name, _ in exchange.calls if name == "tp"]
    assert len(tp_calls) == 0


# ═══════════════════════════════════════════════════════════════════
# Scenario-driven parametric tests
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_all_scenarios(make_entry_manager, scenario: Scenario):
    """Parametric runner covering every Scenario in SCENARIOS."""
    exchange = FakeExchange(
        mode=scenario.mode,
        fill_price=Decimal("100"),
        actual_qty=Decimal("0.37")
        if scenario.mode == FailureMode.PARTIAL_FILL
        else Decimal("0.5"),
    )
    manager = make_entry_manager(exchange)

    sl = 99.0 if scenario.side == "long" else 101.0
    tp = 110.0 if scenario.side == "long" else 90.0

    if Expected.RAISE in scenario.expected:
        with pytest.raises(Exception):
            await manager.execute_live_entry(
                "BTCUSDT",
                scenario.side,
                0.5,
                sl,
                tp,
            )
    else:
        result = await manager.execute_live_entry(
            "BTCUSDT",
            scenario.side,
            0.5,
            sl,
            tp,
        )

        if Expected.CLOSE in scenario.expected:
            assert result.success is False
            assert "EMERGENCY" in result.error or "BASARISIZ" in result.error

        if Expected.PROTECTED in scenario.expected:
            assert exchange.protected is True

        if Expected.UNPROTECTED in scenario.expected:
            assert exchange.protected is False

        if Expected.NO_RETRY in scenario.expected:
            sl_calls = [name for name, _ in exchange.calls if name == "sl"]
            assert len(sl_calls) == 1

    # Protected state invariant: never True when SL failed
    if exchange.protected:
        sl_ok = any(name == "sl" for name, _ in exchange.calls)
        assert sl_ok, "protected=True requires at least one SL call"
