from __future__ import annotations

import os
from typing import Any

import pytest

from golden.fixtures import CBDR_FIXTURES, FVG_FIXTURES
from golden.runners import (
    configure_golden_log,
    data_snapshot_hash,
    run_cbdr_backtest,
    run_cbdr_live,
    run_fvg_backtest,
    run_fvg_signal,
    run_fvg_trailing,
    normalize_sweep_result,
    normalize_fvgs,
    write_golden_log,
)


@pytest.fixture(scope="session", autouse=True)
def _setup_golden_log():
    log_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "output", "golden_test.log"
    )
    configure_golden_log(log_path)


@pytest.fixture
def golden_cbdr_fixture(request) -> dict[str, Any]:
    fixture_id = request.param
    fx = CBDR_FIXTURES[fixture_id]
    return dict(fx)


@pytest.fixture
def golden_fvg_fixture(request) -> dict[str, Any]:
    fixture_id = request.param
    fx = FVG_FIXTURES[fixture_id]
    return dict(fx)


@pytest.fixture
def run_and_log_cbdr():
    def _run(fx: dict, consumer: str = "backtest") -> dict:
        if consumer == "live":
            result = run_cbdr_live(fx)
        else:
            result = run_cbdr_backtest(fx)

        normalized = normalize_sweep_result(result)
        snapshot_hash = data_snapshot_hash(
            fx["bars"],
            {
                "body_high": fx.get("cbdr_body_high"),
                "body_low": fx.get("cbdr_body_low"),
            },
        )

        record = {
            "event_type": "cbdr_sweep_invocation",
            "consumer": consumer,
            "fixture_id": fx["fixture_id"],
            "data_snapshot_hash": snapshot_hash,
            "result": normalized,
        }
        write_golden_log(record)
        return normalized

    return _run


@pytest.fixture
def run_and_log_fvg():
    def _run(fx: dict, consumer: str = "backtest") -> dict:
        if consumer == "signal":
            fvgs = run_fvg_signal(fx)
        elif consumer == "trailing":
            fvgs = run_fvg_trailing(fx)
        else:
            fvgs = run_fvg_backtest(fx)

        normalized = normalize_fvgs(fvgs)
        snapshot_hash = data_snapshot_hash(
            fx["bars"],
            {
                "min_fvg_size": fx.get("min_fvg_size"),
                "timeframe": fx.get("timeframe"),
            },
        )

        record = {
            "event_type": "fvg_invocation",
            "consumer": consumer,
            "fixture_id": fx["fixture_id"],
            "data_snapshot_hash": snapshot_hash,
            "input_bar_count": len(fx["bars"]),
            "closed_bar_count": sum(1 for b in fx["bars"] if b.is_closed),
            "min_fvg_size": fx.get("min_fvg_size"),
            "all_fvgs": normalized,
            "selected_fvg": normalized[-1] if normalized else None,
        }
        write_golden_log(record)
        return normalized

    return _run
