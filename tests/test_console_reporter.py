from session import DailyBias
from trading.console_reporter import ConsoleReporter


class _SS:
    """Minimal SessionState stub — reporter'un dokundugu alanlar."""

    def __init__(self, **kw):
        self.cbdr_body_high = 0.0814
        self.cbdr_body_low = 0.0795
        self.range_type = "CBDR"
        self.sweep_confirmed = False
        self.sweep_direction = None
        self.sweep_level = None
        self.daily_bias = DailyBias.NEUTRAL
        for k, v in kw.items():
            setattr(self, k, v)


class _FVG:
    def __init__(self, bottom, top):
        self.bottom = bottom
        self.top = top


class _RSM:
    def __init__(self, state_name, trigger_fvg=None, direction=None, sweep_level=None):
        self.state_name = state_name
        self.trigger_fvg = trigger_fvg
        self.direction = direction
        self.sweep_level = sweep_level


class TestSweepStatus:
    def test_bias_determined_prints_tamamlandi_not_bekleniyor(self, capsys):
        """Bias belirlenmis (sweep tüketilmis, RSM IDLE) — BEKLENIYOR degil."""
        r = ConsoleReporter()
        ss = _SS(sweep_confirmed=False, daily_bias=DailyBias.BULLISH)
        r.display_sweep_status("BTCUSDT", ss, _RSM("IDLE"), 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: TAMAMLANDI" in out
        assert "LONG" in out
        assert "BEKLENIYOR" not in out

    def test_neutral_bias_prints_bekleniyor(self, capsys):
        """Bias henuz yok — gercekten sweep bekleniyor."""
        r = ConsoleReporter()
        r.display_sweep_status("BTCUSDT", _SS(), _RSM("IDLE"), 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: BEKLENIYOR" in out

    def test_sweep_confirmed_latched_prints_detected(self, capsys):
        """RSM IDLE ama latch'li sweep bekliyor (on_sweep dedup) -> DETECTED."""
        r = ConsoleReporter()
        ss = _SS(sweep_confirmed=True, sweep_direction="bullish", sweep_level=0.0795)
        r.display_sweep_status("BTCUSDT", ss, _RSM("IDLE"), 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: DETECTED" in out

    def test_rsm_sweep_detected_prints_detected(self, capsys):
        """RSM sweep isliyor -> DETECTED (latch'e bakmaz)."""
        r = ConsoleReporter()
        rsm = _RSM("SWEEP_DETECTED", direction="bearish", sweep_level=0.0800)
        r.display_sweep_status("BTCUSDT", _SS(sweep_confirmed=False), rsm, 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: DETECTED" in out
        assert "BEARISH" in out

    def test_rsm_trigger_ready_prints_detected(self, capsys):
        """RSM trigger hazir -> DETECTED."""
        r = ConsoleReporter()
        rsm = _RSM("TRIGGER_READY", direction="bullish", sweep_level=0.0795)
        r.display_sweep_status("BTCUSDT", _SS(sweep_confirmed=False), rsm, 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: DETECTED" in out
        assert "BULLISH" in out

    def test_rsm_bias_locked_prints_tamamlandi(self, capsys):
        """RSM bias kilitli -> TAMAMLANDI + FVG bekleniyor."""
        r = ConsoleReporter()
        rsm = _RSM("BIAS_LOCKED", direction="bullish")
        r.display_sweep_status("BTCUSDT", _SS(sweep_confirmed=False), rsm, 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: TAMAMLANDI" in out
        assert "LONG" in out
        assert "FVG bekleniyor" in out


class TestFvgStatus:
    def test_idle_no_bias_prints_no_fvg_line(self, capsys):
        """IDLE + NEUTRAL bias (sweep yok) — FVG_SCAN logu basilmaz."""
        r = ConsoleReporter()
        r.display_fvg_status("BTCUSDT", _RSM("IDLE"), _SS(), 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "FVG_SCAN" not in out

    def test_idle_with_bias_prints_araniyor(self, capsys):
        """IDLE + daily_bias belirlenmis (sweep tamamlandi) — FVG ARANIYOR."""
        r = ConsoleReporter()
        ss = _SS(sweep_confirmed=False, daily_bias=DailyBias.BULLISH)
        r.display_fvg_status("BTCUSDT", _RSM("IDLE"), ss, 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "FVG ARANIYOR" in out

    def test_sweep_detected_prints_araniyor(self, capsys):
        r = ConsoleReporter()
        r.display_fvg_status("BTCUSDT", _RSM("SWEEP_DETECTED"), _SS(), 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "FVG ARANIYOR" in out

    def test_bias_locked_prints_araniyor(self, capsys):
        r = ConsoleReporter()
        r.display_fvg_status("BTCUSDT", _RSM("BIAS_LOCKED"), _SS(), 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "FVG ARANIYOR" in out

    def test_active_position_prints_full_precision(self, capsys):
        """POZISYON AKTIF satirinda entry/sl/tp 2 ondaliga yuvarlanmamali."""
        r = ConsoleReporter()
        trade = {
            "side": "short",
            "entry_price": 0.0854,
            "sl": 0.0858,
            "tp": 0.0846,
            "trailing_count": 0,
            "fvg_top": 0.0854,
            "fvg_bottom": 0.0853,
            "fvg_direction": "bearish",
        }
        r.display_active_position("ENAUSDT", trade, 19, 45)
        out = capsys.readouterr().out
        assert "SHORT @ 0.0854" in out
        assert "SL: 0.0858" in out
        assert "TP: 0.0846" in out
        assert "FVG: bearish 0.0854-0.0853" in out
        assert "0.09" not in out

    def test_trigger_ready_prints_hazir(self, capsys):
        r = ConsoleReporter()
        rsm = _RSM("TRIGGER_READY", _FVG(0.0795, 0.0800))
        r.display_fvg_status("BTCUSDT", rsm, _SS(), 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "HAZIR" in out

    def test_trigger_ready_prints_full_precision_fvg(self, capsys):
        """Dusuk fiyatli sembollerde FVG 2 ondaliga yuvarlanmamali (0.0853 -> 0.09)."""
        r = ConsoleReporter()
        rsm = _RSM("TRIGGER_READY", _FVG(0.0853, 0.0854))
        r.display_fvg_status("ENAUSDT", rsm, _SS(), 0.000006, 0.0854)
        out = capsys.readouterr().out
        assert "FVG:[0.0853 - 0.0854]" in out
        assert "FVG:[0.0853-0.0854]" in out
        assert "CLOSE: 0.0854" in out
        assert "[0.09" not in out
