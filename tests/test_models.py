import pytest
from src.models import (
    Bar,
    FVG,
    CHoCH,
    SwingPoint,
    FVGQuality,
    AnalysisResult,
    tf_params,
)


class TestBar:
    def test_valid_bar(self):
        b = Bar(index=0, open=100, high=105, low=95, close=102)
        assert b.index == 0
        assert b.body == 2.0
        assert b.upper_wick == 3.0
        assert b.lower_wick == 5.0
        assert b.range == 10.0

    def test_bearish_bar_properties(self):
        b = Bar(index=1, open=105, high=110, low=95, close=100)
        assert b.body == 5.0
        assert b.upper_wick == 5.0
        assert b.lower_wick == 5.0
        assert b.range == 15.0

    def test_invalid_high_low(self):
        with pytest.raises(ValueError, match="high.*< low"):
            Bar(index=0, open=100, high=95, low=105, close=102)

    def test_open_out_of_range(self):
        with pytest.raises(ValueError, match="open.*out of"):
            Bar(index=0, open=200, high=105, low=95, close=102)

    def test_close_out_of_range(self):
        with pytest.raises(ValueError, match="close.*out of"):
            Bar(index=0, open=100, high=105, low=95, close=200)

    def test_body_bullish(self):
        b = Bar(index=0, open=100, high=110, low=95, close=108)
        assert b.body == 8.0

    def test_body_bearish(self):
        b = Bar(index=0, open=108, high=110, low=95, close=100)
        assert b.body == 8.0

    def test_is_closed_default(self):
        b = Bar(index=0, open=100, high=105, low=95, close=102)
        assert b.is_closed is True


class TestFVG:
    def test_valid_bullish(self):
        f = FVG(direction="bullish", top=110, bottom=105, real_index=5)
        assert f.size == 5.0
        assert f.midpoint == 107.5
        assert f.is_active is True

    def test_valid_bearish(self):
        f = FVG(direction="bearish", top=110, bottom=105, real_index=5)
        assert f.size == 5.0
        assert f.is_active is True

    def test_invalid_top_bottom(self):
        with pytest.raises(ValueError, match="top.*<= bottom"):
            FVG(direction="bullish", top=100, bottom=105, real_index=5)

    def test_mark_filled_bullish(self):
        f = FVG(direction="bullish", top=110, bottom=105, real_index=5)
        assert f.mark_filled(104) is True
        assert f.filled is True
        assert f.is_active is False

    def test_mark_filled_bullish_not_reached(self):
        f = FVG(direction="bullish", top=110, bottom=105, real_index=5)
        assert f.mark_filled(106) is False
        assert f.filled is False

    def test_mark_filled_bearish(self):
        f = FVG(direction="bearish", top=110, bottom=105, real_index=5)
        assert f.mark_filled(111) is True
        assert f.filled is True

    def test_mark_filled_bearish_not_reached(self):
        f = FVG(direction="bearish", top=110, bottom=105, real_index=5)
        assert f.mark_filled(108) is False
        assert f.filled is False

    def test_invalidated_not_active(self):
        f = FVG(
            direction="bullish", top=110, bottom=105, real_index=5, invalidated=True
        )
        assert f.is_active is False

    def test_next_check_default(self):
        f = FVG(direction="bullish", top=110, bottom=105, real_index=5)
        assert f._next_check_abs == 7


class TestCHoCH:
    def test_valid(self):
        c = CHoCH(direction="bullish", level=100, bar_index=10, pivot_bar_index=5)
        assert c.direction == "bullish"

    def test_invalid_bar_index(self):
        with pytest.raises(ValueError, match="bar_index < pivot_bar_index"):
            CHoCH(direction="bullish", level=100, bar_index=3, pivot_bar_index=5)

    def test_age_bars(self):
        c = CHoCH(direction="bullish", level=100, bar_index=10, pivot_bar_index=5)
        assert c.age_bars(15) == 5
        assert c.age_bars(10) == 0
        assert c.age_bars(5) == 0


class TestSwingPoint:
    def test_mark_mitigated_high(self):
        sp = SwingPoint(kind="high", price=100, bar_index=5)
        assert sp.mark_mitigated(105) is True
        assert sp.mitigated is True

    def test_mark_mitigated_high_not_reached(self):
        sp = SwingPoint(kind="high", price=100, bar_index=5)
        assert sp.mark_mitigated(95) is False
        assert sp.mitigated is False

    def test_mark_mitigated_low(self):
        sp = SwingPoint(kind="low", price=100, bar_index=5)
        assert sp.mark_mitigated(95) is True
        assert sp.mitigated is True

    def test_mark_mitigated_low_not_reached(self):
        sp = SwingPoint(kind="low", price=100, bar_index=5)
        assert sp.mark_mitigated(105) is False
        assert sp.mitigated is False


class TestFVGQuality:
    def test_valid(self):
        q = FVGQuality(
            displacement=0.5, fvg_size=0.3, sweep=0.8, retest=0.2, score=0.45
        )
        assert q.score == 0.45
        assert q.is_valid is True

    def test_zero_score_not_valid(self):
        q = FVGQuality(displacement=0.5, fvg_size=0.3, sweep=0.8, retest=0.2, score=0.0)
        assert q.is_valid is False

    def test_out_of_range_displacement(self):
        with pytest.raises(ValueError, match="displacement.*out of range"):
            FVGQuality(
                displacement=1.5, fvg_size=0.3, sweep=0.8, retest=0.2, score=0.45
            )

    def test_out_of_range_score(self):
        with pytest.raises(ValueError, match="score.*out of range"):
            FVGQuality(
                displacement=0.5, fvg_size=0.3, sweep=0.8, retest=0.2, score=-0.1
            )


class TestAnalysisResult:
    def test_expected_choch_direction_long(self):
        r = AnalysisResult(symbol="BTCUSDT", direction="long")
        assert r.expected_choch_direction == "bullish"

    def test_expected_choch_direction_short(self):
        r = AnalysisResult(symbol="BTCUSDT", direction="short")
        assert r.expected_choch_direction == "bearish"

    def test_expected_choch_direction_none(self):
        r = AnalysisResult(symbol="BTCUSDT")
        assert r.expected_choch_direction is None

    def test_is_valid_signal_true(self):
        q = FVGQuality(
            displacement=0.5, fvg_size=0.3, sweep=0.8, retest=0.2, score=0.45
        )
        r = AnalysisResult(symbol="BTCUSDT", direction="long", fvg_quality=q)
        assert r.is_valid_signal() is True

    def test_is_valid_signal_false(self):
        r = AnalysisResult(symbol="BTCUSDT", direction="long")
        assert r.is_valid_signal() is False

    def test_summary(self):
        r = AnalysisResult(symbol="BTCUSDT", direction="long")
        s = r.summary()
        assert "BTCUSDT" in s
        assert "long" in s


class TestTfParams:
    def test_known_timeframe(self):
        assert tf_params("15m") == (15, 10, 1)

    def test_case_insensitive(self):
        assert tf_params("15M") == (15, 10, 1)

    def test_unknown_timeframe_default(self):
        assert tf_params("13m") == (15, 10, 2)
