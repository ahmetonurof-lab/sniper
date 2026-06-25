from datetime import UTC, datetime

from src.session import (
    SessionState,
    SessionPhase,
    DailyBias,
    detect_phase,
    detect_phase_from_timestamp,
)


def _dt(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


class TestDetectPhase:
    def test_cbdr_22_00(self):
        assert detect_phase(_dt(22)) == SessionPhase.CBDR

    def test_cbdr_23_00(self):
        assert detect_phase(_dt(23)) == SessionPhase.CBDR

    def test_cbdr_01_00(self):
        assert detect_phase(_dt(1)) == SessionPhase.CBDR

    def test_london_02_00(self):
        assert detect_phase(_dt(2)) == SessionPhase.LONDON

    def test_london_08_00(self):
        assert detect_phase(_dt(8)) == SessionPhase.LONDON

    def test_london_12_00(self):
        assert detect_phase(_dt(12)) == SessionPhase.LONDON

    def test_newyork_13_00(self):
        assert detect_phase(_dt(13)) == SessionPhase.NEWYORK

    def test_newyork_18_00(self):
        assert detect_phase(_dt(18)) == SessionPhase.NEWYORK

    def test_newyork_21_00(self):
        assert detect_phase(_dt(21)) == SessionPhase.NEWYORK

    def test_invalid_int_returns_closed(self):
        assert detect_phase(123) == SessionPhase.CLOSED


class TestDetectPhaseFromTimestamp:
    def test_valid_timestamp(self):
        ts = int(_dt(14, 30).timestamp() * 1000)
        assert detect_phase_from_timestamp(ts) == SessionPhase.NEWYORK

    def test_zero_timestamp(self):
        assert detect_phase_from_timestamp(0) == SessionPhase.CLOSED

    def test_negative_timestamp(self):
        assert detect_phase_from_timestamp(-1) == SessionPhase.CLOSED


class TestSessionState:
    def test_initial_state(self):
        ss = SessionState()
        assert ss.cbdr_body_high == 0.0
        assert ss.cbdr_body_low == float("inf")
        assert ss.cbdr_locked is False
        assert ss.cbdr_day == ""
        assert ss.daily_bias == DailyBias.NEUTRAL
        assert ss.trades_today == 0
        assert ss.retrade_armed is False

    def test_update_tracks_cbdr_high(self):
        ss = SessionState()
        ss.update(_dt(23), open=100, high=110, low=90, close=105)
        assert ss.cbdr_body_high == 110

    def test_update_tracks_cbdr_low(self):
        ss = SessionState()
        ss.update(_dt(23), open=100, high=110, low=90, close=105)
        assert ss.cbdr_body_low == 90

    def test_cbdr_locks_at_hour_2(self):
        ss = SessionState()
        ss.update(_dt(23, day=1), open=100, high=110, low=90, close=105)
        ss.update(_dt(2, day=2), open=101, high=111, low=91, close=106)
        assert ss.cbdr_locked is True
        assert ss.cbdr_body_high == 110
        assert ss.cbdr_body_low == 90

    def test_cbdr_not_locked_before_hour_2(self):
        ss = SessionState()
        ss.update(_dt(23), open=100, high=110, low=90, close=105)
        ss.update(_dt(1), open=101, high=111, low=91, close=106)
        assert ss.cbdr_locked is False

    def test_update_tracks_london(self):
        ss = SessionState()
        ss.update(_dt(8), open=100, high=120, low=80, close=110)
        assert ss.london_high == 120
        assert ss.london_low == 80

    def test_london_high_updates(self):
        ss = SessionState()
        ss.update(_dt(8), open=100, high=110, low=90, close=105)
        ss.update(_dt(10), open=106, high=130, low=95, close=125)
        assert ss.london_high == 130

    def test_ny_inherits_london_if_zero(self):
        ss = SessionState()
        ss.update(_dt(14), open=100, high=110, low=90, close=105)
        assert ss.london_high == 110
        assert ss.london_low == 90

    def test_reset_for_new_cbdr_cycle(self):
        ss = SessionState()
        ss.update(_dt(23, day=1), open=100, high=110, low=90, close=105)
        ss.update(_dt(1, day=2), open=101, high=111, low=91, close=106)
        ss.trades_today = 3
        ss.retrade_armed = True
        ss.retrade_side = "long"
        ss.update(_dt(23, day=2), open=102, high=112, low=92, close=107)
        assert ss.trades_today == 0
        assert ss.retrade_armed is False
        assert ss.cbdr_body_high == 112
        assert ss.cbdr_body_low == 92

    def test_trades_today_increments(self):
        ss = SessionState()
        assert ss.trades_today == 0
        ss.trades_today += 1
        assert ss.trades_today == 1


class TestCheckCbdrSweep:
    def test_bullish_sweep(self):
        ss = SessionState()
        ss.cbdr_body_high = 110
        ss.cbdr_body_low = 100
        ss._check_cbdr_sweep(high=120, low=95, close=105, atr=10)
        assert ss.sweep_confirmed is True
        assert ss.sweep_direction == "bullish"
        assert ss.daily_bias == DailyBias.BULLISH

    def test_bearish_sweep(self):
        ss = SessionState()
        ss.cbdr_body_high = 110
        ss.cbdr_body_low = 100
        ss._check_cbdr_sweep(high=115, low=90, close=105, atr=10)
        assert ss.sweep_confirmed is True
        assert ss.sweep_direction == "bearish"
        assert ss.daily_bias == DailyBias.BEARISH

    def test_no_sweep_inside_range(self):
        ss = SessionState()
        ss.cbdr_body_high = 110
        ss.cbdr_body_low = 100
        ss._check_cbdr_sweep(high=108, low=102, close=105, atr=10)
        assert ss.sweep_confirmed is False

    def test_sweep_needs_close_to_reject(self):
        ss = SessionState()
        ss.cbdr_body_high = 110
        ss.cbdr_body_low = 100
        ss._check_cbdr_sweep(high=125, low=95, close=112, atr=10)
        assert ss.sweep_direction is None
