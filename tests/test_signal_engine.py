"""
test_signal_engine.py — SignalEngine.progress_rsm / evaluate_trigger unit tests.
Bias Kilit Modu (BIAS_LOCKED) dallarini kapsar.
"""

from models import Bar
from retrace_state import RetraceStateMachine, RetraceState
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


class TestProgressRsmRestoredBiasLock:
    """Rapor 4: restore_bias_lock sonrasi progress_rsm yeni sweep ISTEMEZ;
    sadece on_bias_fvg (kilit yonunde) calisir. Restart'la tutarli davranis."""

    def _restored_engine(self, direction="bullish", locked_from=0):
        rsm = RetraceStateMachine()
        rsm.restore_bias_lock(direction, locked_from_bar=locked_from)
        return rsm, SignalEngine(rsm)

    def test_restored_lock_does_not_request_new_sweep(self):
        """sweep_confirmed=True (latch restore'uyla gelir) olsa bile RSM
        BIAS_LOCKED'ta kalir — on_sweep asla cagrilmaz."""
        rsm, engine = self._restored_engine(locked_from=2)
        ss = SessionState()
        ss.daily_bias = DailyBias.BULLISH
        ss.sweep_confirmed = True
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.state_name == "BIAS_LOCKED"
        assert rsm.bias_locked is True

    def test_restored_lock_triggers_on_fresh_locked_fvg(self):
        """Restore sonrasi kilit yonunde taze FVG wick rejection'i -> TRIGGER_READY
        (yeni sweep olmadan FVG-only devam eder)."""
        rsm, engine = self._restored_engine(locked_from=0)
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
        assert rsm.state_name == "TRIGGER_READY"
        assert ss.fvg_ready is True

    def test_restored_lock_resets_on_opposing_bias(self):
        """Restore edilen kilit, ss.daily_bias ile celisirse reset (bias_conflict)
        — canli sifirdan baslamis gibi IDLE'a doner."""
        rsm, engine = self._restored_engine(direction="bullish", locked_from=0)
        ss = SessionState()
        ss.daily_bias = DailyBias.BEARISH
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.state_name == "IDLE"

    def test_restored_lock_keeps_state_without_fvg(self):
        """Taze FVG yokken BIAS_LOCKED korunur (reset yok)."""
        rsm, engine = self._restored_engine(direction="bearish", locked_from=10)
        ss = SessionState()
        ss.daily_bias = DailyBias.BEARISH
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        engine.progress_rsm(bars, bars[4], ss)
        assert rsm.state_name == "BIAS_LOCKED"


class TestIFVGBiasExemption:
    """IFVG bias muafiyeti: IFVG kaynakli trigger'lar daily-bias filtresinden
    muaf tutulur. NORMAL trigger'lar icin mevcut filtre davranisi korunur.
    (Devir eki: ifvg-direktif-ek-devir.md)"""

    def _make_ifvg_trigger(self, direction="bullish"):
        """IFVG kaynakli TRIGGER_READY durumu olustur."""
        rsm = RetraceStateMachine()
        # Bir FVG kirilmasi -> inverted candidate kaydi
        fvg = type(
            "HTFFVG",
            (),
            {
                "top": 108.0,
                "bottom": 105.0,
                "direction": "bearish",
                "bar_index": 2,
            },
        )()
        rsm._register_inverted(fvg)
        # IFVG retest tetikle
        rsm._last_trigger_source = "IFVG"
        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = direction
        rsm.trigger_fvg = fvg
        return rsm

    def _make_normal_trigger(self, direction="bullish"):
        """NORMAL (sweep+FVG) kaynakli TRIGGER_READY durumu olustur."""
        rsm = RetraceStateMachine()
        rsm.on_sweep(direction, 105.0)
        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = direction
        rsm.trigger_fvg = type(
            "HTFFVG",
            (),
            {
                "top": 108.0,
                "bottom": 105.0,
                "direction": direction,
                "bar_index": 2,
            },
        )()
        rsm._last_trigger_source = "NORMAL"
        return rsm

    def test_ifvg_bullish_trigger_passes_bearish_bias(self):
        """IFVG bullish trigger + BEARISH bias -> TRIGGER (muaf)."""
        rsm = self._make_ifvg_trigger("bullish")
        engine = SignalEngine(rsm)
        ss = SessionState()
        ss.daily_bias = DailyBias.BEARISH
        ss.cbdr_start, ss.cbdr_end = 22, 2
        current = _bar(5, 100, 110, 99, 105, timestamp=14 * 3600 * 1000)
        res = engine.evaluate_trigger(current, ss)
        assert res.decision == "TRIGGER"
        assert res.direction == "bullish"

    def test_ifvg_bearish_trigger_passes_bullish_bias(self):
        """IFVG bearish trigger + BULLISH bias -> TRIGGER (muaf)."""
        rsm = self._make_ifvg_trigger("bearish")
        engine = SignalEngine(rsm)
        ss = SessionState()
        ss.daily_bias = DailyBias.BULLISH
        ss.cbdr_start, ss.cbdr_end = 22, 2
        current = _bar(5, 100, 110, 99, 105, timestamp=14 * 3600 * 1000)
        res = engine.evaluate_trigger(current, ss)
        assert res.decision == "TRIGGER"
        assert res.direction == "bearish"

    def test_ifvg_trigger_passes_neutral_bias(self):
        """IFVG trigger + NEUTRAL bias -> TRIGGER (muaf)."""
        rsm = self._make_ifvg_trigger("bullish")
        engine = SignalEngine(rsm)
        ss = SessionState()
        ss.daily_bias = DailyBias.NEUTRAL
        ss.cbdr_start, ss.cbdr_end = 22, 2
        current = _bar(5, 100, 110, 99, 105, timestamp=14 * 3600 * 1000)
        res = engine.evaluate_trigger(current, ss)
        assert res.decision == "TRIGGER"

    def test_normal_bullish_trigger_rejected_by_bearish_bias(self):
        """NORMAL bullish trigger + BEARISH bias -> SKIP (mevcut filtre korunur)."""
        rsm = self._make_normal_trigger("bullish")
        engine = SignalEngine(rsm)
        ss = SessionState()
        ss.daily_bias = DailyBias.BEARISH
        ss.cbdr_start, ss.cbdr_end = 22, 2
        current = _bar(5, 100, 110, 99, 105, timestamp=14 * 3600 * 1000)
        res = engine.evaluate_trigger(current, ss)
        assert res.decision == "SKIP"
        assert res.reason == "bias_bearish"
        assert rsm.state_name == "IDLE"

    def test_normal_bearish_trigger_rejected_by_bullish_bias(self):
        """NORMAL bearish trigger + BULLISH bias -> SKIP (mevcut filtre korunur)."""
        rsm = self._make_normal_trigger("bearish")
        engine = SignalEngine(rsm)
        ss = SessionState()
        ss.daily_bias = DailyBias.BULLISH
        ss.cbdr_start, ss.cbdr_end = 22, 2
        current = _bar(5, 100, 110, 99, 105, timestamp=14 * 3600 * 1000)
        res = engine.evaluate_trigger(current, ss)
        assert res.decision == "SKIP"
        assert res.reason == "bias_bullish"
        assert rsm.state_name == "IDLE"

    def test_normal_trigger_rejected_by_neutral_bias(self):
        """NORMAL trigger + NEUTRAL bias -> SKIP (mevcut filtre korunur)."""
        rsm = self._make_normal_trigger("bullish")
        engine = SignalEngine(rsm)
        ss = SessionState()
        ss.daily_bias = DailyBias.NEUTRAL
        ss.cbdr_start, ss.cbdr_end = 22, 2
        current = _bar(5, 100, 110, 99, 105, timestamp=14 * 3600 * 1000)
        res = engine.evaluate_trigger(current, ss)
        assert res.decision == "SKIP"
        assert res.reason == "bias_neutral"
        assert rsm.state_name == "IDLE"
