from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models import Bar


def bar(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    is_closed: bool = True,
    timestamp: int = 0,
) -> Bar:
    return Bar(
        index=index,
        open=open_,
        high=high,
        low=low,
        close=close,
        is_closed=is_closed,
        timestamp=timestamp,
    )


# ─── Fixture schema ────────────────────────────────────────────────


@dataclass
class GoldenFixture:
    fixture_id: str
    symbol: str = "BNBUSDT"
    timeframe: str = "15m"
    bars: list[Bar] = field(default_factory=list)
    atr_period: int = 14
    atr_value: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    cbdr_body_high: float = 0.0
    cbdr_body_low: float = float("inf")
    body_locked: bool = True
    tolerance: float = 0.0
    min_fvg_size: float = 1e-8
    since_index: int | None = None
    direction_filter: str | None = None


# ═══════════════════════════════════════════════════════════════════
# CBDR Sweep Fixtures (S01–S15)
# ═══════════════════════════════════════════════════════════════════

# Her fixture'da body_low=100.0, body_high=110.0 olarak kurgulandi.
# tolerance = atr * 0.5 (CBDR_SWEEP_ATR_TOLERANCE_MULT)
# atr=10 -> tolerance=5.0

CBDR_BODY_HIGH: float = 110.0
CBDR_BODY_LOW: float = 100.0
CBDR_ATR: float = 10.0
CBDR_TOL: float = 5.0


def _cbdr_base(overrides: dict | None = None) -> dict:
    base = {
        "cbdr_body_high": CBDR_BODY_HIGH,
        "cbdr_body_low": CBDR_BODY_LOW,
        "body_locked": True,
        "atr_value": CBDR_ATR,
        "config": {
            "CBDR_SWEEP_ATR_TOLERANCE_MULT": 0.5,
            "FVG_MIN_SIZE_ATR_MULT": None,
            "FVG_BUFFER_MIN_FACTOR": None,
        },
    }
    if overrides:
        base.update(overrides)
    return base


# S01: Bullish sweep — low < body_low - tolerance, close > body_low
# low < 95, close > 100
FIXTURE_S01: dict = _cbdr_base(
    {
        "fixture_id": "S01",
        "bars": [bar(0, 105, 108, 94, 101)],
        "expected": {
            "sweep_confirmed": True,
            "sweep_direction": "bullish",
            "sweep_level": CBDR_BODY_LOW,
        },
    }
)

# S02: Bearish sweep — high > body_high + tolerance, close < body_high
# high > 115, close < 110
FIXTURE_S02: dict = _cbdr_base(
    {
        "fixture_id": "S02",
        "bars": [bar(0, 108, 116, 105, 109)],
        "expected": {
            "sweep_confirmed": True,
            "sweep_direction": "bearish",
            "sweep_level": CBDR_BODY_HIGH,
        },
    }
)

# S03: Tolerance boundary — low == body_low - tolerance (95)
FIXTURE_S03: dict = _cbdr_base(
    {
        "fixture_id": "S03",
        "bars": [bar(0, 105, 108, 95.0, 102)],
        "expected": {
            "sweep_confirmed": False,
            "sweep_direction": None,
            "sweep_level": None,
        },
    }
)

# S04: Upper wick exists but close is not inside
FIXTURE_S04: dict = _cbdr_base(
    {
        "fixture_id": "S04",
        "bars": [bar(0, 108, 116, 107, 110)],
        "expected": {
            "sweep_confirmed": False,
            "sweep_direction": None,
            "sweep_level": None,
        },
    }
)

# S05: Lower wick exists but close is not inside
FIXTURE_S05: dict = _cbdr_base(
    {
        "fixture_id": "S05",
        "bars": [bar(0, 105, 108, 94, 100)],
        "expected": {
            "sweep_confirmed": False,
            "sweep_direction": None,
            "sweep_level": None,
        },
    }
)

# S06: Body not locked — locked=False
FIXTURE_S06: dict = _cbdr_base(
    {
        "fixture_id": "S06",
        "bars": [bar(0, 105, 108, 94, 101)],
        "body_locked": False,
        "expected": {
            "sweep_confirmed": False,
            "sweep_direction": None,
            "sweep_level": None,
        },
    }
)

# S07: ATR zero/NaN
FIXTURE_S07_ZERO: dict = _cbdr_base(
    {
        "fixture_id": "S07_zero",
        "bars": [bar(0, 105, 108, 94, 101)],
        "atr_value": 0.0,
        "expected": {
            "sweep_confirmed": False,
            "sweep_direction": None,
            "sweep_level": None,
        },
    }
)

FIXTURE_S07_NAN: dict = _cbdr_base(
    {
        "fixture_id": "S07_nan",
        "bars": [bar(0, 105, 108, 94, 101)],
        "atr_value": float("nan"),
        "expected": {
            "sweep_confirmed": False,
            "sweep_direction": None,
            "sweep_level": None,
        },
    }
)

# S08: ATR variation — same bar, different ATR -> different tolerance
FIXTURE_S08: dict = _cbdr_base(
    {
        "fixture_id": "S08",
        "bars": [bar(0, 105, 108, 96, 101)],
        "expected": {
            "note": "same_bar_diff_atr",
        },
    }
)

# S09: Both sides same bar
FIXTURE_S09: dict = _cbdr_base(
    {
        "fixture_id": "S09",
        "bars": [bar(0, 105, 116, 94, 101)],
        "expected": {
            "sweep_confirmed": True,
            "sweep_direction": None,
        },
    }
)

# S10: Duplicate bar event
FIXTURE_S10: dict = _cbdr_base(
    {
        "fixture_id": "S10",
        "bars": [bar(0, 105, 108, 94, 101)],
        "expected": {
            "sweep_confirmed": True,
        },
    }
)

# S11: Out-of-order bar
FIXTURE_S11: dict = _cbdr_base(
    {
        "fixture_id": "S11",
        "bars": [
            bar(1, 108, 116, 105, 109, timestamp=2000),
            bar(0, 105, 108, 94, 101, timestamp=1000),
        ],
        "expected": {
            "sweep_confirmed": True,
        },
    }
)

# S12: Backtest/live snapshot match
FIXTURE_S12: dict = _cbdr_base(
    {
        "fixture_id": "S12",
        "bars": [bar(0, 105, 108, 94, 101, is_closed=True)],
        "expected": {
            "sweep_confirmed": True,
            "sweep_direction": "bullish",
        },
    }
)

# S13: Open 15m bar
FIXTURE_S13: dict = _cbdr_base(
    {
        "fixture_id": "S13",
        "bars": [bar(0, 105, 108, 94, 101, is_closed=False)],
        "expected": {
            "backtest_sweep": True,
            "live_sweep": False,
        },
    }
)

# S14: Session state reset
FIXTURE_S14: dict = _cbdr_base(
    {
        "fixture_id": "S14",
        "bars": [bar(0, 105, 108, 94, 101)],
        "cbdr_body_high": 0.0,
        "cbdr_body_low": float("inf"),
        "body_locked": False,
        "expected": {
            "sweep_confirmed": False,
        },
    }
)

# S15: Tolerance config change
FIXTURE_S15: dict = _cbdr_base(
    {
        "fixture_id": "S15",
        "bars": [bar(0, 105, 108, 97, 101)],
        "expected": {
            "sweep_confirmed": False,
        },
    }
)


# ═══════════════════════════════════════════════════════════════════
# FVG Fixtures (F01–F20)
# ═══════════════════════════════════════════════════════════════════


def _fvg_base(overrides: dict | None = None) -> dict:
    base = {
        "timeframe": "15m",
        "min_fvg_size": 1e-8,
        "since_index": None,
    }
    if overrides:
        base.update(overrides)
    return base


# F01: Valid bullish FVG
FIXTURE_F01: dict = _fvg_base(
    {
        "fixture_id": "F01",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "expected": {"count": 1, "direction": "bullish", "top": 107.0, "bottom": 105.0},
    }
)

# F02: Valid bearish FVG
FIXTURE_F02: dict = _fvg_base(
    {
        "fixture_id": "F02",
        "bars": [
            bar(0, 110, 115, 105, 108),
            bar(1, 106, 109, 99, 102),
            bar(2, 98, 104, 94, 100),
        ],
        "expected": {"count": 1, "direction": "bearish", "top": 105.0, "bottom": 104.0},
    }
)

# F03: Gap exactly at min_size boundary
FIXTURE_F03: dict = _fvg_base(
    {
        "fixture_id": "F03",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "min_fvg_size": 2.0,
        "expected": {"count": 1},
    }
)

# F04: Gap below min_size
FIXTURE_F04: dict = _fvg_base(
    {
        "fixture_id": "F04",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "min_fvg_size": 3.0,
        "expected": {"count": 0},
    }
)

# F05: Zero height (overlapping bars, no gap)
FIXTURE_F05: dict = _fvg_base(
    {
        "fixture_id": "F05",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 104, 108, 100, 106),
        ],
        "expected": {"count": 0},
    }
)

# F06: No gap (overlap)
FIXTURE_F06: dict = _fvg_base(
    {
        "fixture_id": "F06",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 104, 108, 100, 106),
        ],
        "expected": {"count": 0},
    }
)

# F07: ATR zero — min_fvg_size=0, all gaps pass
FIXTURE_F07: dict = _fvg_base(
    {
        "fixture_id": "F07",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "min_fvg_size": 0.0,
        "expected": {"count": 1},
    }
)

# F08: ATR NaN — min_fvg_size=0, all gaps pass
FIXTURE_F08: dict = _fvg_base(
    {
        "fixture_id": "F08",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "min_fvg_size": 0.0,
        "expected": {"count": 1},
    }
)

# F09: Multiple FVGs in order
FIXTURE_F09: dict = _fvg_base(
    {
        "fixture_id": "F09",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
            bar(3, 105, 110, 100, 108),
            bar(4, 100, 107, 93, 95),
            bar(5, 94, 100, 90, 96),
            bar(6, 85, 90, 82, 88),
            bar(7, 80, 88, 78, 85),
        ],
        "expected": {"count": 3},
    }
)

# F10: Last FVG selection
FIXTURE_F10: dict = _fvg_base(
    {
        "fixture_id": "F10",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
            bar(3, 110, 115, 105, 112),
            bar(4, 120, 122, 118, 120),
            bar(5, 118, 125, 115, 122),
        ],
        "expected": {"count": 2, "last_index": 4},
    }
)

# F11: Entry scope — since_index filter
FIXTURE_F11: dict = _fvg_base(
    {
        "fixture_id": "F11",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
            bar(3, 105, 110, 100, 108),
            bar(4, 102, 107, 96, 104),
            bar(5, 94, 100, 90, 96),
            bar(6, 88, 95, 85, 93),
        ],
        "since_index": 3,
        "expected": {"count": 1},
    }
)

# F12: Trailing scope empty
FIXTURE_F12: dict = _fvg_base(
    {
        "fixture_id": "F12",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "since_index": 5,
        "expected": {"count": 0},
    }
)

# F13: Timeframe consistency
FIXTURE_F13: dict = _fvg_base(
    {
        "fixture_id": "F13",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "expected": {"count": 1},
    }
)

# F14: Unclosed bar
FIXTURE_F14: dict = _fvg_base(
    {
        "fixture_id": "F14",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110, is_closed=False),
        ],
        "expected": {"count": 0},
    }
)

# F15: Duplicate event
FIXTURE_F15: dict = _fvg_base(
    {
        "fixture_id": "F15",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "expected": {"count": 1},
    }
)

# F16: Out-of-order bars (by timestamp, but detect_fvgs uses list order)
FIXTURE_F16: dict = _fvg_base(
    {
        "fixture_id": "F16",
        "bars": [
            bar(2, 98, 104, 94, 100, timestamp=3000),
            bar(1, 106, 109, 99, 102, timestamp=2000),
            bar(0, 110, 115, 105, 108, timestamp=1000),
        ],
        "expected": {"count": 1},
    }
)

# F17: Symbol agnostic
FIXTURE_F17: dict = _fvg_base(
    {
        "fixture_id": "F17",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "expected": {"count": 1},
    }
)

# F18: Direction filter — multiple FVGs with different directions
FIXTURE_F18: dict = _fvg_base(
    {
        "fixture_id": "F18",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
            bar(3, 110, 115, 105, 108),
            bar(4, 106, 109, 99, 102),
            bar(5, 98, 104, 94, 100),
        ],
        "expected": {"count": 2, "bullish_count": 1, "bearish_count": 1},
    }
)

# F19: Analyzer vs retrace — same snapshot
FIXTURE_F19: dict = _fvg_base(
    {
        "fixture_id": "F19",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "expected": {"count": 1},
    }
)

# F20: Analyzer vs trailing
FIXTURE_F20: dict = _fvg_base(
    {
        "fixture_id": "F20",
        "bars": [
            bar(0, 100, 105, 95, 102),
            bar(1, 103, 106, 96, 104),
            bar(2, 108, 112, 107, 110),
        ],
        "expected": {"count": 1},
    }
)


# ─── All fixture registry ──────────────────────────────────────────


CBDR_FIXTURES: dict[str, dict] = {
    "S01": FIXTURE_S01,
    "S02": FIXTURE_S02,
    "S03": FIXTURE_S03,
    "S04": FIXTURE_S04,
    "S05": FIXTURE_S05,
    "S06": FIXTURE_S06,
    "S07_zero": FIXTURE_S07_ZERO,
    "S07_nan": FIXTURE_S07_NAN,
    "S08": FIXTURE_S08,
    "S09": FIXTURE_S09,
    "S10": FIXTURE_S10,
    "S11": FIXTURE_S11,
    "S12": FIXTURE_S12,
    "S13": FIXTURE_S13,
    "S14": FIXTURE_S14,
    "S15": FIXTURE_S15,
}

FVG_FIXTURES: dict[str, dict] = {
    "F01": FIXTURE_F01,
    "F02": FIXTURE_F02,
    "F03": FIXTURE_F03,
    "F04": FIXTURE_F04,
    "F05": FIXTURE_F05,
    "F06": FIXTURE_F06,
    "F07": FIXTURE_F07,
    "F08": FIXTURE_F08,
    "F09": FIXTURE_F09,
    "F10": FIXTURE_F10,
    "F11": FIXTURE_F11,
    "F12": FIXTURE_F12,
    "F13": FIXTURE_F13,
    "F14": FIXTURE_F14,
    "F15": FIXTURE_F15,
    "F16": FIXTURE_F16,
    "F17": FIXTURE_F17,
    "F18": FIXTURE_F18,
    "F19": FIXTURE_F19,
    "F20": FIXTURE_F20,
}
