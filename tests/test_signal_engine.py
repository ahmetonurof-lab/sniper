"""
test_signal_engine.py — SignalEngine.progress_rsm / evaluate_trigger unit tests.
Bias Kilit Modu (BIAS_LOCKED) dallarini kapsar.
"""

from models import Bar
from retrace_state import RetraceStateMachine
from session import DailyBias, SessionState
from trading.signal_engine import EvalResult, SignalEngine


def _bar(index, open_, high, low, close, is_closed=True, timestamp=0):
    return Bar(
        index=index,
        open=open_,
        high=high,
        low=low,
        close=close,
        is_closed=is_closed,
        timestamp=timestamp,
    )


def _make_engine_bullish_locked(locked_from=0):
    rsm = RetraceStateMachine()
    rsm.on_sweep("bullish", 105.0)
    rsm.lock_bias(bar_index=locked_from)
    return rsm, SignalEngine(rsm)


class TestProgressRsmBiasLock:
    def test_locked_bias_keeps_state_when_bias_matches(self):
        rsm, engine = _make_engine_bullish_locked(locked_from=0)
        ss = SessionState()
        ss.daily_bias = DailyBias.BULLISH
        # Kilit sonrasi taze FVG yok -> BIAS_LOCKED'te kalir, reset olmaz
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.state_name == "BIAS_LOCKED"

    def test_locked_bias_resets_on_opposing_bias(self):
        rsm, engine = _make_engine_bullish_locked(locked_from=0)
        ss = SessionState()
        ss.daily_bias = DailyBias.BEARISH  # bullish kilide ters
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.state_name == "IDLE"

    def test_locked_bias_resets_on_neutral_bias(self):
        rsm, engine = _make_engine_bullish_locked(locked_from=0)
        ss = SessionState()
        ss.daily_bias = DailyBias.NEUTRAL  # yeni CBDR gunu
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.state_name == "IDLE"

    def test_locked_bias_triggers_ready_on_fresh_fvg(self):
        rsm, engine = _make_engine_bullish_locked(locked_from=0)
        ss = SessionState()
        ss.daily_bias = DailyBias.BULLISH
        # Bar 2'de bullish FVG, bar 4 wick ile dokunur -> TRIGGER_READY
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
            _bar(4, 110, 113, 104, 112),
        ]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.state_name == "TRIGGER_READY"
        assert ss.fvg_ready is True

    def test_evaluate_trigger_triggers_from_locked_fvg(self):
        rsm, engine = _make_engine_bullish_locked(locked_from=0)
        ss = SessionState()
        ss.daily_bias = DailyBias.BULLISH
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
            _bar(4, 110, 113, 104, 112),
        ]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.can_trigger()
        # Current bar'i CBDR penceresi (22-2) DISINA al: saat 14 UTC
        current = _bar(4, 110, 113, 104, 112, timestamp=14 * 3600 * 1000)
        res = engine.evaluate_trigger(current, ss)
        assert isinstance(res, EvalResult)
        assert res.decision == "TRIGGER"
        assert res.direction == "bullish"
        assert res.trigger_fvg is not None
