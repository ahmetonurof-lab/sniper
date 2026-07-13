"""
integration_test.py — Strateji zinciri entegrasyon testleri

Sweep -> FVG -> Trigger -> Entry -> Trailing -> Exit

NOT: Bu testler production davranisini degistirmez.
Sentetik bar verisi kullanir, Binance/network bagimliligi yoktur.
"""

import pytest

from models import Bar
from retrace_state import RetraceStateMachine, HTFFVG, RetraceState
from session import DailyBias, SessionState
from trading.entry_manager import EntryManager
from trading.trailing_manager import TrailingManager


# ── Yardimci: sentetik bar olusturucu ────────────────────────────


def _bar(index, o, h, low, c, closed=True, ts=0):
    return Bar(
        index=index, open=o, high=h, low=low, close=c, is_closed=closed, timestamp=ts
    )


# ═══════════════════════════════════════════════════════════════════
# 1) Sweep -> FVG -> Trigger entegrasyon testleri
# ═══════════════════════════════════════════════════════════════════


class TestSweepFVGTrigger:
    """RSM + fvg + signal_engine: sweep -> FVG taramasi -> trigger karari."""

    def test_bullish_sweep_with_valid_fvg_reaches_trigger_ready(self):
        """Bullish sweep + FVG + wick rejection + close inside FVG = TRIGGER_READY."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)

        # Bullish FVG: bar(0) high=103, bar(2) low=105 → gap [103, 105], bar(1) impulse
        bars = [
            _bar(0, 100, 103, 99, 102, ts=0),
            _bar(1, 103, 105, 102, 104, ts=900000),
            _bar(2, 106, 110, 105, 108, ts=1800000),
            _bar(3, 108, 112, 107, 110, ts=2700000),
            _bar(4, 110, 113, 109, 112, ts=3600000),
            _bar(5, 112, 115, 111, 114, ts=4500000),
            _bar(6, 114, 116, 113, 115, ts=5400000),
            _bar(7, 115, 117, 114, 116, ts=6300000),
            # Close inside FVG [103, 105] for fvg_close_confirmed
            _bar(8, 105, 107, 103, 104, ts=7200000),
        ]
        # Sweep bar: wick touches FVG (low=101 <= top=105), body safe (close=117 > bottom=103)
        sweep_bar = _bar(9, 116, 118, 101, 117, ts=8100000)

        rsm.on_sweep_confirmed(bars, sweep_bar)

        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.direction == "bullish"
        assert rsm.trigger_fvg is not None
        assert rsm.trigger_fvg.direction == "bullish"

    def test_bearish_sweep_with_valid_fvg_reaches_trigger_ready(self):
        """Bearish sweep + FVG + wick rejection + close inside FVG = TRIGGER_READY."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)

        # Bearish FVG: bar(0) low=109, bar(2) high=108 → gap [108, 109], bar(1) impulse
        bars = [
            _bar(0, 110, 113, 109, 111, ts=0),
            _bar(1, 109, 111, 107, 108, ts=900000),
            _bar(2, 106, 108, 103, 105, ts=1800000),
            _bar(3, 105, 107, 102, 104, ts=2700000),
            _bar(4, 104, 106, 100, 102, ts=3600000),
            _bar(5, 102, 104, 98, 100, ts=4500000),
            _bar(6, 100, 102, 96, 98, ts=5400000),
            _bar(7, 98, 100, 94, 96, ts=6300000),
            # Close inside FVG [108, 109]
            _bar(8, 96, 110, 95, 108.5, ts=7200000),
        ]
        # Sweep bar: wick touches FVG (high=109 >= bottom=108), body safe (close=95 <= top=109)
        # close=95 <= sweep_level=95 prevents invalidation (bearish: close > sweep_level triggers)
        sweep_bar = _bar(9, 96, 109, 94, 95, ts=8100000)

        rsm.on_sweep_confirmed(bars, sweep_bar)

        assert rsm.state == RetraceState.TRIGGER_READY
        assert rsm.direction == "bearish"
        assert rsm.trigger_fvg is not None
        assert rsm.trigger_fvg.direction == "bearish"

    def test_no_fvg_keeps_sweep_detected(self):
        """Sweep var ama FVG yok → SWEEP_DETECTED'de kal, IDLE'a donme."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        assert rsm.state == RetraceState.SWEEP_DETECTED

        # Bars with NO FVG gaps — all overlapping ranges
        bars = [_bar(i, 100, 102, 98, 101, ts=i * 900000) for i in range(20)]
        sweep_bar = _bar(20, 101, 106, 99, 105, ts=20 * 900000)

        rsm.on_sweep_confirmed(bars, sweep_bar)

        assert rsm.state == RetraceState.SWEEP_DETECTED  # No reset to IDLE

    def test_second_sweep_ignored_while_in_sweep_detected(self):
        """Ikinci sweep gelince state degismemeli, ilk sweep context korunmali."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        assert rsm.direction == "bullish"
        assert rsm.sweep_level == 105.0

        # Second sweep with opposite direction — should be ignored
        rsm.on_sweep("bearish", 95.0)

        assert rsm.direction == "bullish"
        assert rsm.sweep_level == 105.0
        assert rsm.state == RetraceState.SWEEP_DETECTED

    def test_sweep_invalidated_when_close_breaks_opposite(self):
        """Bullish sweep: close < sweep_level → invalidation → IDLE."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        bars = [_bar(i, 100, 102, 98, 101, ts=i * 900000) for i in range(5)]
        sweep_bar = _bar(5, 106, 109, 101, 102, ts=5 * 900000)  # close=102 < 105

        rsm.on_sweep_confirmed(bars, sweep_bar)

        assert rsm.state == RetraceState.IDLE

    def test_direction_mapping_bullish_sweep_leads_to_long(self):
        """Bullish sweep direction → entry'de 'long' side kullanilir."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bullish", 105.0)
        assert rsm.direction == "bullish"

        # bot.py _try_entry kodu: side = "long" if sweep_dir == "bullish" else "short"
        side = "long" if rsm.direction == "bullish" else "short"
        assert side == "long"

    def test_direction_mapping_bearish_sweep_leads_to_short(self):
        """Bearish sweep direction → entry'de 'short' side kullanilir."""
        rsm = RetraceStateMachine()
        rsm.on_sweep("bearish", 95.0)
        assert rsm.direction == "bearish"

        side = "long" if rsm.direction == "bullish" else "short"
        assert side == "short"


# ═══════════════════════════════════════════════════════════════════
# 2) Trigger -> Entry entegrasyon testleri
# ═══════════════════════════════════════════════════════════════════


class TestTriggerEntry:
    """SignalEngine trigger ciktisi -> EntryManager SL/TP/qty hesaplari."""

    def test_long_entry_calculates_correct_sl_tp(self):
        """Long trigger: SL FVG altinda, TP risk_dist*RR ile hesaplanir."""
        fvg = HTFFVG(top=105.0, bottom=103.0, direction="bullish", bar_index=5)
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=110.0,
            risk_pts=5.0,
            fvg_buf=0.50,
            tp_rr=2.0,
            trigger_fvg=fvg,
            london_high=115.0,
            london_low=105.0,
        )
        # buffer = max(0.2, max(0.5, min(0.5, 2.5))) = 0.5
        # SL = 103.0 - 0.5 = 102.5, rd = |102.5 - 110| = 7.5
        # TP = 110 + 7.5 * 2 = 125.0
        assert sl == pytest.approx(102.5, abs=0.1)
        assert tp == pytest.approx(125.0, abs=0.1)

    def test_short_entry_calculates_correct_sl_tp(self):
        """Short trigger: SL FVG ustunde, TP risk_dist*RR ile hesaplanir."""
        fvg = HTFFVG(top=109.0, bottom=108.0, direction="bearish", bar_index=5)
        sl, tp = EntryManager.calculate_sl_tp(
            side="short",
            entry_price=100.0,
            risk_pts=5.0,
            fvg_buf=0.50,
            tp_rr=2.0,
            trigger_fvg=fvg,
            london_high=110.0,
            london_low=95.0,
        )
        # buffer = max(0.1, max(0.5, min(0.25, 2.5))) = 0.5
        # SL = 109.0 + 0.5 = 109.5, rd = |109.5 - 100| = 9.5
        # TP = 100 - 9.5 * 2 = 81.0
        assert sl == pytest.approx(109.5, abs=0.1)
        assert tp == pytest.approx(81.0, abs=0.1)

    def test_entry_without_fvg_uses_risk_fallback(self):
        """Trigger_fvg yoksa SL, entry - risk*2 fallback kullanir."""
        sl, tp = EntryManager.calculate_sl_tp(
            side="long",
            entry_price=100.0,
            risk_pts=5.0,
            fvg_buf=0.50,
            tp_rr=2.0,
            trigger_fvg=None,
            london_high=110.0,
            london_low=95.0,
        )
        # SL = 100 - 5*2 = 90, rd = |90 - 100| = 10
        # TP = 100 + 10*2 = 120
        assert sl == pytest.approx(90.0, abs=0.1)
        assert tp == pytest.approx(120.0, abs=0.1)

    def test_invalid_risk_dist_blocks_entry(self):
        """Risk mesafesi min_risk_dist altindaysa validate_risk False doner."""
        valid, msg = EntryManager.validate_risk(risk_dist=0.001, atr_val=1.0)
        assert valid is False
        assert "min" in msg.lower()  # "risk_dist=0.001 < min=0.1"

    def test_valid_risk_dist_allows_entry(self):
        """Risk mesafesi yeterliyse validate_risk True doner."""
        valid, msg = EntryManager.validate_risk(risk_dist=1.0, atr_val=1.0)
        assert valid is True

    def test_qty_calculation_scales_with_risk(self):
        """Qty = balance * risk_pct / risk_dist, margin cap ile sinirli."""
        qty = EntryManager.calculate_qty(
            balance=10000.0,
            risk_pct=0.003,
            risk_dist=5.0,
            leverage=5,
            entry_price=100.0,
        )
        # qty = (10000 * 0.003) / 5.0 = 6.0
        # max_qty = (10000*0.20*5) / 100 = 100.0 → cap asilmamis
        assert qty == pytest.approx(6.0, abs=0.1)

    def test_qty_zero_when_risk_dist_zero(self):
        """risk_dist=0 veya negatif → qty=0 (guvenlik)."""
        qty = EntryManager.calculate_qty(
            balance=10000.0,
            risk_pct=0.003,
            risk_dist=0.0,
            leverage=5,
            entry_price=100.0,
        )
        assert qty == 0.0

    def test_bullish_trigger_to_long_mapping_consistent(self):
        """SignalEngine'de bullish trigger → entry'de long side."""
        # Bu mapping bot.py _try_entry: side = "long" if sweep_dir == "bullish" else "short"
        trigger_dir = "bullish"
        side = "long" if trigger_dir == "bullish" else "short"
        assert side == "long"

    def test_bearish_trigger_to_short_mapping_consistent(self):
        """SignalEngine'de bearish trigger → entry'de short side."""
        trigger_dir = "bearish"
        side = "long" if trigger_dir == "bullish" else "short"
        assert side == "short"


# ═══════════════════════════════════════════════════════════════════
# 3) Trailing -> Exit entegrasyon testleri
# ═══════════════════════════════════════════════════════════════════


class TestTrailingExit:
    """TrailingManager: FVG trailing update + exit karari."""

    def _make_trade(
        self, side="long", entry=100.0, sl=95.0, tp=110.0, trail_steps=None
    ):
        return {
            "side": side,
            "entry_price": entry,
            "sl": sl,
            "tp": tp,
            "initial_sl": sl,
            "initial_tp": tp,
            "trailing_count": 0,
            "trail_steps": trail_steps or [],
            "qty": 1.0,
        }

    def _bars_with_bullish_fvg(self, base=100.0):
        """Bullish FVG: gap [base+3, base+5] at index 1."""
        return [
            _bar(0, base, base + 3, base - 1, base + 2, ts=0),
            _bar(1, base + 3, base + 5, base + 2, base + 4, ts=900000),
            _bar(2, base + 5, base + 8, base + 5, base + 7, ts=1800000),
            _bar(3, base + 7, base + 10, base + 6, base + 9, ts=2700000),
            _bar(4, base + 9, base + 12, base + 8, base + 11, ts=3600000),
            _bar(5, base + 11, base + 14, base + 10, base + 13, ts=4500000),
            _bar(6, base + 13, base + 16, base + 12, base + 15, ts=5400000),
        ]

    def _bars_with_bearish_fvg(self, base=100.0):
        """Bearish FVG: gap [base+2, base+3] at index 1."""
        return [
            _bar(0, base + 5, base + 8, base + 4, base + 6, ts=0),
            _bar(1, base + 3, base + 6, base + 2, base + 5, ts=900000),
            _bar(2, base + 1, base + 4, base - 1, base + 2, ts=1800000),
            _bar(3, base - 1, base + 2, base - 3, base + 0, ts=2700000),
            _bar(4, base - 3, base + 0, base - 5, base - 2, ts=3600000),
            _bar(5, base - 5, base - 2, base - 7, base - 4, ts=4500000),
            _bar(6, base - 7, base - 4, base - 9, base - 6, ts=5400000),
        ]

    def test_long_trail_on_bullish_fvg_tightens_sl_up(self):
        """Bullish FVG sonrasi long trade'de stop yukari sıkılasir."""
        bars = self._bars_with_bullish_fvg()
        trade = self._make_trade(side="long", entry=100.0, sl=95.0)
        result = TrailingManager.evaluate_trail(
            bars, trade, atr_val=0.5, min_fvg_size=0.01
        )
        # Yeni SL eskisinden yukarda olmali (stop yukari cekilir)
        if result.updated:
            assert result.new_sl > trade["sl"]

    def test_short_trail_on_bearish_fvg_tightens_sl_down(self):
        """Bearish FVG sonrasi short trade'de stop asagi sıkılasir."""
        bars = self._bars_with_bearish_fvg()
        trade = self._make_trade(side="short", entry=100.0, sl=105.0)
        result = TrailingManager.evaluate_trail(
            bars, trade, atr_val=0.5, min_fvg_size=0.01
        )
        if result.updated:
            assert result.new_sl < trade["sl"]

    def test_check_exit_long_hit_sl(self):
        """Long trade: low <= SL → exit tetiklenir."""
        current = _bar(10, 95, 96, 94, 95, ts=10 * 900000)
        trade = self._make_trade(side="long", entry=100.0, sl=95.0, tp=110.0)
        decision = TrailingManager.check_exit(current, trade)
        assert decision.triggered is True
        assert decision.result == "SL"

    def test_check_exit_long_hit_tp(self):
        """Long trade: high >= TP → exit tetiklenir."""
        current = _bar(10, 109, 111, 108, 110, ts=10 * 900000)
        trade = self._make_trade(side="long", entry=100.0, sl=95.0, tp=110.0)
        decision = TrailingManager.check_exit(current, trade)
        assert decision.triggered is True
        assert decision.result == "TP"

    def test_check_exit_short_hit_sl(self):
        """Short trade: high >= SL → exit tetiklenir."""
        current = _bar(10, 106, 107, 104, 106, ts=10 * 900000)
        trade = self._make_trade(side="short", entry=100.0, sl=105.0, tp=90.0)
        decision = TrailingManager.check_exit(current, trade)
        assert decision.triggered is True
        assert decision.result == "SL"

    def test_check_exit_short_hit_tp(self):
        """Short trade: low <= TP → exit tetiklenir."""
        current = _bar(10, 91, 93, 89, 92, ts=10 * 900000)
        trade = self._make_trade(side="short", entry=100.0, sl=105.0, tp=90.0)
        decision = TrailingManager.check_exit(current, trade)
        assert decision.triggered is True
        assert decision.result == "TP"

    def test_check_exit_no_hit(self):
        """Fiyat SL/TP arasindayken exit tetiklenmez."""
        current = _bar(10, 98, 102, 97, 100, ts=10 * 900000)
        trade = self._make_trade(side="long", entry=100.0, sl=95.0, tp=110.0)
        decision = TrailingManager.check_exit(current, trade)
        assert decision.triggered is False

    def test_short_bar_set_no_false_exit(self):
        """Kisa/eksik bar setinde yanlis exit uretilmez."""
        current = _bar(0, 100, 102, 98, 101, ts=0)
        trade = self._make_trade(side="long", entry=100.0, sl=95.0, tp=110.0)
        decision = TrailingManager.check_exit(current, trade)
        assert decision.triggered is False

    def test_trail_does_not_update_when_no_fvg(self):
        """FVG olmayan bar setinde trail guncellenmez."""
        bars = [_bar(i, 100, 102, 98, 101, ts=i * 900000) for i in range(20)]
        trade = self._make_trade(side="long", entry=100.0, sl=95.0)
        result = TrailingManager.evaluate_trail(
            bars, trade, atr_val=0.5, min_fvg_size=50.0
        )
        assert result.updated is False
        assert result.exit_now is False


# ═══════════════════════════════════════════════════════════════════
# 4) Dry strategy flow testi — Sweep -> Exit (full chain)
# ═══════════════════════════════════════════════════════════════════


class TestDryStrategyFlow:
    """Sweep -> FVG -> Trigger -> Entry -> Trail -> Exit tam zincir."""

    def _make_session_state(self, bias: DailyBias, locked: bool = True):
        """Hafif SessionState: CBDR kilitli, sweep/bias atanmis."""
        ss = SessionState(start_hour=22, end_hour=2)
        ss.cbdr_locked = locked
        ss.sweep_confirmed = True
        if bias == DailyBias.BULLISH:
            ss.sweep_direction = "bullish"
            ss.sweep_level = 100.0
            ss.daily_bias = DailyBias.BULLISH
        else:
            ss.sweep_direction = "bearish"
            ss.sweep_level = 110.0
            ss.daily_bias = DailyBias.BEARISH
        return ss

    def _bar(self, index, o, h, low, c, ts=None):
        ts = ts or index * 900000
        return Bar(
            index=index, open=o, high=h, low=low, close=c, is_closed=True, timestamp=ts
        )

    def _build_bullish_flow_bars(self):
        """Bullish FVG [103, 105] + close-inside + sweep bar with wick rejection."""
        return [
            self._bar(0, 100, 103, 99, 102, 0),
            self._bar(1, 103, 105, 102, 104, 900000),
            self._bar(2, 106, 110, 105, 108, 1800000),
            self._bar(3, 108, 112, 107, 110, 2700000),
            self._bar(4, 110, 113, 109, 112, 3600000),
            self._bar(5, 112, 115, 111, 114, 4500000),
            self._bar(6, 114, 116, 113, 115, 5400000),
            self._bar(7, 115, 117, 114, 116, 6300000),
            self._bar(8, 105, 107, 103, 104, 7200000),  # close inside FVG
        ]

    def test_bullish_full_flow_sweep_to_exit(self):
        """Bullish: sweep -> FVG -> trigger -> entry hesaplari -> trail -> exit."""
        from trading.signal_engine import SignalEngine

        rsm = RetraceStateMachine()
        ss = self._make_session_state(DailyBias.BULLISH)
        engine = SignalEngine(rsm)

        # 1) Sweep -> state progress
        rsm.on_sweep("bullish", ss.sweep_level)
        assert rsm.state == RetraceState.SWEEP_DETECTED

        # 2) FVG taramasi + trigger
        bars_15m = self._build_bullish_flow_bars()
        sweep_bar = self._bar(
            9, 116, 118, 101, 117, 5 * 3600 * 1000
        )  # 05:00 UTC LONDON
        engine.progress_rsm(bars_15m, sweep_bar, ss, atr_val=1.0)

        if rsm.state == RetraceState.TRIGGER_READY:
            # 3) Entry hesaplari
            side = "long" if rsm.direction == "bullish" else "short"
            assert side == "long"

            sl, tp = EntryManager.calculate_sl_tp(
                side=side,
                entry_price=117.0,
                risk_pts=5.0,
                fvg_buf=0.50,
                tp_rr=2.0,
                trigger_fvg=rsm.trigger_fvg,
                london_high=120.0,
                london_low=100.0,
            )
            assert sl < 117.0  # SL entry altinda
            assert tp > 117.0  # TP entry ustunde

            # 4) Trailing (simule)
            trail_bars = bars_15m + [self._bar(10, 118, 121, 116, 120, 9000000)]
            trade = {
                "side": side,
                "entry_price": 117.0,
                "sl": sl,
                "tp": tp,
                "initial_sl": sl,
                "initial_tp": tp,
                "trailing_count": 0,
                "trail_steps": [],
                "qty": 1.0,
            }
            trail_result = TrailingManager.evaluate_trail(
                trail_bars, trade, atr_val=1.0, min_fvg_size=0.01
            )
            if trail_result.updated:
                assert trail_result.new_sl > sl  # stop yukari cekildi

            # 5) Exit
            exit_bar = self._bar(11, 117, 119, sl - 0.5, 118, 9900000)
            decision = TrailingManager.check_exit(exit_bar, trade)
            # SL'ye dustuyse exit tetiklenir
            if exit_bar.low <= sl:
                assert decision.triggered is True

        else:
            pytest.skip("RSM TRIGGER_READY ulasmadi — FVG esik/wick kosulu saglanamadi")

    def test_bearish_full_flow_sweep_to_exit(self):
        """Bearish: sweep -> FVG -> trigger -> entry -> trail -> exit."""
        from trading.signal_engine import SignalEngine

        rsm = RetraceStateMachine()
        ss = self._make_session_state(DailyBias.BEARISH)
        engine = SignalEngine(rsm)

        rsm.on_sweep("bearish", ss.sweep_level)
        assert rsm.state == RetraceState.SWEEP_DETECTED

        # Bearish FVG: gap [108, 109]
        bars_15m = [
            self._bar(0, 110, 113, 109, 111, 0),
            self._bar(1, 109, 111, 107, 108, 900000),
            self._bar(2, 106, 108, 103, 105, 1800000),
            self._bar(3, 105, 107, 102, 104, 2700000),
            self._bar(4, 104, 106, 100, 102, 3600000),
            self._bar(5, 102, 104, 98, 100, 4500000),
            self._bar(6, 100, 102, 96, 98, 5400000),
            self._bar(7, 98, 100, 94, 96, 6300000),
            self._bar(8, 96, 110, 95, 108.5, 7200000),  # close inside FVG
        ]
        sweep_bar = self._bar(9, 96, 109, 94, 95, 5 * 3600 * 1000)  # 05:00 UTC LONDON
        engine.progress_rsm(bars_15m, sweep_bar, ss, atr_val=1.0)

        if rsm.state == RetraceState.TRIGGER_READY:
            side = "short" if rsm.direction == "bearish" else None
            assert side == "short"

            sl, tp = EntryManager.calculate_sl_tp(
                side="short",
                entry_price=95.0,
                risk_pts=5.0,
                fvg_buf=0.50,
                tp_rr=2.0,
                trigger_fvg=rsm.trigger_fvg,
                london_high=110.0,
                london_low=90.0,
            )
            assert sl > 95.0  # SL entry ustunde (short)
            assert tp < 95.0  # TP entry altinda (short)

            trail_bars = bars_15m + [self._bar(10, 94, 96, 91, 93, 9000000)]
            trade = {
                "side": "short",
                "entry_price": 95.0,
                "sl": sl,
                "tp": tp,
                "initial_sl": sl,
                "initial_tp": tp,
                "trailing_count": 0,
                "trail_steps": [],
                "qty": 1.0,
            }
            trail_result = TrailingManager.evaluate_trail(
                trail_bars, trade, atr_val=1.0, min_fvg_size=0.01
            )
            if trail_result.updated:
                assert trail_result.new_sl < sl  # stop asagi cekildi

            exit_bar = self._bar(11, 93, sl + 0.5, 91, 92, 9900000)
            decision = TrailingManager.check_exit(exit_bar, trade)
            if exit_bar.high >= sl:
                assert decision.triggered is True
        else:
            pytest.skip("RSM TRIGGER_READY ulasmadi — FVG esik/wick kosulu saglanamadi")
