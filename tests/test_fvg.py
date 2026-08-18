from models import Bar, FVG
from fvg import (
    detect_fvgs,
    update_fvg_states,
    find_latest_unfilled_fvg,
    is_retesting_fvg,
    cleanup_fvgs,
    refresh_fvg_list,
    fvg_is_alive,
)


def _bar(index, open_, high, low, close, is_closed=True):
    return Bar(
        index=index, open=open_, high=high, low=low, close=close, is_closed=is_closed
    )


class TestDetectFvgs:
    def test_bullish_gap(self):
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 106, 96, 104),
            _bar(2, 108, 112, 107, 110),
        ]
        result = detect_fvgs(bars, timeframe="5m")
        assert len(result) == 1
        assert result[0].direction == "bullish"
        assert result[0].top == 107
        assert result[0].bottom == 105
        assert result[0].real_index == 1

    def test_bearish_gap(self):
        bars = [
            _bar(0, 110, 115, 105, 108),
            _bar(1, 106, 109, 99, 102),
            _bar(2, 98, 104, 94, 100),
        ]
        result = detect_fvgs(bars, timeframe="5m")
        assert len(result) == 1
        assert result[0].direction == "bearish"
        assert result[0].top == 105
        assert result[0].bottom == 104
        assert result[0].real_index == 1

    def test_no_gap_no_fvg(self):
        bars = [
            _bar(0, 100, 110, 90, 105),
            _bar(1, 104, 112, 92, 106),
            _bar(2, 105, 111, 95, 108),
        ]
        result = detect_fvgs(bars, timeframe="5m")
        assert len(result) == 0

    def test_min_fvg_size_filter(self):
        bars = [
            _bar(0, 100, 101, 98, 100),
            _bar(1, 100, 102, 99, 101),
            _bar(2, 102, 105, 102, 104),
        ]
        result = detect_fvgs(bars, timeframe="5m", min_fvg_size=2)
        assert len(result) == 0
        result = detect_fvgs(bars, timeframe="5m", min_fvg_size=0.5)
        assert len(result) == 1

    def test_since_index_filter(self):
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 106, 96, 104),
            _bar(2, 108, 112, 107, 110),
            _bar(3, 105, 110, 100, 108),
            _bar(4, 102, 107, 96, 104),
            _bar(5, 94, 100, 90, 96),
            _bar(6, 88, 95, 85, 93),
        ]
        result = detect_fvgs(bars, timeframe="5m", since_index=3)
        assert len(result) >= 1

    def test_unclosed_next_bar_skipped(self):
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 106, 96, 104),
            _bar(2, 108, 112, 107, 110, is_closed=False),
        ]
        result = detect_fvgs(bars, timeframe="5m")
        assert len(result) == 0

    def test_inside_bar_no_gap(self):
        bars = [
            _bar(0, 100, 110, 90, 105),
            _bar(1, 102, 108, 92, 104),
            _bar(2, 103, 109, 95, 106),
        ]
        result = detect_fvgs(bars, timeframe="5m")
        assert len(result) == 0

    def test_multiple_fvgs(self):
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 106, 96, 104),
            _bar(2, 108, 112, 107, 110),
            _bar(3, 105, 110, 100, 108),
            _bar(4, 102, 107, 96, 104),
            _bar(5, 94, 100, 90, 96),
            _bar(6, 88, 95, 85, 93),
            _bar(7, 80, 90, 75, 85),
        ]
        result = detect_fvgs(bars, timeframe="5m")
        assert len(result) >= 2


class TestUpdateFvgStates:
    def test_bullish_filled(self):
        fvg = FVG(direction="bullish", top=110, bottom=105, real_index=1)
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 108, 99, 104),
            _bar(2, 104, 108, 102, 106),
            _bar(3, 106, 112, 104, 107),
        ]
        update_fvg_states([fvg], bars)
        assert fvg.filled is True

    def test_bullish_invalidated(self):
        fvg = FVG(direction="bullish", top=110, bottom=105, real_index=1)
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 108, 99, 104),
            _bar(2, 102, 106, 100, 103),
            _bar(3, 100, 104, 98, 102),
        ]
        update_fvg_states([fvg], bars)
        assert fvg.invalidated is True

    def test_bearish_filled(self):
        fvg = FVG(direction="bearish", top=110, bottom=105, real_index=1)
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 108, 99, 104),
            _bar(2, 105, 110, 103, 108),
            _bar(3, 106, 112, 104, 108),
        ]
        update_fvg_states([fvg], bars)
        assert fvg.filled is True

    def test_bearish_invalidated(self):
        fvg = FVG(direction="bearish", top=110, bottom=105, real_index=1)
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 108, 99, 104),
            _bar(2, 106, 112, 102, 112),
            _bar(3, 108, 115, 105, 113),
        ]
        update_fvg_states([fvg], bars)
        assert fvg.invalidated is True

    def test_empty_bars_noop(self):
        fvg = FVG(direction="bullish", top=110, bottom=105, real_index=1)
        update_fvg_states([fvg], [])
        assert fvg.filled is False
        assert fvg.invalidated is False

    def test_bullish_filled_sticky_after_zone_exit(self):
        fvg = FVG(direction="bullish", top=110, bottom=105, real_index=1)
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 108, 99, 104),
            _bar(2, 104, 108, 102, 106),
            _bar(3, 106, 112, 104, 107),
            _bar(4, 107, 115, 105, 113),
        ]
        update_fvg_states([fvg], bars)
        assert fvg.filled is True
        assert fvg.invalidated is False

    def test_bearish_filled_sticky_after_zone_exit(self):
        fvg = FVG(direction="bearish", top=110, bottom=105, real_index=1)
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 108, 99, 104),
            _bar(2, 105, 110, 103, 108),
            _bar(3, 106, 112, 104, 108),
            _bar(4, 103, 110, 98, 99),
        ]
        update_fvg_states([fvg], bars)
        assert fvg.filled is True
        assert fvg.invalidated is False


class TestFindLatestUnfilledFvg:
    def test_returns_latest_by_index(self):
        fvgs = [
            FVG(direction="bullish", top=110, bottom=105, real_index=3, filled=True),
            FVG(direction="bullish", top=120, bottom=115, real_index=7),
            FVG(direction="bullish", top=130, bottom=125, real_index=5),
        ]
        result = find_latest_unfilled_fvg(fvgs, "bullish")
        assert result is not None
        assert result.real_index == 7

    def test_filters_by_direction(self):
        fvgs = [
            FVG(direction="bullish", top=110, bottom=105, real_index=5),
            FVG(direction="bearish", top=120, bottom=115, real_index=7),
        ]
        result = find_latest_unfilled_fvg(fvgs, "bearish")
        assert result is not None
        assert result.real_index == 7

    def test_returns_none_when_none_match(self):
        fvgs = [
            FVG(direction="bullish", top=110, bottom=105, real_index=5, filled=True),
        ]
        result = find_latest_unfilled_fvg(fvgs, "bullish")
        assert result is None

    def test_min_fvg_size_filter(self):
        fvgs = [
            FVG(direction="bullish", top=110, bottom=105, real_index=5),
            FVG(direction="bullish", top=120, bottom=119.5, real_index=7),
        ]
        result = find_latest_unfilled_fvg(fvgs, "bullish", min_fvg_size=1)
        assert result is not None
        assert result.real_index == 5


class TestIsRetestingFvg:
    def test_none_fvg_returns_false(self):
        bar = _bar(0, 100, 105, 95, 102)
        assert is_retesting_fvg(None, bar, atr=10) is False

    def test_inactive_fvg_returns_false(self):
        fvg = FVG(
            direction="bullish", top=110, bottom=105, real_index=1, invalidated=True
        )
        bar = _bar(0, 100, 105, 95, 102)
        assert is_retesting_fvg(fvg, bar, atr=10) is False

    def test_bullish_wick_touches_body_safe(self):
        fvg = FVG(direction="bullish", top=110, bottom=105, real_index=1)
        bar = _bar(2, 106, 112, 104, 108)
        result = is_retesting_fvg(fvg, bar, atr=10, atr_buffer_factor=0.1)
        assert result is True

    def test_bearish_wick_touches_body_safe(self):
        fvg = FVG(direction="bearish", top=110, bottom=105, real_index=1)
        bar = _bar(2, 100, 109, 95, 102)
        result = is_retesting_fvg(fvg, bar, atr=10, atr_buffer_factor=0.1)
        assert result is True


class TestCleanupFvgs:
    def test_removes_old_filled(self):
        fvgs = [
            FVG(direction="bullish", top=110, bottom=105, real_index=5, filled=True),
            FVG(direction="bullish", top=120, bottom=115, real_index=590),
        ]
        result = cleanup_fvgs(fvgs, current_abs=600, max_age=50)
        assert len(result) == 1
        assert result[0].real_index == 590

    def test_keeps_recent_fvgs(self):
        fvgs = [
            FVG(direction="bullish", top=110, bottom=105, real_index=590, filled=True),
            FVG(direction="bullish", top=120, bottom=115, real_index=595),
        ]
        result = cleanup_fvgs(fvgs, current_abs=600, max_age=50)
        assert len(result) == 2


class TestFvgIsAlive:
    """IFVG guard-fix: scan_from semantigi — kirilim barinin kendisi flipped
    aday icin olum kosulu DEGILDIR; tarama break_bar+1'den baslar."""

    def test_default_scans_from_formation_plus_2(self):
        """scan_from yok: tarama formation+2'den baslar; far-side close orada
        FVG'yi oldurur (bullish: close < bottom)."""
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),  # formation
            _bar(2, 100, 102, 98, 101),  # formation+1 (boundary, sayilmaz)
            _bar(3, 100, 102, 98, 101),  # formation+2: close 101 < 103 -> dead
        ]
        assert fvg_is_alive("bullish", 105, 103, 1, bars) is False

    def test_default_ignores_bars_before_formation_plus_2(self):
        """formation+1'deki far-side close taramaya DAHIL edilmez (boundary bar)."""
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),  # formation
            _bar(2, 100, 102, 98, 101),  # formation+1 far-side close — sayilmaz
        ]
        assert fvg_is_alive("bullish", 105, 103, 1, bars) is True

    def test_scan_from_skips_break_bar_far_side_close(self):
        """IFVG: kirilim barinin kendisi flipped aday icin olum DEGIL —
        scan_from=break_bar+1 ile tarama kirilimin sonrasindan baslar.
        (bearish flipped aday: far-side close = close > top)"""
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 100, 102, 98, 101),
            _bar(3, 106, 108, 104, 107),  # kirilim bari: close 107 > top 105
            _bar(4, 103, 105, 101, 104),  # retest bari: close 104 <= top 105
        ]
        # scan_from=4 (break_bar 3 + 1) -> kirilim bari sayilmaz -> alive
        assert fvg_is_alive("bearish", 105, 103, 1, bars, scan_from=4) is True

    def test_scan_from_counts_bars_after_break(self):
        """Kirilim SONRASI far-side close hala olum kosuludur (tutarlilik,
        gevsetme degil)."""
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 100, 102, 98, 101),
            _bar(3, 106, 108, 104, 107),  # kirilim bari
            _bar(4, 105, 107, 103, 106),  # break+1: close 106 > top 105 -> dead
        ]
        assert fvg_is_alive("bearish", 105, 103, 1, bars, scan_from=4) is False

    def test_scan_from_overrides_formation_index(self):
        """scan_from verilince formation_index yoksayilir — tarama dogrudan
        verilen bardan baslar."""
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(
                2, 100, 102, 98, 101
            ),  # formation+1 far-side — scan_from=3 ile sayilmaz
            _bar(3, 103, 105, 101, 104),
        ]
        assert fvg_is_alive("bullish", 105, 103, 1, bars, scan_from=3) is True

    def test_unclosed_bars_skipped(self):
        """Kapanmamis bar canlilik taramasinda sayilmaz (canli parity)."""
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 100, 102, 98, 101),
            _bar(3, 100, 102, 98, 101, is_closed=False),  # kapanmamis
        ]
        assert fvg_is_alive("bullish", 105, 103, 1, bars) is True


class TestRefreshFvgList:
    def test_adds_new_fvgs_and_updates_states(self):
        bars = [
            _bar(0, 100, 105, 95, 102),
            _bar(1, 103, 106, 96, 104),
            _bar(2, 108, 112, 107, 110),
        ]
        existing = []
        result = refresh_fvg_list(existing, bars, timeframe="5m", symbol="TEST")
        assert len(result) >= 1
        for f in result:
            assert isinstance(f, FVG), f"Expected FVG, got {type(f)}: {f}"
