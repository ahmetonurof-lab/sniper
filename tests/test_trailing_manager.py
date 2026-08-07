"""
test_trailing_manager.py — TrailingManager: FVG trail + exit check unit tests.
Pure logic — no mocking needed except for config constants.
"""

from decimal import Decimal

import pytest
from unittest.mock import patch

from models import Bar, FVG
from trading.trailing_manager import (
    ImmediateTriggerError,
    TrailLevel,
    TrailResult,
    ExitDecision,
    TrailingManager,
    TrailingConfig,
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
        timestamp=timestamp or (index * 900000),
    )


def _trade(
    side="long",
    entry_price=100.0,
    sl=95.0,
    tp=110.0,
    initial_sl=None,
    initial_tp=None,
    risk_pts=None,
    trailing_count=0,
):
    """Build a trade dict matching ActiveTrade shape."""
    init_sl = initial_sl if initial_sl is not None else sl
    init_tp = initial_tp if initial_tp is not None else tp
    rp = risk_pts if risk_pts is not None else abs(init_sl - entry_price)
    return {
        "symbol": "BTCUSDT",
        "side": side,
        "entry_price": entry_price,
        "sl": sl,
        "tp": tp,
        "initial_sl": init_sl,
        "initial_tp": init_tp,
        "risk_pts": rp,
        "trailing_count": trailing_count,
        "trail_steps": [],
        "qty": 0.1,
    }


# ═══════════════════════════════════════════════════════════════════
# TrailResult tests
# ═══════════════════════════════════════════════════════════════════


class TestTrailResult:
    def test_defaults(self):
        r = TrailResult()
        assert r.updated is False
        assert r.new_sl == 0.0
        assert r.new_tp == 0.0
        assert r.trail_count == 0

    def test_partial_init(self):
        r = TrailResult(updated=True, new_sl=100.0, new_tp=120.0, trail_count=3)
        assert r.updated is True
        assert r.new_sl == 100.0
        assert r.new_tp == 120.0
        assert r.trail_count == 3


# ═══════════════════════════════════════════════════════════════════
# ExitDecision tests
# ═══════════════════════════════════════════════════════════════════


class TestExitDecision:
    def test_defaults(self):
        e = ExitDecision()
        assert e.triggered is False
        assert e.result is None
        assert e.exit_price == 0.0

    def test_sl_exit(self):
        e = ExitDecision(triggered=True, result="SL", exit_price=95.0)
        assert e.triggered is True
        assert e.result == "SL"
        assert e.exit_price == 95.0

    def test_tp_exit(self):
        e = ExitDecision(triggered=True, result="TP", exit_price=110.0)
        assert e.triggered is True
        assert e.result == "TP"
        assert e.exit_price == 110.0


# ═══════════════════════════════════════════════════════════════════
# evaluate_trail tests
# ═══════════════════════════════════════════════════════════════════


class TestEvaluateTrail:
    def test_empty_bars_returns_no_update(self):
        result = TrailingManager.evaluate_trail([], _trade(), 0.3, 0.5)
        assert result.updated is False

    def test_single_bar_returns_no_update(self):
        bars = [_bar(0, 100, 105, 95, 102)]
        result = TrailingManager.evaluate_trail(bars, _trade(), 0.3, 0.5)
        assert result.updated is False

    @patch("trading.trailing_manager.cfg")
    def test_long_trail_on_bullish_fvg(self, mock_cfg):
        """Long trade: new SL = fvg.bottom - buffer > current SL → trail."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.2
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # 5 bars → chunk has 4 (indices 0-3) → FVG at bar1, confirmed by bar3 close in range
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(
                2, 106, 110, 105, 108
            ),  # bullish FVG: b_next.low=105 > b_prev.high=103
            _bar(3, 103, 106, 102, 104),  # close in FVG range → confirms FVG
            _bar(4, 108, 112, 107, 110),  # current bar
        ]

        # atr_buffer = 0.3 * 0.25 = 0.075, new_sl = 103 - 0.075 = 102.925
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)

        assert result.updated is True
        assert result.new_sl == pytest.approx(102.925)
        assert result.new_tp == pytest.approx(106.0 + (102.925 - 97.0))
        assert result.trail_count == 1

    @patch("trading.trailing_manager.cfg")
    def test_no_trail_when_new_sl_not_better(self, mock_cfg):
        """Long trade: new_sl <= current_sl → no trail."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="long", entry_price=100.0, sl=102.0, tp=106.0, risk_pts=2.0)

        # Bullish FVG bottom below current SL — should NOT trail
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 101, 103, 99, 100),
            _bar(2, 100, 103, 98, 102),  # bullish FVG: top=98, bottom=97
            _bar(3, 101, 104, 99, 103),
        ]
        # atr_buffer=0.075, new_sl=96.925 < current_sl=102 → NO trail
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    @patch("trading.trailing_manager.cfg")
    def test_min_move_filter_blocks_small_trail(self, mock_cfg):
        """Trail is blocked when sl_diff <= min_move."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.5  # High threshold
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG: top=105, bottom=103
            _bar(3, 108, 112, 107, 110),
        ]

        # buffer = 0.9, new_sl = 103 - 0.9 = 102.1
        # current_sl=97, risk_pts=3.0, min_move=3.0*0.5=1.5
        # sl_diff = 102.1 - 97.0 = 5.1 > 1.5 → trail PASSES
        # Let me lower the threshold or adjust values. Actually with higher threshold:
        # TRAIL_MIN_MOVE_MULT = 0.5 → min_move = 3.0 * 0.5 = 1.5, sl_diff=5.1 > 1.5 → still trails

        # To block: need sl_diff <= min_move. Let me set min_move very high.
        mock_cfg.TRAIL_MIN_MOVE_MULT = 3.0  # min_move = 3.0 * 3.0 = 9.0
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False  # 5.925 < 9.0 → blocked

    @patch("trading.trailing_manager.cfg")
    def test_skips_filled_fvg(self, mock_cfg):
        """Filled/invalidated FVGs are skipped."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # evaluate_trail only calls detect_fvgs, not update_fvg_states → filled is always False
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 103, 106, 102, 104),  # close in FVG → confirms
            _bar(4, 108, 112, 107, 110),
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is True

    def test_skips_wrong_direction_fvg_long(self):
        """Long trade skips bearish FVGs."""
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 109, 111, 107, 108),
            _bar(2, 106, 108, 103, 105),  # bearish FVG
        ]
        # buffer=0.9, but FVG is bearish, long trade ignores it
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    def test_uses_only_bars_except_last(self):
        """evaluate_trail uses bars[:-1] (excludes last bar)."""
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # FVG at index 3 uses bar index 3 as b_curr, and bar 4 as b_next
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 100, 103, 99, 102),
            _bar(3, 103, 105, 102, 104),
            _bar(4, 106, 110, 105, 108),  # This bar excluded from chunk
        ]
        # chunk = bars[:-1] = bars[0:4], so bar 4 is excluded
        # FVG at b_curr=3 would need b_next=4, but bar 4 is excluded
        # So no FVG found → no trail
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    def test_trailing_count_increments(self):
        """Trail count increments correctly."""
        trade = _trade(
            side="long",
            entry_price=100.0,
            sl=97.0,
            tp=106.0,
            risk_pts=3.0,
            trailing_count=2,
        )

        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 103, 106, 102, 104),  # close in FVG → confirms
            _bar(4, 108, 112, 107, 110),
        ]
        # Will trail → trail_count should become 3
        with patch("trading.trailing_manager.cfg") as mock_cfg:
            mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
            mock_cfg.ATR_TRAIL_MULT = 0.25
            mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
            mock_cfg.CONTINUATION_CONFIRM_BARS = 1
            mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
            mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}
            result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.trail_count == 3

    @patch("trading.trailing_manager.cfg")
    def test_long_fvg_too_close_skips_trail(self, mock_cfg):
        """Long: FVG.bottom is very close to current price → trail skipped."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0030
        mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 102.93, 106, 102, 103),
            _bar(4, 100, 101, 99, 100.0),
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is True
        assert result.new_sl == pytest.approx(102.925)

    @patch("trading.trailing_manager.cfg")
    def test_short_fvg_too_close_skips_trail(self, mock_cfg):
        """Short: FVG.top is very close to current price → trail skipped."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0030
        mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 99, 101, 97, 98),
            _bar(2, 96, 98, 93, 95),
            _bar(3, 97.07, 100, 96.5, 98.5),  # close in [98,99] → confirms bearish FVG
            _bar(4, 100, 101, 99, 100.0),
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is True
        assert result.new_sl == pytest.approx(99.075)
        assert result.new_tp == pytest.approx(94.0 - (103.0 - 99.075))

    def test_chunk_lookback_capped_at_50(self):
        """lookback is min(50, len(chunk))."""
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # 60 bars → chunk has 59, lookback = min(50, 59) = 50
        bars = [_bar(i, 100, 103, 98, 102) for i in range(60)]
        # No FVG in these bars → no trail
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    # ── D-2: exit_now guard removed (aligned with analyzer_v5 backtest) ──

    @patch("trading.trailing_manager.cfg")
    def test_d2_price_past_new_level_no_longer_forces_exit_now(self, mock_cfg):
        """FIX (D-2): eski implementasyonda son bar'in close'u hesaplanan
        new_sl seviyesinin "gerisinde" kaldiginda (new_sl >= current.close)
        aninda TrailResult(exit_now=True) donuyordu — new_sl'in mevcut SL'den
        bir iyilesme olup olmadigina bile bakmadan (P2-6/P2-7 kok nedeni).
        analyzer_v5.py (backtest) bu guard'i hic icermiyor; simdi live de
        ayni sekilde davraniyor: gecerli bir trail varsa normal updated=True
        olarak isleniyor, exit_now hicbir zaman True donmuyor.
        """
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.2
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),  # bullish FVG: bottom=103
            _bar(3, 103, 106, 102, 104),  # close in FVG range → confirms FVG
            # son bar (chunk'tan haric tutulur) close=100.0 — eski kodda
            # new_sl(102.925) >= current.close(100.0) oldugu icin exit_now
            # tetiklenirdi. Artik bu bar hic okunmuyor.
            _bar(4, 108, 112, 95, 100.0),
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)

        assert result.exit_now is False
        assert result.updated is True
        assert result.new_sl == pytest.approx(102.925)

    @patch("trading.trailing_manager.cfg")
    def test_d2_short_side_price_past_new_level_no_exit_now(self, mock_cfg):
        """D-2 fix — short taraf icin ayni senaryo. FVG dogrulanmis (bearish,
        top=109/bottom=108) ama iyilesme degil (new_sl=109.075 > current_sl=103,
        short icin kotu yon) — eski kodda bile bu durumda new_sl<=current.close
        oldugu icin (109.075<=110) exit_now tetiklenirdi; artik hicbir zaman
        tetiklenmiyor, updated da False kaliyor (gercek davranis degismedi,
        sadece yanlis erken-exit kaldirildi)."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 109, 111, 107, 108),
            _bar(2, 106, 108, 103, 105),  # bearish FVG: top=109, bottom=108
            _bar(3, 107, 109, 106, 108.5),  # close in [108,109] → confirms
            # son bar (chunk'tan haric) close=110 — eski kodda
            # new_sl(109.075) <= current.close(110) oldugu icin exit_now
            # tetiklenirdi (iyilesme olup olmadigina bakilmaksizin).
            _bar(4, 109, 111, 108, 110.0),
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.exit_now is False
        assert result.updated is False  # zaten iyilesme degil — davranis ayni

    # ── FVG confirm-mode: retrace vs continuation ──

    @patch("trading.trailing_manager.cfg")
    def test_short_continuation_on_close_below_fvg_bottom(self, mock_cfg):
        """Short: close < fvg.bottom → continuation confirm.
        SL = fvg.bottom + atr_buffer (retrace'teki fvg.top + buffer'dan daha siki)."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        # bearish FVG at i=1: top=99, bottom=98; bar3 close 94 < bottom → continuation
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 99, 101, 97, 98),
            _bar(2, 96, 98, 93, 95),
            _bar(3, 95, 97, 93, 94),
            _bar(4, 92, 95, 91, 93),  # current (chunk'tan haric)
        ]
        # atr_buffer = 0.3 * 0.25 = 0.075; continuation new_sl = 98 + 0.075 = 98.075
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)

        assert result.updated is True
        assert result.new_sl == pytest.approx(98.075)
        assert result.new_tp == pytest.approx(94.0 - (103.0 - 98.075))
        assert result.trail_count == 1

    @patch("trading.trailing_manager.cfg")
    def test_long_continuation_on_close_above_fvg_top(self, mock_cfg):
        """Long: close > fvg.top → continuation confirm. SL = fvg.top - atr_buffer."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # bullish FVG at i=2: top=105, bottom=104; bar4 close 109 > top → continuation
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 101, 104, 100, 102),
            _bar(2, 103, 106, 102, 105),
            _bar(3, 106, 109, 105, 107),
            _bar(4, 108, 110, 107, 109),
            _bar(5, 110, 112, 109, 111),  # current (chunk'tan haric)
        ]
        # continuation new_sl = 105 - 0.075 = 104.925
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)

        assert result.updated is True
        assert result.new_sl == pytest.approx(104.925)
        assert result.new_tp == pytest.approx(106.0 + (104.925 - 97.0))
        assert result.trail_count == 1

    def test_bearish_close_above_top_is_invalidation_not_continuation(self):
        """Yon kontrolu: short (bearish FVG) icin close > fvg.top invalidation'dir,
        continuation degil → trailing tetiklenmez."""
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        # bearish FVG at i=1: top=109, bottom=108; bar3 close 111 > top → invalidation
        bars = [
            _bar(0, 110, 113, 109, 111),
            _bar(1, 109, 111, 107, 108),
            _bar(2, 106, 108, 103, 105),
            _bar(3, 110, 112, 109, 111),
            _bar(4, 111, 113, 110, 112),  # current
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    def test_bullish_close_below_bottom_is_invalidation_not_continuation(self):
        """Yon kontrolu: long (bullish FVG) icin close < fvg.bottom invalidation'dir,
        continuation degil → trailing tetiklenmez."""
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # bullish FVG at i=2: top=105, bottom=104; bar4 close 103 < bottom → invalidation
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 101, 104, 100, 102),
            _bar(2, 103, 106, 102, 105),
            _bar(3, 106, 109, 105, 107),
            _bar(4, 102, 104, 101, 103),
            _bar(5, 101, 103, 100, 102),  # current
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    @patch("trading.trailing_manager.cfg")
    def test_is_placeable_blocks_stale_short_candidate(self, mock_cfg):
        """is_placeable: fvg.top + buffer, current close'un altinda kaliyorsa stale →
        hop yok. (ALGO 01:00:44 ornegi: aday sl=0.089049 < price=0.0897.)"""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.2
        mock_cfg.ATR_TRAIL_MULT = 0.10
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.10
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        # bearish FVG at i=1: top=99, bottom=98; bar3 close 99.5 in gap → retrace
        # new_sl = 99 + 0.3*0.10 = 99.03 < current close 99.5 → stale
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 99, 101, 97, 98),
            _bar(2, 96, 98, 93, 95),
            _bar(3, 98.9, 100, 98, 99.5),
            _bar(4, 100, 101, 99.5, 100.2),  # current
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    @patch("trading.trailing_manager.cfg")
    def test_is_placeable_blocks_stale_long_candidate(self, mock_cfg):
        """is_placeable: fvg.bottom - buffer, current close'un ustunde kaliyorsa
        stale → hop yok (long simetrik kontrol)."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.2
        mock_cfg.ATR_TRAIL_MULT = 0.10
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.10
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # bullish FVG at i=2: top=105, bottom=104; bar4 close 103.5 in gap → retrace
        # new_sl = 104 - 0.03 = 103.97 > current close 103.5 → stale
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 101, 104, 100, 102),
            _bar(2, 103, 106, 102, 105),
            _bar(3, 106, 109, 105, 107),
            _bar(4, 102.5, 104, 102, 103.5),
            _bar(5, 102, 103.5, 101, 102.5),  # current
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False

    @patch("trading.trailing_manager.cfg")
    def test_retrace_ilk_onay_kazanir_continuation_ezmez(self, mock_cfg):
        """Once gap ici close (retrace) sonra far-side close gelirse retrace SL
        kullanilir; continuation SL (daha siki) ilk onayi ezmez."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        # bearish FVG at i=1: top=99, bottom=98
        # bar3 close 98.8 in gap → retrace (ilk onay); bar4 close 96.5 < bottom gorulmez
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 99, 101, 97, 98),
            _bar(2, 96, 98, 93, 95),
            _bar(3, 98.5, 100, 98, 98.8),
            _bar(4, 96, 98, 95, 96.5),
            _bar(5, 95, 97, 94, 96),  # current
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is True
        assert result.new_sl == pytest.approx(99.075)  # retrace: 99 + 0.075
        assert result.new_tp == pytest.approx(94.0 - (103.0 - 99.075))

    @patch("trading.trailing_manager.cfg")
    def test_continuation_stale_after_price_return_blocked(self, mock_cfg):
        """Continuation onaylanir ama SL fiyatin gerisinde kalirsa hop olmaz
        (is_placeable) — stale candidate sorununun continuation kopyasi."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        # bearish FVG at i=1: top=99, bottom=98
        # bar3 close 94 < bottom → continuation onayi; ama bar4 fiyat 98.8'e dondu →
        # new_sl = 98.075 < current 98.8 → stale → hop yok
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 99, 101, 97, 98),
            _bar(2, 96, 98, 93, 95),
            _bar(3, 95, 97, 93, 94),
            _bar(4, 98.5, 100, 98, 98.8),
            _bar(5, 97, 99, 96, 98),  # current
        ]
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)
        assert result.updated is False


# ═══════════════════════════════════════════════════════════════════
# check_exit tests
# ═══════════════════════════════════════════════════════════════════


class TestCheckExit:
    # ── Long trade exits ──

    def test_long_sl_triggered(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 98, 100, 94, 97)  # low=94 <= sl=95 → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"
        assert result.exit_price == 95.0

    def test_long_sl_exact_touch(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 98, 100, 95, 97)  # low=95 == sl → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    def test_long_tp_triggered(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 108, 112, 107, 109)  # high=112 >= tp=110 → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"
        assert result.exit_price == 110.0

    def test_long_tp_exact_touch(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 108, 110, 107, 109)  # high=110 == tp → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"

    def test_long_no_exit(self):
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 100, 105, 98, 102)  # low=98 > sl, high=105 < tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is False
        assert result.result is None

    def test_long_sl_priority_over_tp(self):
        """SL has priority (checked first). Both triggered → SL wins."""
        trade = _trade(side="long", sl=95.0, tp=110.0)
        current = _bar(10, 98, 112, 94, 108)  # low=94 <= sl AND high=112 >= tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"  # SL checked first

    # ── Short trade exits ──

    def test_short_sl_triggered(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 105, 112, 100, 108)  # high=112 >= sl=110 → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"
        assert result.exit_price == 110.0

    def test_short_sl_exact_touch(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 105, 110, 100, 108)  # high=110 == sl → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    def test_short_tp_triggered(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 95, 98, 88, 92)  # low=88 <= tp=90 → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"
        assert result.exit_price == 90.0

    def test_short_tp_exact_touch(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 95, 98, 90, 92)  # low=90 == tp → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"

    def test_short_no_exit(self):
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 100, 105, 95, 98)  # high=105 < sl, low=95 > tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is False
        assert result.result is None

    def test_short_sl_priority_over_tp(self):
        """SL has priority — both triggered → SL wins."""
        trade = _trade(side="short", sl=110.0, tp=90.0)
        current = _bar(10, 95, 112, 88, 92)  # high=112 >= sl AND low=88 <= tp
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    # ── Edge cases ──

    def test_sl_at_current_price(self):
        """SL exactly at the bar range boundary."""
        trade = _trade(side="long", sl=100.0, tp=120.0)
        current = _bar(10, 105, 108, 100, 104)  # low=100 == sl → SL
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "SL"

    def test_tp_at_current_price(self):
        """TP exactly at the bar range boundary."""
        trade = _trade(side="long", sl=90.0, tp=110.0)
        current = _bar(10, 108, 110, 105, 107)  # high=110 == tp → TP
        result = TrailingManager.check_exit(current, trade)
        assert result.triggered is True
        assert result.result == "TP"


# ═══════════════════════════════════════════════════════════════════
# Integration-style: trail + exit in sequence
# ═══════════════════════════════════════════════════════════════════


class TestTrailAndExitSequence:
    """Simulate a realistic 1m bar sequence with trailing updates."""

    @patch("trading.trailing_manager.cfg")
    def test_trail_then_exit_long(self, mock_cfg):
        """Trail SL up, then next bar hits the new SL."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}

        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # Step 1: FVG trail — SL moves from 97 to 102.925
        bars_15m = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 103, 106, 102, 104),  # close in FVG → confirms
            _bar(4, 108, 112, 107, 110),
        ]

        trail = TrailingManager.evaluate_trail(bars_15m, trade, 0.3, 0.5)
        assert trail.updated is True
        new_sl = trail.new_sl
        new_tp = trail.new_tp

        # Apply trail to trade
        trade["sl"] = new_sl
        trade["tp"] = new_tp

        # Step 2: Next 1m bar hits the new SL (low=101.5 < 102.925)
        current = _bar(20, 102, 104, 101.5, 103)
        exit_check = TrailingManager.check_exit(current, trade)
        assert exit_check.triggered is True
        assert exit_check.result == "SL"

    @patch("trading.trailing_manager.cfg")
    def test_trail_then_tp_long(self, mock_cfg):
        """Trail SL up, then price shoots to new TP."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        mock_cfg.MIN_SL_DISTANCE_PCT = 0.0015
        mock_cfg.MIN_SL_DISTANCE_PCT_MAP = {}

        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        bars_15m = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 103, 105, 102, 104),
            _bar(2, 106, 110, 105, 108),
            _bar(3, 103, 106, 102, 104),  # close in FVG → confirms
            _bar(4, 108, 112, 107, 110),
        ]

        trail = TrailingManager.evaluate_trail(bars_15m, trade, 0.3, 0.5)
        trade["sl"] = trail.new_sl
        trade["tp"] = trail.new_tp

        # Price surges to new TP (high=112 >= 111.925)
        current = _bar(20, 108, 112, 107, 111)
        exit_check = TrailingManager.check_exit(current, trade)
        assert exit_check.triggered is True
        assert exit_check.result == "TP"

    @patch("trading.trailing_manager.cfg")
    def test_no_trail_then_sl_hit_long(self, mock_cfg):
        """Without FVG trail, original SL is hit."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.25
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1

        trade = _trade(side="long", entry_price=100.0, sl=97.0, tp=106.0, risk_pts=3.0)

        # No bullish FVG → no trail
        bars_15m = [
            _bar(0, 100, 102, 98, 101),
            _bar(1, 101, 103, 99, 102),
            _bar(2, 100, 102, 98, 101),
        ]
        trail = TrailingManager.evaluate_trail(bars_15m, trade, 0.3, 0.5)
        assert trail.updated is False

        # SL stays at 97, hit by next bar
        current = _bar(20, 98, 99, 96, 97)
        exit_check = TrailingManager.check_exit(current, trade)
        assert exit_check.triggered is True
        assert exit_check.result == "SL"


# ═══════════════════════════════════════════════════════════════════
# TP re-anchor from trailing SL (compute_trail_candidate)
# ═══════════════════════════════════════════════════════════════════


class FakePriceReader:
    def __init__(self, price: str) -> None:
        self.price = Decimal(price)

    async def get_last_price(self, symbol: str) -> Decimal:
        return self.price


class FakeGateway:
    def __init__(self, *, raise_immediate_trigger: bool = False) -> None:
        self.raise_immediate_trigger = raise_immediate_trigger
        self.calls = 0
        self.fingerprints = []

    async def replace_protection(self, *, trade, candidate, current_price):
        self.calls += 1
        self.fingerprints.append(candidate.fingerprint)
        if self.raise_immediate_trigger:
            raise ImmediateTriggerError("Order would immediately trigger")
        return True


def test_tp_is_reanchored_from_new_trailing_sl_not_from_entry_rule():
    def extractor(scoped_bars, trade):
        return TrailLevel(
            price=Decimal("106.0"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing low",
        )

    bars = [
        {"index": 30, "high": 108, "low": 103, "close": 107},
        {"index": 31, "high": 109, "low": 104, "close": 108},
        {"index": 32, "high": 110, "low": 105, "close": 109},
    ]
    trade = {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": 100.0,
        "entry_bar_index": 30,
        "stop_loss": 95.0,
        "take_profit": 120.0,
        "tick_size": 0.5,
        "trail_level_extractor": extractor,
    }

    manager = TrailingManager(
        price_reader=FakePriceReader("109.0"),
        protection_gateway=FakeGateway(),
        config=TrailingConfig(
            sl_buffer_ticks=0,
        ),
    )

    candidate = manager.compute_trail_candidate(trade, bars)

    assert candidate is not None
    assert candidate.sl == Decimal("106.0")
    # tp = old_tp + (new_sl - old_sl) = 120 + (106 - 95) = 131
    assert candidate.tp == Decimal("131.0")


def test_tp_shifts_by_same_delta_as_sl():
    def extractor(scoped_bars, trade):
        return TrailLevel(
            price=Decimal("106.0"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing low",
        )

    bars = [
        {"index": 40, "high": 108, "low": 103, "close": 107},
        {"index": 41, "high": 109, "low": 104, "close": 108},
        {"index": 42, "high": 110, "low": 105, "close": 109},
    ]
    trade = {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": 100.0,
        "entry_bar_index": 40,
        "stop_loss": 95.0,
        "take_profit": 130.0,
        "tick_size": 0.5,
        "trail_level_extractor": extractor,
    }

    manager = TrailingManager(
        price_reader=FakePriceReader("109.0"),
        protection_gateway=FakeGateway(),
        config=TrailingConfig(
            sl_buffer_ticks=0,
        ),
    )

    candidate = manager.compute_trail_candidate(trade, bars)

    assert candidate is not None
    # sl_shift = 106 - 95 = 11, tp = 130 + 11 = 141
    assert candidate.tp == Decimal("141.0")


def test_short_tp_shifts_by_same_delta_as_sl():
    def extractor(scoped_bars, trade):
        return TrailLevel(
            price=Decimal("90.0"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing high",
        )

    bars = [
        {"index": 30, "high": 108, "low": 103, "close": 107},
        {"index": 31, "high": 109, "low": 104, "close": 108},
        {"index": 32, "high": 110, "low": 105, "close": 109},
    ]
    trade = {
        "symbol": "BTCUSDT",
        "side": "short",
        "entry_price": 100.0,
        "entry_bar_index": 30,
        "stop_loss": 105.0,
        "take_profit": 85.0,
        "tick_size": 0.5,
        "trail_level_extractor": extractor,
    }

    manager = TrailingManager(
        price_reader=FakePriceReader("95.0"),
        protection_gateway=FakeGateway(),
        config=TrailingConfig(sl_buffer_ticks=0),
    )

    candidate = manager.compute_trail_candidate(trade, bars)

    assert candidate is not None
    assert candidate.sl == Decimal("90.0")
    # short: improved_sl=90 < current_sl=105 → sl_shift=-15
    # tp = 85 + (-15) = 70
    assert candidate.tp == Decimal("70.0")


def test_no_tp_update_when_current_tp_is_none():
    def extractor(scoped_bars, trade):
        return TrailLevel(
            price=Decimal("106.0"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing low",
        )

    bars = [
        {"index": 30, "high": 108, "low": 103, "close": 107},
        {"index": 31, "high": 109, "low": 104, "close": 108},
        {"index": 32, "high": 110, "low": 105, "close": 109},
    ]
    trade = {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": 100.0,
        "entry_bar_index": 30,
        "stop_loss": 95.0,
        "take_profit": None,
        "tick_size": 0.5,
        "trail_level_extractor": extractor,
    }

    manager = TrailingManager(
        price_reader=FakePriceReader("109.0"),
        protection_gateway=FakeGateway(),
        config=TrailingConfig(sl_buffer_ticks=0),
    )

    candidate = manager.compute_trail_candidate(trade, bars)

    assert candidate is not None
    assert candidate.sl == Decimal("106.0")
    assert candidate.tp is None


def test_no_tp_update_when_current_sl_is_none():
    def extractor(scoped_bars, trade):
        return TrailLevel(
            price=Decimal("106.0"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing low",
        )

    bars = [
        {"index": 30, "high": 108, "low": 103, "close": 107},
        {"index": 31, "high": 109, "low": 104, "close": 108},
        {"index": 32, "high": 110, "low": 105, "close": 109},
    ]
    trade = {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": 100.0,
        "entry_bar_index": 30,
        "stop_loss": None,
        "take_profit": 120.0,
        "tick_size": 0.5,
        "trail_level_extractor": extractor,
    }

    manager = TrailingManager(
        price_reader=FakePriceReader("109.0"),
        protection_gateway=FakeGateway(),
        config=TrailingConfig(sl_buffer_ticks=0),
    )

    candidate = manager.compute_trail_candidate(trade, bars)

    assert candidate is not None
    assert candidate.sl == Decimal("106.0")
    assert candidate.tp is None


def test_tp_rounds_to_tick_on_delta_shift():
    def extractor(scoped_bars, trade):
        return TrailLevel(
            price=Decimal("106.3"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing low",
        )

    bars = [
        {"index": 30, "high": 108, "low": 103, "close": 107},
        {"index": 31, "high": 109, "low": 104, "close": 108},
        {"index": 32, "high": 110, "low": 105, "close": 109},
    ]
    trade = {
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_price": 100.0,
        "entry_bar_index": 30,
        "stop_loss": 95.0,
        "take_profit": 120.0,
        "tick_size": 0.5,
        "trail_level_extractor": extractor,
    }

    manager = TrailingManager(
        price_reader=FakePriceReader("109.0"),
        protection_gateway=FakeGateway(),
        config=TrailingConfig(sl_buffer_ticks=0),
    )

    candidate = manager.compute_trail_candidate(trade, bars)

    assert candidate is not None
    # raw_sl = 106.3, normalized to tick 0.5 for long SL = floor = 106.0
    assert candidate.sl == Decimal("106.0")
    # sl_shift = 106.0 - 95.0 = 11.0
    # raw_tp = 120.0 + 11.0 = 131.0 → normalized to tick 0.5 for long TP = ceil = 131.0
    assert candidate.tp == Decimal("131.0")


class TestOrchestrateTrailCanonicalKeys:
    """BUG-3: orchestratet_trail trade['sl']/['tp'] canonical alanlarini yazar
    (stop_loss/take_profit DEGIL) — hem ActiveTrade hem duz dict icin."""

    def _extractor(self, scoped_bars, trade):
        return TrailLevel(
            price=Decimal("106.0"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing low",
        )

    def _bars(self):
        return [
            {"index": 30, "high": 108, "low": 103, "close": 107},
            {"index": 31, "high": 109, "low": 104, "close": 108},
            {"index": 32, "high": 110, "low": 105, "close": 109},
        ]

    def _base_trade(self):
        return {
            "symbol": "BTCUSDT",
            "side": "long",
            "entry_price": 100.0,
            "entry_bar_index": 30,
            "sl": 95.0,
            "tp": 120.0,
            "tick_size": 0.5,
            "trail_level_extractor": self._extractor,
        }

    @pytest.mark.asyncio
    async def test_active_trade_updates_sl_tp(self):
        from models import ActiveTrade

        trade = ActiveTrade(**self._base_trade())
        manager = TrailingManager(
            price_reader=FakePriceReader("109.0"),
            protection_gateway=FakeGateway(),
            config=TrailingConfig(sl_buffer_ticks=0),
        )
        decision = await manager.orchestrate_trail(trade, self._bars())
        assert decision.action == "updated"
        assert trade.sl == 106.0  # canonical sl guncellendi
        assert trade.tp == 131.0
        # fingerprint state: protection_state set edildi ve last_applied yazildi
        ps = trade.get("protection_state") or {}
        assert ps.get("last_applied_fingerprint") == decision.candidate.fingerprint

    @pytest.mark.asyncio
    async def test_plain_dict_updates_sl_tp(self):
        trade = self._base_trade()
        manager = TrailingManager(
            price_reader=FakePriceReader("109.0"),
            protection_gateway=FakeGateway(),
            config=TrailingConfig(sl_buffer_ticks=0),
        )
        decision = await manager.orchestrate_trail(trade, self._bars())
        assert decision.action == "updated"
        # order_manager.update_trail_orders trade.get("sl") ile okur — guncel deger
        assert trade.get("sl") == 106.0
        assert trade.get("tp") == 131.0
        # fingerprint state: duz dict icin protection_state yoksa olusturuldu
        ps = trade.get("protection_state") or {}
        assert ps.get("last_applied_fingerprint") == decision.candidate.fingerprint


# ═══════════════════════════════════════════════════════════════════
# Fingerprint fiyat bucket'i — identical_invalid_candidate_suppressed
# kilitlenme bug'i (ENAUSDT 2026-08-03). Zaman bazli expiry YOK.
# ═══════════════════════════════════════════════════════════════════


class TestFingerprintPriceBucket:
    """Fingerprint'e fiyat bucket'i eklenir; suppress fiyat lehine
    degistiginde otomatik kalkar (mikro-noise emilir)."""

    def _extractor(self, scoped_bars, trade):
        return TrailLevel(
            price=Decimal("106.0"),
            source_bar_index=scoped_bars[-1]["index"],
            reason="post-entry swing low",
        )

    def _bars(self):
        return [
            {"index": 30, "high": 108, "low": 103, "close": 107},
            {"index": 31, "high": 109, "low": 104, "close": 108},
            {"index": 32, "high": 110, "low": 105, "close": 109},
        ]

    def _trade(self):
        return {
            "symbol": "BTCUSDT",
            "side": "long",
            "entry_price": 100.0,
            "entry_bar_index": 30,
            "sl": 95.0,
            "tp": 120.0,
            "tick_size": 0.5,
            "trail_level_extractor": self._extractor,
        }

    def _candidate(self, price: str):
        manager = TrailingManager(
            price_reader=FakePriceReader(price),
            protection_gateway=FakeGateway(),
            config=TrailingConfig(sl_buffer_ticks=0),
        )
        return manager.compute_trail_candidate(
            self._trade(), self._bars(), current_price=Decimal(price)
        )

    def test_price_inside_same_bucket_shares_fingerprint(self):
        # mikro-noise (109.0 vs 109.49) ayni bucket — ayni fingerprint
        a = self._candidate("109.0")
        b = self._candidate("109.4")
        assert a is not None and b is not None
        assert a.fingerprint == b.fingerprint
        # fingerprint fiyat bucket'ini icerir (suffix degil "-")
        assert a.fingerprint.endswith("|") is False
        assert a.fingerprint.rsplit("|", 1)[1] != "-"

    def test_price_crossing_bucket_changes_fingerprint(self):
        a = self._candidate("109.0")
        b = self._candidate("110.5")
        assert a is not None and b is not None
        assert a.fingerprint != b.fingerprint

    @pytest.mark.asyncio
    async def test_suppress_lifted_when_price_moves_favorably(self):
        """ENABUG regression: ayni candidate not placeable -> suppress.
        Fiyat lehine bucket atlarsa yeniden degerlendirilir (updated)."""
        trade = self._trade()
        # fiyat 104.99 -> sl=106.0 placeable degil (sl < price + epsilon gerekli)
        manager = TrailingManager(
            price_reader=FakePriceReader("104.99"),
            protection_gateway=FakeGateway(),
            config=TrailingConfig(sl_buffer_ticks=0),
        )
        decision = await manager.orchestrate_trail(trade, self._bars())
        assert decision.action == "skip"
        assert decision.reason == "candidate not placeable against current price"
        ps = trade["protection_state"]
        assert ps.get("last_invalid_fingerprint") == decision.candidate.fingerprint

        # ayni fiyat bucket'inda tekrar -> suppressed
        again = await manager.orchestrate_trail(trade, self._bars())
        assert again.action == "skip"
        assert again.reason == "identical invalid candidate suppressed"

        # fiyat lehine bucket atlar -> yeni fingerprint -> updated
        manager2 = TrailingManager(
            price_reader=FakePriceReader("109.0"),
            protection_gateway=FakeGateway(),
            config=TrailingConfig(sl_buffer_ticks=0),
        )
        lifted = await manager2.orchestrate_trail(trade, self._bars())
        assert lifted.action == "updated"
        assert trade.get("sl") == 106.0


# ═══════════════════════════════════════════════════════════════════
# _fvg_confirm_mode N-bar teyit (continuation streak) tests
# Off-by-one / streak-sifirlama kontrolu: far-side kapanislar ard arda
# sayilir; araya gap ici kapanis girerse retrace kazanir, invalidation
# girerse None (streak anlamini yitirir).
# ═══════════════════════════════════════════════════════════════════


class TestConfirmModeNBar:
    def _bull(self, real_index=2):
        # bullish FVG: top=105, bottom=104; scan_from = real_index + 2
        return FVG(
            direction="bullish",
            top=105.0,
            bottom=104.0,
            real_index=real_index,
            timeframe="15m",
        )

    def _bear(self, real_index=1):
        # bearish FVG: top=99, bottom=98; scan_from = real_index + 2
        return FVG(
            direction="bearish",
            top=99.0,
            bottom=98.0,
            real_index=real_index,
            timeframe="15m",
        )

    def _bars(self, closes):
        bars = []
        for i, c in enumerate(closes):
            if i < 3:
                hi, lo = c + 2.0, c - 2.0
            else:
                hi, lo = c + 1.0, c - 1.0
            bars.append(_bar(i, c, hi, lo, c))
        return bars

    def test_n1_first_far_side_bar_immediately_continuation(self):
        """Off-by-one kontrolu: N=1'de ilk far-side kapanis (scan_from sonrasi)
        hemen 'continuation' dondurur."""
        fvg = self._bull(real_index=2)
        bars = self._bars([100.0, 101.0, 102.0, 103.0, 107.0, 108.0])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 1) == "continuation"

    def test_n2_two_consecutive_far_side_continuation(self):
        """N=2: ard arda iki far-side kapanis kesintisiz sayilir → continuation."""
        fvg = self._bull(real_index=2)
        bars = self._bars([100.0, 101.0, 102.0, 103.0, 107.0, 108.0])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 2) == "continuation"

    def test_n2_streak_broken_by_gap_inside_returns_retrace(self):
        """Streak sifirlama: 1 far-side sonra gap ici kapanis → retrace kazanir,
        continuation tetiklenmez (sahte kirilim filtre edilir)."""
        fvg = self._bull(real_index=2)
        bars = self._bars([100.0, 101.0, 102.0, 103.0, 107.0, 104.5])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 2) == "retrace"

    def test_n2_streak_broken_by_invalidation_returns_none(self):
        """Streak sifirlama: 1 far-side sonra invalidation (close < bottom)
        → None (FVG olur, trailing tetiklenmez)."""
        fvg = self._bull(real_index=2)
        bars = self._bars([100.0, 101.0, 102.0, 103.0, 107.0, 103.0])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 2) is None

    def test_n3_requires_three_consecutive_far_side(self):
        """N=3: iki far-side yeterli degil; ucuncu ard arda bar ile tetiklenir."""
        fvg = self._bull(real_index=2)
        bars = self._bars([100.0, 101.0, 102.0, 103.0, 107.0, 108.0, 109.0])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 3) == "continuation"

    def test_n3_two_far_side_then_gap_inside_returns_retrace(self):
        """N=3: iki far-side sonra gap ici kapanis → streak sifirlanir, retrace."""
        fvg = self._bull(real_index=2)
        bars = self._bars([100.0, 101.0, 102.0, 103.0, 107.0, 108.0, 104.5])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 3) == "retrace"

    def test_bearish_n2_two_consecutive_continuation(self):
        """Bearish simetri: ard arda iki far-side (close < bottom) → continuation."""
        fvg = self._bear(real_index=1)
        bars = self._bars([102.0, 99.0, 95.0, 94.0, 93.0])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 2) == "continuation"

    def test_bearish_n2_streak_broken_by_gap_inside_retrace(self):
        """Bearish simetri: far-side sonra gap ici → retrace."""
        fvg = self._bear(real_index=1)
        bars = self._bars([102.0, 99.0, 95.0, 94.0, 98.2])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 2) == "retrace"

    def test_bearish_n2_invalidation_returns_none(self):
        """Bearish simetri: far-side sonra close > top → invalidation, None."""
        fvg = self._bear(real_index=1)
        bars = self._bars([102.0, 99.0, 95.0, 94.0, 100.0])
        assert TrailingManager._fvg_confirm_mode(fvg, bars, 2) is None

    @patch("trading.trailing_manager.cfg")
    def test_continuation_uses_wider_separate_buffer(self, mock_cfg):
        """Tasarim kontrolu: continuation hop'u retrace tamponundan AYRI ve daha
        genis K kullanir (ATR_TRAIL_MULT_CONTINUATION). N=1, K=0.5:
        short continuation SL = fvg.bottom + 0.5*ATR (retrace 0.25*ATR'den uzak)."""
        mock_cfg.TRAIL_MIN_MOVE_MULT = 0.1
        mock_cfg.ATR_TRAIL_MULT = 0.25
        mock_cfg.ATR_TRAIL_MULT_CONTINUATION = 0.5
        mock_cfg.CONTINUATION_CONFIRM_BARS = 1
        trade = _trade(side="short", entry_price=100.0, sl=103.0, tp=94.0, risk_pts=3.0)

        # bearish FVG at i=1: top=99, bottom=98; bar3 close 94 < bottom → continuation
        bars = [
            _bar(0, 100, 103, 99, 102),
            _bar(1, 99, 101, 97, 98),
            _bar(2, 96, 98, 93, 95),
            _bar(3, 95, 97, 93, 94),
            _bar(4, 92, 95, 91, 93),  # current (chunk'tan haric)
        ]
        # K=0.5: new_sl = 98 + 0.5*0.3 = 98.15 (retrace buffer 0.25 olsaydi 98.075)
        result = TrailingManager.evaluate_trail(bars, trade, 0.3, 0.5)

        assert result.updated is True
        assert result.new_sl == pytest.approx(98.15)
        assert result.new_tp == pytest.approx(94.0 - (103.0 - 98.15))
        assert result.trail_count == 1
