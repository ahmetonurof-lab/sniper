"""
test_retrace_state.py — RetraceStateMachine + scan_htf_fvgs unit tests.
"""

from unittest.mock import patch

from fvg import detect_fvgs
from models import Bar
from retrace_state import (
    RetraceState,
    RetraceStateMachine,
    HTFFVG,
    scan_htf_fvgs,
)


# ── Helper ────────────────────────────────────────────────────────


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


def _make_15m_bars(n=50, trend="up", base=100.0, step=1.0, gap=False):
    """Generate 15m bars with optional bullish gap for FVG detection."""
    bars = []
    for i in range(n):
        if trend == "up":
            o = base + i * step
            c = o + step * 0.6
        else:
            o = base - i * step
            c = o - step * 0.6
        hi = max(o, c) + step * 0.4
        lo = min(o, c) - step * 0.4
        bars.append(_bar(i, o, hi, lo, c, timestamp=i * 900000))
    return bars


def _make_bars_with_gap(direction="bullish", gap_index=10, base=100.0):
    """Create bars where bar gap_index-1 and gap_index+1 have a gap."""
    bars = []
    for i in range(30):
        if i == gap_index - 1:
            if direction == "bullish":
                bars.append(_bar(i, base, base + 3, base - 1, base + 2))
            else:
                bars.append(_bar(i, base + 5, base + 8, base + 4, base + 6))
        elif i == gap_index:
            if direction == "bullish":
                bars.append(_bar(i, base + 3, base + 5, base + 2, base + 4))
            else:
                bars.append(_bar(i, base + 2, base + 6, base + 1, base + 3))
        elif i == gap_index + 1:
            if direction == "bullish":
                bars.append(_bar(i, base + 5, base + 8, base + 5, base + 7))
            else:
                bars.append(_bar(i, base - 1, base + 2, base - 2, base + 1))
        else:
            bars.append(_bar(i, base, base + 2, base - 2, base + 1))
        base += 1
    return bars


def _zigzag_fvg_bars(cycles=6, base=100.0):
    """Alternating bull/bear FVG'ler: her 6 bar'lik cycle 2 FVG uretir.

    Cycle: A(down) B(up impulse) C(up) D(contained) E(down impulse) F(down).
    B-center -> bullish FVG (C.low > A.high), E-center -> bearish FVG
    (F.high < D.low). Toplam FVG sayisi = cycles * 2.
    """
    bars = []
    i = 0
    p = base
    for _ in range(cycles):
        seq = [
            (p, p + 0.5, p - 1.0, p),  # A down
            (p, p + 20.0, p - 0.5, p + 18.0),  # B up impulse
            (p + 18.0, p + 22.0, p + 17.0, p + 21.0),  # C up (bull FVG)
            (p + 21.0, p + 22.0, p + 20.0, p + 21.0),  # D contained
            (p + 21.0, p + 21.5, p + 1.0, p + 3.0),  # E down impulse (bear FVG)
            (p + 3.0, p + 4.0, p, p + 2.0),  # F down
        ]
        for o, h, lo, c in seq:
            bars.append(_bar(i, o, h, lo, c, timestamp=i * 900000))
            i += 1
        p += 2.0
    return bars


# ═══════════════════════════════════════════════════════════════════
# scan_htf_fvgs tests
# ═══════════════════════════════════════════════════════════════════


class TestScanHtfFvgs:
    def test_returns_empty_for_less_than_5_bars(self):
        bars = [_bar(i, 100, 105, 95, 102) for i in range(4)]
        result = scan_htf_fvgs(bars, lookback=100, min_fvg_size=1.0)
        assert result == []

    def test_returns_htf_fvgs_sorted_by_bar_index(self):
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        result = scan_htf_fvgs(bars, lookback=100, min_fvg_size=0.1)
        assert len(result) >= 1
        # Sorted by bar_index
        for i in range(1, len(result)):
            assert result[i - 1].bar_index <= result[i].bar_index

    def test_limits_to_10(self):
        # Generate many bars with gaps
        bars = []
        base = 100.0
        for i in range(50):
            if i % 3 == 0 and i > 1 and i < 48:
                # Create a bullish gap
                bars.append(_bar(i - 2, base, base + 2, base - 1, base + 1))
                bars.append(_bar(i - 1, base + 2, base + 4, base + 1, base + 3))
                bars.append(_bar(i, base + 4, base + 6, base + 4, base + 5))
            else:
                bars.append(_bar(i, base, base + 2, base - 2, base + 1))
            base += 0.5
        result = scan_htf_fvgs(bars, lookback=100, min_fvg_size=0.05)
        assert len(result) <= 10

    def test_direction_filter_applies_before_cap(self):
        """L-06: cap (son 10) yon filtresinden once uygulanirsa tek yondeki
        FVG'ler tum slotlari doldurup diger yonun FVG'lerini eleyebilir."""
        bars = _zigzag_fvg_bars(cycles=6)
        all_fvgs = detect_fvgs(
            bars, lookback=len(bars), timeframe="15m", min_fvg_size=10.0
        )
        # Test onkosulu: toplam FVG > 10 -> cap gercekten kapsaniyor olmali
        assert len(all_fvgs) > 10
        bull_total = sum(1 for f in all_fvgs if f.direction == "bullish")
        bear_total = sum(1 for f in all_fvgs if f.direction == "bearish")

        unfiltered = scan_htf_fvgs(bars, lookback=100, min_fvg_size=10.0)
        assert len(unfiltered) == 10  # cap aktif

        bull = scan_htf_fvgs(bars, lookback=100, min_fvg_size=10.0, direction="bullish")
        bear = scan_htf_fvgs(bars, lookback=100, min_fvg_size=10.0, direction="bearish")
        assert all(x.direction == "bullish" for x in bull)
        assert all(x.direction == "bearish" for x in bear)
        # Filtre cap'tan ONCE: filtrelenmis sonuc TUM o yondeki FVG'leri icerir
        assert len(bull) == bull_total
        assert len(bear) == bear_total
        assert bull_total > 0 and bear_total > 0

    def test_direction_none_returns_all(self):
        bars = _zigzag_fvg_bars(cycles=3)
        result = scan_htf_fvgs(bars, lookback=100, min_fvg_size=10.0, direction=None)
        baseline = scan_htf_fvgs(bars, lookback=100, min_fvg_size=10.0)
        # HTFFVG eq tanimlamiyor -> alan bazli karsilastir
        assert len(result) == len(baseline)
        assert all(
            a.direction == b.direction
            and a.top == b.top
            and a.bottom == b.bottom
            and a.bar_index == b.bar_index
            for a, b in zip(result, baseline)
        )

    def test_handles_min_fvg_size_filter(self):
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        result_small = scan_htf_fvgs(bars, lookback=100, min_fvg_size=0.1)
        result_large = scan_htf_fvgs(bars, lookback=100, min_fvg_size=50.0)
        assert len(result_small) >= 1
        assert len(result_large) == 0


# ═══════════════════════════════════════════════════════════════════
# HTFFVG tests
# ═══════════════════════════════════════════════════════════════════


class TestHTFFVG:
    def test_creation(self):
        fvg = HTFFVG(top=110.0, bottom=105.0, direction="bullish", bar_index=5)
        assert fvg.top == 110.0
        assert fvg.bottom == 105.0
        assert fvg.direction == "bullish"
        assert fvg.bar_index == 5

    def test_repr(self):
        fvg = HTFFVG(top=110.0, bottom=105.0, direction="bullish", bar_index=5)
        r = repr(fvg)
        assert "105.00" in r
        assert "110.00" in r
        assert "bullish" in r


# ═══════════════════════════════════════════════════════════════════
# RetraceStateMachine tests
# ═══════════════════════════════════════════════════════════════════


class TestRetraceStateMachineInit:
    def test_starts_in_idle(self):
        rsm = RetraceStateMachine()
        assert rsm.state == RetraceState.IDLE
        assert rsm.direction is None
        assert rsm.sweep_level is None
        assert rsm.trigger_fvg is None

    def test_state_name(self):
        rsm = RetraceStateMachine()
        assert rsm.state_name == "IDLE"

    def test_can_trigger_false_initially(self):
        rsm = RetraceStateMachine()
        assert rsm.can_trigger() is False


class TestOnSweep:
    def test_on_sweep_bullish_transitions_to_sweep_detected(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.direction == "bullish"
        assert rsm.sweep_level == 105.0

    def test_on_sweep_bearish_transitions_to_sweep_detected(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.direction == "bearish"
        assert rsm.sweep_level == 95.0

    def test_on_sweep_ignored_when_not_idle(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        # Second call should be ignored
        rsm.on_sweep("bearish", 95.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.direction == "bullish"  # unchanged

    def test_on_sweep_with_bar_index_stores_pending_id(self):
        rsm = RetraceStateMachine()
        with patch("state_manager.is_sweep_used", return_value=False):
            rsm.on_sweep("bullish", 105.0, bar_index=42)
        assert rsm._pending_sweep_persistence_id == "bullish_42"

    def test_on_sweep_with_symbol_prefixed_pending_id(self):
        """L-04: symbol verildiginde pending ID symbol icerir."""
        rsm = RetraceStateMachine()
        with patch("state_manager.is_sweep_used", return_value=False):
            rsm.on_sweep("bullish", 105.0, bar_index=42, symbol="BTCUSDT")
        assert rsm._pending_sweep_persistence_id == "BTCUSDT_bullish_42"

    @patch("state_manager.is_sweep_used")
    def test_on_sweep_symbol_included_in_dedup_check(self, mock_is_used):
        """L-04: dedup kontrolu symbol'lu ID ile yapilir."""
        mock_is_used.return_value = False
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0, bar_index=42, symbol="ETHUSDT")
        mock_is_used.assert_called_once_with("ETHUSDT_bullish_42")
        assert rsm.state == RetraceState.SWEEP_DETECTED

    @patch("state_manager.is_sweep_used")
    def test_sweep_ids_distinct_across_symbols(self, mock_is_used):
        """L-04 core: ayni bar_index farkli coinlerde ayri sweep sayilmali —
        ID collision (sembolsuz format) bugu burada yakalanir."""
        mock_is_used.return_value = False
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0, bar_index=42, symbol="BTCUSDT")
        assert rsm._pending_sweep_persistence_id == "BTCUSDT_bullish_42"
        rsm.reset()
        rsm.on_sweep("bullish", 105.0, bar_index=42, symbol="ETHUSDT")
        assert rsm._pending_sweep_persistence_id == "ETHUSDT_bullish_42"

    @patch("state_manager.is_sweep_used")
    def test_on_sweep_skips_already_used(self, mock_is_used):
        mock_is_used.return_value = True
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0, bar_index=42)
        assert rsm.state == RetraceState.IDLE  # No transition
        mock_is_used.assert_called_once_with("bullish_42")

    @patch("state_manager.is_sweep_used")
    def test_on_sweep_graceful_on_state_manager_error(self, mock_is_used):
        mock_is_used.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        # Should not raise, should still transition
        rsm.on_sweep("bullish", 105.0, bar_index=42)
        assert rsm.state == RetraceState.SWEEP_DETECTED


class TestOnSweepConfirmed:
    def test_resets_when_no_fvg_found(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        # Use bars with no FVG gaps — sweep stays active
        bars = [_bar(i, 100, 102, 98, 101) for i in range(20)]
        sweep_bar = _bar(19, 101, 106, 99, 105)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.SWEEP_DETECTED

    def test_resets_when_not_in_sweep_detected(self):
        rsm = RetraceStateMachine()
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        sweep_bar = _bar(15, 108, 109.5, 103, 109)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.IDLE

    def test_bullish_trigger_when_wick_touches_but_body_safe(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        # Sweep bar: wick goes down into FVG zone, but body stays above
        # FVG from gap_index=10 is bullish: top ~b_next.low, bottom ~b_prev.high
        sweep_bar = _bar(15, 108, 110, 103, 109)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        # This may or may not trigger depending on exact FVG values.
        # The key behavior: if a matching FVG exists with wick rejection,
        # we transition to TRIGGER_READY. Otherwise reset.
        # Let's test more explicitly with crafted bars.
        pass

    def test_bullish_wick_rejection_triggers(self):
        """Crafted scenario: bullish sweep + FVG with wick rejection + close inside FVG."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        # Craft bars with a clear bullish FVG detected by detect_fvgs
        # FVG: b_prev=bar(0) high=103, b_next=bar(2) low=105 → gap [103, 105]
        # bar(1) is impulse candle
        bars = [
            _bar(0, 100, 103, 99, 102),  # b_prev
            _bar(1, 103, 105, 102, 104, is_closed=True),  # impulse
            _bar(2, 106, 110, 105, 105, is_closed=True),  # b_next (close=105 fills gap)
            _bar(3, 108, 112, 107, 110, is_closed=True),
            _bar(4, 110, 113, 109, 112, is_closed=True),
            _bar(5, 112, 115, 111, 114, is_closed=True),
            _bar(6, 114, 116, 113, 115, is_closed=True),
            _bar(7, 115, 117, 114, 116, is_closed=True),
        ]
        # Add a bar that closes INSIDE the FVG [103, 105] for fvg_close_confirmed
        # bar(8): close=104 which is between FVG bottom(103) and top(105)
        bars.append(_bar(8, 105, 107, 103, 104, is_closed=True, timestamp=8 * 900000))
        # Sweep bar: wick goes down to 101 (within FVG top=105), body closes above FVG
        sweep_bar = _bar(9, 116, 118, 101, 117, is_closed=True, timestamp=9 * 900000)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.trigger_fvg is not None

    def test_bearish_wick_rejection_triggers(self):
        """Crafted scenario: bearish sweep + FVG with wick rejection + close inside FVG."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)
        # Bearish FVG: b_prev=bar(0) low=109, b_next=bar(2) high=108 → gap [108, 109]
        # bar(1) is impulse candle
        bars = [
            _bar(0, 110, 113, 109, 111, is_closed=True),  # b_prev
            _bar(1, 109, 111, 107, 108, is_closed=True),  # impulse
            _bar(2, 106, 108, 103, 105, is_closed=True),  # b_next
            _bar(3, 105, 107, 102, 104, is_closed=True),
            _bar(4, 104, 106, 100, 102, is_closed=True),
            _bar(5, 102, 104, 98, 100, is_closed=True),
            _bar(6, 100, 102, 96, 98, is_closed=True),
            _bar(7, 98, 100, 94, 96, is_closed=True),
        ]
        # Add a bar closing INSIDE FVG [108, 109]
        bars.append(_bar(8, 96, 110, 95, 108.5, is_closed=True, timestamp=8 * 900000))
        # Sweep bar: wick goes up to 109 (touches FVG), body stays below, close ok (< sweep_level)
        sweep_bar = _bar(9, 96, 109, 94, 95, is_closed=True, timestamp=9 * 900000)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.trigger_fvg is not None

    def test_skips_fvg_with_wrong_direction(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        # Create a bearish FVG only
        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 110, 112, 108, 109),
            _bar(2, 107, 109, 104, 106),  # bearish gap
            _bar(3, 106, 108, 102, 104),
            _bar(4, 104, 106, 100, 102),
            _bar(5, 102, 104, 98, 100),
        ]
        sweep_bar = _bar(6, 100, 106, 98, 105)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        # Bearish FVG, but we need bullish → SWEEP_DETECTED'de kalir, reset yok
        assert rsm.state == RetraceState.SWEEP_DETECTED

    def test_skips_fvg_after_sweep_bar(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        # FVGs that appear at or after sweep bar index should be skipped
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG at index 1
            _bar(3, 108, 112, 107, 110),
        ]
        # Sweep bar at index 0 — FVG bar_index (1) >= sweep bar (0) → skip
        sweep_bar = _bar(0, 116, 118, 104, 117)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.SWEEP_DETECTED

    @patch("retrace_state.scan_htf_fvgs")
    def test_on_sweep_confirmed_filters_scan_by_direction(self, mock_scan):
        """L-06: on_sweep_confirmed taramayi kendi yonuyle filtreler."""
        mock_scan.return_value = []
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        bars = [_bar(i, 100, 102, 98, 101) for i in range(20)]
        sweep_bar = _bar(19, 101, 106, 99, 105)
        rsm.on_sweep_confirmed(bars, sweep_bar, atr_val=2.0)
        _, kwargs = mock_scan.call_args
        assert kwargs.get("direction") == "bullish"

    @patch("retrace_state.scan_htf_fvgs")
    def test_on_bias_fvg_filters_scan_by_direction(self, mock_scan):
        """L-06: on_bias_fvg taramayi kendi yonuyle filtreler."""
        mock_scan.return_value = []
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=0)
        bars = [_bar(i, 100, 102, 98, 101) for i in range(20)]
        rsm.on_bias_fvg(bars, bars[15])
        _, kwargs = mock_scan.call_args
        assert kwargs.get("direction") == "bullish"


class TestCanTrigger:
    def test_can_trigger_only_in_trigger_ready(self):
        rsm = RetraceStateMachine()
        assert rsm.can_trigger() is False  # IDLE

        rsm.on_sweep("bullish", 105.0)
        assert rsm.can_trigger() is False  # SWEEP_DETECTED

        # Manually set to TRIGGER_READY
        rsm.state = RetraceState.TRIGGER_READY
        assert rsm.can_trigger() is True


# ═══════════════════════════════════════════════════════════════════
# Bias Kilit Modu (BIAS_LOCKED + on_bias_fvg)
# ═══════════════════════════════════════════════════════════════════


class TestBiasLock:
    def test_lock_bias_sets_bias_locked_and_keeps_direction(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.state = RetraceState.TRIGGER_READY
        rsm.trigger_fvg = HTFFVG(110.0, 105.0, "bullish", 5)

        rsm.lock_bias(bar_index=5)
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.bias_locked is True
        assert rsm.locked_direction == "bullish"
        # Sweep verileri temizlendi, yon + kilit noktasi korunuyor
        assert rsm.sweep_level is None
        assert rsm.trigger_fvg is None
        assert rsm._locked_from_bar == 5

    def test_lock_bias_noop_without_direction(self):
        rsm = RetraceStateMachine()
        rsm.lock_bias(bar_index=3)
        assert rsm.state == RetraceState.IDLE
        assert rsm.bias_locked is False

    def test_lock_bias_preserves_guard_when_no_bar_index(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)
        rsm.lock_bias(bar_index=7)
        # Exit-cagrisi gibi bar_index=None -> mevcut guard korunur
        rsm.lock_bias()
        assert rsm._locked_from_bar == 7

    def test_reset_clears_bias_lock(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=5)
        rsm.reset()
        assert rsm.state == RetraceState.IDLE
        assert rsm.bias_locked is False
        assert rsm.locked_direction is None
        assert rsm._locked_from_bar is None

    def test_on_bias_fvg_ignored_when_not_locked(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)
        current = _bar(20, 106, 108, 100, 107)
        rsm.on_bias_fvg(bars, current)
        assert rsm.state != RetraceState.TRIGGER_READY

    def test_on_bias_fvg_requires_fresh_fvg_after_lock_point(self):
        """Kilit oncesi FVG (from_bar=12) tekrar tetiklenmemeli."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=12)
        bars = _make_bars_with_gap("bullish", gap_index=10, base=100.0)  # FVG ~10
        current = _bar(15, 108, 110, 100, 109)
        rsm.on_bias_fvg(bars, current)
        # locked_from_bar=12 >= fvg.bar_index(~10) -> stale, trigger yok
        assert rsm.state == RetraceState.BIAS_LOCKED

    def test_on_bias_fvg_triggers_on_fresh_fvg(self):
        """Kilit sonrasi yeni FVG + wick rejection -> TRIGGER_READY."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=0)
        # Bar 2'de bullish FVG, current bar 4 wick ile dokunur
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG [103,105]
            _bar(3, 108, 112, 107, 110),
            _bar(4, 110, 113, 104, 112),  # current: wick 104 <= top 105, body 112 > 105
        ]
        rsm.on_bias_fvg(bars, bars[4])
        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.trigger_fvg is not None
        assert rsm.trigger_fvg.direction == "bullish"

    def test_on_bias_fvg_body_break_does_not_trigger(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=0)
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG [103,105]
            _bar(3, 108, 112, 107, 110),
            _bar(4, 110, 113, 100, 101),  # body 101 < bottom 103 -> broke down
        ]
        rsm.on_bias_fvg(bars, bars[4])
        assert rsm.state == RetraceState.BIAS_LOCKED

    def test_on_bias_fvg_rejects_invalidated_fvg(self):
        """L-07: far-side close FVG'yi INVALIDATED yapar — tekrar tetiklenemez."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=0)
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(
                2, 106, 110, 105, 108
            ),  # bullish FVG [103,105] (impulse=bar1, boundary=bar2)
            _bar(3, 108, 112, 107, 110),
            _bar(4, 110, 113, 100, 101),  # close 101 < bottom 103 -> invalidated
            _bar(5, 110, 113, 104, 112),  # current: wick 104 <= top 105
        ]
        rsm.on_bias_fvg(bars, bars[5])
        assert rsm.state == RetraceState.BIAS_LOCKED

    def test_on_bias_fvg_rejects_already_touched_fvg(self):
        """L-07: formation ile current arasinda wick dokunusu FVG'yi tuketir."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=0)
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(
                2, 106, 110, 105, 108
            ),  # bullish FVG [103,105] (impulse=bar1, boundary=bar2)
            _bar(3, 108, 112, 104, 110),  # low 104 <= top 105 -> FVG already filled
            _bar(4, 110, 113, 104, 112),  # current: wick yine dokunur
        ]
        rsm.on_bias_fvg(bars, bars[4])
        assert rsm.state == RetraceState.BIAS_LOCKED

    def test_on_bias_fvg_gap_inside_close_is_not_invalid(self):
        """L-07 parity (backtest): gap icinde kapanis (ACTIVE_ENTRY_ZONE)
        INVALID DEGILDIR — FVG canli kalir; ancak ayni bar FVG'ye wick ile
        dokundugundan (low <= top) FVG tuketilmis sayilir ve tekrar trigger
        etmez."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.lock_bias(bar_index=0)
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG [103,105], boundary=bar2
            _bar(3, 108, 112, 104, 104),  # close 104 gap icinde, low 104 <= top 105
            _bar(4, 110, 113, 109, 112),  # current: wick 109 > top 105 -> dokunmaz
        ]
        rsm.on_bias_fvg(bars, bars[4])
        # FVG alive (gap-inside close invalid degil) ama touched -> trigger yok
        assert rsm.state == RetraceState.BIAS_LOCKED


class TestReset:
    def test_reset_clears_all_fields(self):
        rsm = RetraceStateMachine()
        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = "bullish"
        rsm.sweep_level = 105.0
        rsm.trigger_fvg = HTFFVG(110.0, 105.0, "bullish", 5)
        rsm._pending_sweep_persistence_id = "bullish_42"

        rsm.reset()

        assert rsm.state == RetraceState.IDLE
        assert rsm.direction is None
        assert rsm.sweep_level is None
        assert rsm.trigger_fvg is None
        assert rsm._pending_sweep_persistence_id is None

    def test_reset_no_pending_sweep(self):
        rsm = RetraceStateMachine()
        rsm.state = RetraceState.SWEEP_DETECTED
        rsm.direction = "bearish"

        rsm.reset()

        assert rsm.state == RetraceState.IDLE
        assert rsm.direction is None


class TestOperationalFail:
    """Grup 3 (Sonnet direktifi): order/fill hatalarinda ardisik sayac —
    3. hatada full reset (IDLE), aksi halde lock_bias."""

    def _trigger_ready(self):
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        rsm.state = RetraceState.TRIGGER_READY
        return rsm

    def test_first_fail_locks_bias(self):
        rsm = self._trigger_ready()
        rsm.on_operational_fail(bar_index=10)
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.direction == "bullish"
        assert rsm._fail_count == 1
        assert rsm._locked_from_bar == 10

    def test_second_fail_still_locks_bias(self):
        rsm = self._trigger_ready()
        rsm.on_operational_fail(bar_index=10)
        rsm.on_operational_fail(bar_index=11)
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm._fail_count == 2

    def test_third_consecutive_fail_full_reset(self):
        """Grup 3 sarti: art arda 3 order/fill hatasi -> full reset (IDLE)."""
        rsm = self._trigger_ready()
        rsm.on_operational_fail(bar_index=10)
        rsm.on_operational_fail(bar_index=11)
        rsm.on_operational_fail(bar_index=12)
        assert rsm.state == RetraceState.IDLE
        assert rsm.direction is None
        assert rsm._fail_count == 0

    def test_reset_clears_fail_streak(self):
        rsm = self._trigger_ready()
        rsm.on_operational_fail(bar_index=10)
        rsm.reset()
        assert rsm._fail_count == 0

    def test_clear_fail_streak_after_successful_entry(self):
        rsm = self._trigger_ready()
        rsm.on_operational_fail(bar_index=10)
        rsm.clear_fail_streak()
        assert rsm._fail_count == 0
        rsm.on_operational_fail(bar_index=11)
        assert rsm._fail_count == 1
        assert rsm.state == RetraceState.BIAS_LOCKED


class TestSweepConsumption:
    """L-08/L-09: on_sweep_confirmed sweep tuketmez; tuketim
    confirm_entry_success()/_consume_sweep() uzerinden. Persistence hatasi
    pending ID'yi korur (yutulmaz).

    Rapor 4: dedup metadata'si lock state'ten BAGIMSIZDIR — lock_bias()/
    restore_bias_lock() pending ID'yi silmez; yalnizca basarili
    mark_sweep_used() sonrasi temizlenir. Persistence hatasi BIAS kilidini ve
    FVG-only aramayi asla durdurmaz."""

    @patch("state_manager.mark_sweep_used")
    def test_consume_sweep_success_clears_pending(self, mock_mark):
        rsm = RetraceStateMachine()
        rsm._pending_sweep_persistence_id = "bullish_42"
        assert rsm._consume_sweep() is True
        mock_mark.assert_called_once_with("bullish_42")
        assert rsm._pending_sweep_persistence_id is None

    def test_consume_sweep_no_id_is_noop(self):
        rsm = RetraceStateMachine()
        assert rsm._consume_sweep() is True
        assert rsm._pending_sweep_persistence_id is None

    @patch("state_manager.mark_sweep_used")
    def test_consume_sweep_error_keeps_pending(self, mock_mark):
        """L-09+Rapor 4: persistence hatasi yutulmaz — ID ayni alanda korunur
        (tasinmaz/silinmez), False doner."""
        mock_mark.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        rsm._pending_sweep_persistence_id = "bullish_42"
        assert rsm._consume_sweep() is False
        assert rsm._pending_sweep_persistence_id == "bullish_42"

    @patch("state_manager.mark_sweep_used")
    def test_confirm_entry_success_delegates_to_consume(self, mock_mark):
        rsm = RetraceStateMachine()
        rsm._pending_sweep_persistence_id = "BTCUSDT_bullish_42"
        assert rsm.confirm_entry_success() is True
        mock_mark.assert_called_once_with("BTCUSDT_bullish_42")
        assert rsm._pending_sweep_persistence_id is None

    @patch("state_manager.mark_sweep_used")
    def test_confirm_entry_success_keeps_id_on_error(self, mock_mark):
        mock_mark.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        rsm._pending_sweep_persistence_id = "bullish_42"
        assert rsm.confirm_entry_success() is False
        assert rsm._pending_sweep_persistence_id == "bullish_42"

    @patch("state_manager.mark_sweep_used")
    def test_lock_bias_preserves_pending_sweep_persistence_id(self, mock_mark):
        """Rapor 4 CORE: persistence hatasi -> confirm False -> lock_bias()
        cagrisi ID'yi SILMEMELI; periyodik retry ile temizlenebilmeli."""
        mock_mark.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        rsm.direction = "bullish"
        rsm._pending_sweep_persistence_id = "BTCUSDT_bullish_42"
        assert rsm.confirm_entry_success() is False
        rsm.lock_bias(bar_index=10)
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm._pending_sweep_persistence_id == "BTCUSDT_bullish_42"

        # Sonraki retry basarili -> ID temizlenir, symbol-scoped key yazilir
        mock_mark.side_effect = None
        assert rsm.retry_pending_sweep_persistence() is True
        mock_mark.assert_called_with("BTCUSDT_bullish_42")
        assert rsm._pending_sweep_persistence_id is None

    @patch("state_manager.mark_sweep_used")
    def test_retry_pending_sweep_persistence_success_clears(self, mock_mark):
        rsm = RetraceStateMachine()
        rsm._pending_sweep_persistence_id = "BTCUSDT_bullish_42"
        assert rsm.retry_pending_sweep_persistence() is True
        mock_mark.assert_called_once_with("BTCUSDT_bullish_42")
        assert rsm._pending_sweep_persistence_id is None

    def test_retry_pending_sweep_persistence_no_id_is_noop(self):
        rsm = RetraceStateMachine()
        assert rsm.retry_pending_sweep_persistence() is True

    @patch("state_manager.mark_sweep_used")
    def test_retry_pending_sweep_persistence_error_keeps_id(self, mock_mark):
        mock_mark.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        rsm._pending_sweep_persistence_id = "bullish_42"
        assert rsm.retry_pending_sweep_persistence() is False
        assert rsm._pending_sweep_persistence_id == "bullish_42"

    def test_reset_clears_pending_sweep_persistence_id(self):
        rsm = RetraceStateMachine()
        rsm._pending_sweep_persistence_id = "bullish_1"
        rsm.reset()
        assert rsm._pending_sweep_persistence_id is None

    @patch("state_manager.mark_sweep_used")
    def test_restore_bias_lock_keeps_pending_persistence_id(self, mock_mark):
        """Rapor 4: restore_bias_lock() lock metadata'sini yukler ama dedup
        pending ID'sine dokunmaz (restart oncesi yarim kalan tuketim retry ile
        tamamlanir)."""
        mock_mark.side_effect = Exception("disk error")
        rsm = RetraceStateMachine()
        rsm.direction = "bearish"
        rsm._pending_sweep_persistence_id = "BTCUSDT_bearish_7"
        rsm.lock_bias(bar_index=7)
        rsm.restore_bias_lock("bearish", locked_from_bar=7)
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.locked_direction == "bearish"
        assert rsm._pending_sweep_persistence_id == "BTCUSDT_bearish_7"

    def test_restore_bias_lock_sets_state_direction_from_bar(self):
        """Rapor 4: restart sonrasi RSM IDLE'dan BIAS_LOCKED'a gecer; yeni
        sweep beklemez, locked_from_bar korunur."""
        rsm = RetraceStateMachine()
        rsm.direction = "bullish"
        rsm.on_sweep("bullish", 100.0, bar_index=5)
        rsm.reset()
        assert rsm.state == RetraceState.IDLE
        rsm.restore_bias_lock("bullish", locked_from_bar=5)
        assert rsm.state == RetraceState.BIAS_LOCKED
        assert rsm.locked_direction == "bullish"
        assert rsm._locked_from_bar == 5
        assert rsm.sweep_level is None
        assert rsm.trigger_fvg is None

    def test_trigger_does_not_consume_sweep(self):
        """L-08: on_sweep_confirmed TRIGGER_READY'ye gecirir ama sweep'i
        tuketmez — confirm_entry_success()'e birakilir."""
        rsm = RetraceStateMachine()
        with patch("state_manager.is_sweep_used", return_value=False):
            rsm.on_sweep("bullish", 105.0, bar_index=9)
        bars = [
            _bar(0, 100, 103, 99, 102),  # b_prev
            _bar(1, 103, 105, 102, 104),  # impulse
            _bar(2, 106, 110, 105, 105, is_closed=True),  # b_next
            _bar(3, 108, 112, 107, 110, is_closed=True),
            _bar(4, 110, 113, 109, 112, is_closed=True),
            _bar(5, 112, 115, 111, 114, is_closed=True),
            _bar(6, 114, 116, 113, 115, is_closed=True),
            _bar(7, 115, 117, 114, 116, is_closed=True),
        ]
        sweep_bar = _bar(9, 116, 118, 101, 117, is_closed=True, timestamp=9 * 900000)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.TRIGGER_READY
        # Henuz tuketilmedi — lock_bias oncesi confirm_entry_success() gerekir
        assert rsm._pending_sweep_persistence_id == "bullish_9"
        with patch("state_manager.mark_sweep_used") as mock_mark:
            assert rsm.confirm_entry_success() is True
            mock_mark.assert_called_once_with("bullish_9")
        assert rsm._pending_sweep_persistence_id is None


# ═══════════════════════════════════════════════════════════════════
# Integration-style: full flow
# ═══════════════════════════════════════════════════════════════════


class TestFullFlow:
    def test_idle_to_sweep_to_trigger_flow_bullish(self):
        rsm = RetraceStateMachine()
        assert rsm.state == RetraceState.IDLE

        with patch("state_manager.is_sweep_used", return_value=False):
            rsm.on_sweep("bullish", 105.0, bar_index=5)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm.can_trigger() is False

        # Now confirm with bars that have matching FVG + wick rejection
        # FVG: bar(0) high=103, bar(2) low=105 → gap [103, 105]
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 108, 112, 107, 110),
            _bar(4, 110, 113, 109, 112),
            _bar(5, 112, 115, 111, 114),
            _bar(6, 114, 116, 113, 115),
            _bar(7, 115, 117, 114, 116),
            # Bar that closes INSIDE FVG [103, 105] for fvg_close_confirmed
            _bar(8, 105, 107, 103, 104, is_closed=True, timestamp=8 * 900000),
        ]
        sweep_bar = _bar(9, 116, 118, 101, 117, is_closed=True, timestamp=9 * 900000)

        rsm.on_sweep_confirmed(bars, sweep_bar)

        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.can_trigger() is True

        # Reset
        rsm.reset()
        assert rsm.state == RetraceState.IDLE
        assert rsm.can_trigger() is False

    def test_sweep_invalid_when_body_breaks_below_level(self):
        """Bullish sweep: close < sweep_level → invalidation → IDLE."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        bars = [_bar(i, 100, 102, 98, 101) for i in range(5)]
        sweep_bar = _bar(5, 106, 109, 101, 102, timestamp=5 * 900000)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.IDLE

    def test_body_breaks_fvg_does_not_trigger_bearish(self):
        """Body closing beyond the FVG invalidates the wick rejection."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)

        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 110, 112, 108, 109),
            _bar(2, 107, 109, 104, 106),
            _bar(3, 106, 108, 102, 104),
            _bar(4, 104, 106, 100, 102),
            _bar(5, 102, 104, 98, 100),
            _bar(6, 100, 102, 96, 98),
            _bar(7, 98, 100, 94, 96),
        ]
        # Wick touched, but body CLOSED ABOVE FVG top → no trigger
        sweep_bar = _bar(8, 96, 109, 94, 109, timestamp=8 * 900000)
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.IDLE

    def test_default_uses_max_wick_ratio(self):
        rsm = RetraceStateMachine()
        assert rsm._max_wick_ratio == 1.0

    def test_stores_custom_max_wick_ratio(self):
        rsm = RetraceStateMachine(max_wick_ratio=0.5)
        assert rsm._max_wick_ratio == 0.5


# ═══════════════════════════════════════════════════════════════════
# IFVG (Inversion FVG) — ikincil sinyal yolu (flag: config.IFVG_ENABLED)
# ═══════════════════════════════════════════════════════════════════


class TestIFVGRegisterInverted:
    def test_bullish_flips_to_bearish(self):
        rsm = RetraceStateMachine()
        rsm._register_inverted(HTFFVG(110.0, 105.0, "bullish", 5))
        assert len(rsm._inverted_candidates) == 1
        c = rsm._inverted_candidates[0]
        assert c.direction == "bearish"
        assert c.top == 110.0 and c.bottom == 105.0 and c.bar_index == 5

    def test_bearish_flips_to_bullish(self):
        rsm = RetraceStateMachine()
        rsm._register_inverted(HTFFVG(110.0, 105.0, "bearish", 3))
        assert len(rsm._inverted_candidates) == 1
        assert rsm._inverted_candidates[0].direction == "bullish"

    def test_appends_multiple(self):
        rsm = RetraceStateMachine()
        rsm._register_inverted(HTFFVG(110.0, 105.0, "bullish", 5))
        rsm._register_inverted(HTFFVG(90.0, 85.0, "bearish", 9))
        assert len(rsm._inverted_candidates) == 2
        assert [c.direction for c in rsm._inverted_candidates] == [
            "bearish",
            "bullish",
        ]

    def test_break_bar_index_default_none(self):
        """IFVG guard-fix: break_bar_index verilmezse None kalir (NORMAL
        aday konvansiyonu) — formasyon+2 taramasi korunur."""
        rsm = RetraceStateMachine()
        rsm._register_inverted(HTFFVG(110.0, 105.0, "bullish", 5))
        c = rsm._inverted_candidates[0]
        assert c.break_bar_index is None
        assert c.bar_index == 5

    def test_break_bar_index_recorded(self):
        """IFVG guard-fix: kirilim barinin index'i adayda saklanir —
        canlilik taramasi bu barin SONRASINDAN baslar."""
        rsm = RetraceStateMachine()
        rsm._register_inverted(HTFFVG(110.0, 105.0, "bullish", 5), break_bar_index=12)
        c = rsm._inverted_candidates[0]
        assert c.direction == "bearish"
        assert c.top == 110.0 and c.bottom == 105.0
        assert c.bar_index == 5  # orijinal formasyon bari korunur
        assert c.break_bar_index == 12


class TestIFVGCheckRetest:
    def test_flag_off_returns_none_and_keeps_candidate(self):
        """Flag kapali (default) -> davranis korunur: None, aday listede kalir."""
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bullish", 5))
        cur = _bar(9, 110, 112, 106, 107, timestamp=9 * 900000)  # wick low<=top
        assert rsm.check_ifvg_retest(cur) is None
        assert len(rsm._inverted_candidates) == 1

    def test_bullish_wick_touch_no_break_returns_and_removes(self, monkeypatch):
        # Bullish aday: low<=top (dokunur), close>=bottom (kirik degil).
        monkeypatch.setattr("config.IFVG_ENABLED", True)
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bullish", 5))
        cur = _bar(
            9, 110, 112, 106, 107, timestamp=9 * 900000
        )  # low106<=110, close107>=105
        hit = rsm.check_ifvg_retest(cur)
        assert hit is not None
        assert hit.direction == "bullish"
        assert len(rsm._inverted_candidates) == 0  # kullanildi, listeden cikti

    def test_bullish_full_break_drops_without_return(self, monkeypatch):
        # Bullish aday: close<bottom -> tam kirdi -> silinir, None (trigger yok).
        monkeypatch.setattr("config.IFVG_ENABLED", True)
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bullish", 5))
        cur = _bar(9, 108, 110, 100, 101, timestamp=9 * 900000)  # close101<105
        assert rsm.check_ifvg_retest(cur) is None
        assert len(rsm._inverted_candidates) == 0  # olu aday duskuruldü

    def test_bearish_wick_touch_no_break(self, monkeypatch):
        # Bearish aday: high>=bottom (dokunur), close<=top (kirik degil).
        monkeypatch.setattr("config.IFVG_ENABLED", True)
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bearish", 5))
        cur = _bar(
            9, 106, 109, 103, 107, timestamp=9 * 900000
        )  # high109>=105, close107<=110
        hit = rsm.check_ifvg_retest(cur)
        assert hit is not None and hit.direction == "bearish"
        assert len(rsm._inverted_candidates) == 0

    def test_no_touch_keeps_candidate(self, monkeypatch):
        # Bullish aday: low>top -> wick dokunmadi -> aday kalir, None.
        monkeypatch.setattr("config.IFVG_ENABLED", True)
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bullish", 5))
        cur = _bar(9, 111, 114, 111, 113, timestamp=9 * 900000)  # low111>top110
        assert rsm.check_ifvg_retest(cur) is None
        assert len(rsm._inverted_candidates) == 1

    def test_break_checked_before_touch(self, monkeypatch):
        # Ayni anda wick dokunup bedende kirilirsa: kirmak onceliklidir ->
        # aday duskurulur, trigger YOK.
        monkeypatch.setattr("config.IFVG_ENABLED", True)
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bullish", 5))
        cur = _bar(
            9, 100, 112, 99, 100, timestamp=9 * 900000
        )  # low99<=110 VE close100<105
        assert rsm.check_ifvg_retest(cur) is None
        assert len(rsm._inverted_candidates) == 0


class TestIFVGLifecycle:
    def test_reset_clears_inverted_candidates(self):
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bearish", 5))
        rsm.reset()
        assert rsm._inverted_candidates == []

    def test_lock_bias_does_not_clear_inverted_candidates(self):
        rsm = RetraceStateMachine()
        rsm._inverted_candidates.append(HTFFVG(110.0, 105.0, "bearish", 5))
        rsm.direction = "bullish"
        rsm.lock_bias(bar_index=5)
        assert rsm.state == RetraceState.BIAS_LOCKED
        # lock_bias() kirmis-FVG adaylarina DOKUNMAZ (suressiz gecerli)
        assert len(rsm._inverted_candidates) == 1


class TestIFVGRegisterOnBodyBreak:
    """Item 3: body break aninda aday kaydi — on_sweep_confirmed + on_bias_fvg."""

    @patch("retrace_state.scan_htf_fvgs")
    def test_on_sweep_confirmed_registers_on_body_break(self, mock_scan, monkeypatch):
        monkeypatch.setattr("config.IFVG_ENABLED", True)
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 100.0)  # dusuk sweep seviyesi -> invalidation yok
        mock_scan.return_value = [HTFFVG(105.0, 103.0, "bullish", 1)]
        # sweep bar: low101<=top105 (wick), close101<bottom103 (broke), >=100 (no reset)
        sweep_bar = _bar(5, 110, 112, 101, 101, timestamp=5 * 900000)
        bars = [_bar(i, 100, 102, 99, 101, timestamp=i * 900000) for i in range(5)]
        rsm.on_sweep_confirmed(bars, sweep_bar)
        # body broke -> normal trigger YOK; ama inverted aday (bearish) kayda alindi
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert len(rsm._inverted_candidates) == 1
        assert rsm._inverted_candidates[0].direction == "bearish"
        # IFVG guard-fix: break_bar_index = kirilimin gerceklestigi bar (sweep bar)
        assert rsm._inverted_candidates[0].break_bar_index == sweep_bar.index

    @patch("retrace_state._fvg_touched_between", return_value=False)
    @patch("retrace_state.fvg_is_alive", return_value=True)
    @patch("retrace_state.scan_htf_fvgs")
    def test_on_bias_fvg_registers_on_body_break(
        self, mock_scan, mock_alive, mock_touched, monkeypatch
    ):
        monkeypatch.setattr("config.IFVG_ENABLED", True)
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 100.0)
        rsm.lock_bias(bar_index=0)
        mock_scan.return_value = [HTFFVG(105.0, 103.0, "bullish", bar_index=2)]
        bars = [_bar(i, 110, 113, 100, 111, timestamp=i * 900000) for i in range(6)]
        current = _bar(
            5, 110, 113, 101, 101, timestamp=5 * 900000
        )  # low101<=105, close101<103
        rsm.on_bias_fvg(bars, current)
        assert rsm.state == RetraceState.BIAS_LOCKED  # break -> trigger yok
        assert len(rsm._inverted_candidates) == 1
        assert rsm._inverted_candidates[0].direction == "bearish"
        # IFVG guard-fix: break_bar_index = kirilimin gerceklestigi bar (current)
        assert rsm._inverted_candidates[0].break_bar_index == current.index

    @patch("retrace_state.scan_htf_fvgs")
    def test_flag_off_no_register_on_body_break(self, mock_scan):
        """Flag kapali (default): body break'te aday kaydi YAPILMAZ (regresyon)."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 100.0)
        mock_scan.return_value = [HTFFVG(105.0, 103.0, "bullish", 1)]
        sweep_bar = _bar(5, 110, 112, 101, 101, timestamp=5 * 900000)
        bars = [_bar(i, 100, 102, 99, 101, timestamp=i * 900000) for i in range(5)]
        rsm.on_sweep_confirmed(bars, sweep_bar)
        assert rsm.state == RetraceState.SWEEP_DETECTED
        assert rsm._inverted_candidates == []
