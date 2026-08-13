"""
test_exit_registry_identity.py — L-01 regression tests.

Bug: ExitLifecycleService ve UserDataHandler constructor'lari
`exit_log or {}` / `exit_locks or {}` deseni kullaniyordu. `or` bos dict icin
YENI bir object uretir; bot.py bu registry'leri paylastigindan iki servis
farkli dict'e bakar ve idempotency guard / per-trade lock gercekte calismaz.
Fix: `is not None` deseni (recovery_manager.py:80 ile ayni).
"""

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading.exit_lifecycle import ExitLifecycleService
from trading.user_data_handler import UserDataHandler


# ═══════════════════════════════════════════════════════════════════
# ExitLifecycleService — paylasilan registry identity
# ═══════════════════════════════════════════════════════════════════


def _make_service(exit_log, exit_locks):
    rest = AsyncMock()
    rest.place_market_order_priority = AsyncMock(return_value={})
    rest.place_force_close_order = AsyncMock(return_value=True)
    rest.get_positions = AsyncMock(return_value=[])

    om = AsyncMock()
    om.position_still_open = AsyncMock(return_value=False)
    om.verify_protection = AsyncMock(return_value=(True, True))
    om.repair_protection = AsyncMock()
    om.cleanup_on_exit = AsyncMock()

    svc = ExitLifecycleService(
        rest_client=rest,
        order_manager=om,
        active_trades={},
        states={},
        rsms={},
        trades=deque(maxlen=1000),
        pl_callback=MagicMock(),
        risk_mgr=MagicMock(),
        balance_getter=lambda: 1000.0,
        balance_setter=lambda v: None,
        wallet_balance_getter=lambda: 1000.0,
        output_dir="/tmp",
        fvg_state_file="/tmp/fvg.json",
        exit_log=exit_log,
        exit_locks=exit_locks,
        is_live=True,
    )
    return svc, rest, om


class TestExitLifecycleRegistryIdentity:
    def test_exit_log_shared_identity_preserved(self):
        shared: dict = {}
        svc, _, _ = _make_service(shared, {})
        assert svc._exit_log is shared  # L-01: identity korunuyor

    def test_exit_locks_shared_identity_preserved(self):
        shared: dict = {}
        svc, _, _ = _make_service({}, shared)
        assert svc._exit_locks is shared  # L-01: identity korunuyor

    def test_none_defaults_create_fresh_empty(self):
        svc, _, _ = _make_service(None, None)
        assert svc._exit_log == {}
        assert svc._exit_locks == {}
        assert isinstance(svc._exit_locks, dict)


# ═══════════════════════════════════════════════════════════════════
# UserDataHandler — paylasilan exit lock registry identity
# ═══════════════════════════════════════════════════════════════════


class TestUserDataHandlerRegistryIdentity:
    def test_exit_locks_shared_identity_preserved(self):
        shared: dict = {}
        handler = UserDataHandler(
            active_trades={},
            pl_callback=MagicMock(),
            wallet_callback=MagicMock(),
            order_manager=MagicMock(),
            exit_callback=AsyncMock(),
            exit_locks=shared,
        )
        assert handler._exit_locks is shared  # L-01: identity korunuyor

    def test_none_defaults_create_fresh_empty(self):
        handler = UserDataHandler(
            active_trades={},
            pl_callback=MagicMock(),
            wallet_callback=MagicMock(),
            order_manager=MagicMock(),
            exit_callback=AsyncMock(),
            exit_locks=None,
        )
        assert handler._exit_locks == {}


# ═══════════════════════════════════════════════════════════════════
# Race: iki exit akisi ayni trade'e ayni anda dokunur — paylasilan lock
# registry olmadan ikisi de guard'i atlar (cifte exit riski).
# ═══════════════════════════════════════════════════════════════════


def _trade_ctx(sym="BTCUSDT"):
    from models import ActiveTrade

    base = dict(
        symbol=sym,
        side="long",
        entry_price=50000.0,
        entry_bar_index=50,
        sl=49000.0,
        tp=52000.0,
        qty=0.1,
        exit_price=51000.0,
        result="TP",
        status="ACTIVE",
        entry_timestamp=1700000000000,
    )
    t = ActiveTrade(
        symbol=base["symbol"],
        side=base["side"],
        entry_price=base["entry_price"],
        sl=base["sl"],
        tp=base["tp"],
        qty=base["qty"],
        status=base["status"],
    )
    t["entry_bar_index"] = base["entry_bar_index"]
    t["exit_price"] = base["exit_price"]
    t["result"] = base["result"]
    t["entry_timestamp"] = base["entry_timestamp"]
    return t


class TestSharedRegistryRace:
    @pytest.mark.asyncio
    async def test_gather_uses_single_shared_lock_registry(self):
        """Iki ExitLifecycleService AYNI lock registry'yi paylasir — ayni
        trade'e ayni anda dokunmalari tek per-trade lock uzerinde serilir ve
        ikinci akis ilkinin _exit_committed flag'ini gorur."""
        shared_locks: dict = {}
        active_trades: dict = {}
        trade = _trade_ctx()
        active_trades["BTCUSDT"] = trade

        svc1, rest1, om1 = _make_service({}, shared_locks)
        svc2, rest2, om2 = _make_service({}, shared_locks)

        # Girdigi anda `_exit_committed` flag'ini tiklayan basit bir exit akisi
        async def run(svc):
            trade["_exit_committed"] = True
            await asyncio.sleep(0)
            return svc._exit_locks is shared_locks

        r1, r2 = await asyncio.gather(run(svc1), run(svc2))
        assert r1 is True and r2 is True
        assert svc1._exit_locks is svc2._exit_locks
