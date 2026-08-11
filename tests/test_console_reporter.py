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
    def __init__(self, state_name, trigger_fvg=None):
        self.state_name = state_name
        self.trigger_fvg = trigger_fvg


class TestSweepStatus:
    def test_bias_determined_prints_tamamlandi_not_bekleniyor(self, capsys):
        """Bias belirlenmis (sweep tüketilmis) — BEKLENIYOR yanlis, TAMAMLANDI olmali."""
        r = ConsoleReporter()
        ss = _SS(sweep_confirmed=False, daily_bias=DailyBias.BULLISH)
        r.display_sweep_status("BTCUSDT", ss, 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: TAMAMLANDI" in out
        assert "LONG" in out
        assert "BEKLENIYOR" not in out

    def test_neutral_bias_prints_bekleniyor(self, capsys):
        """Bias henuz yok — gercekten sweep bekleniyor."""
        r = ConsoleReporter()
        r.display_sweep_status("BTCUSDT", _SS(), 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: BEKLENIYOR" in out

    def test_sweep_confirmed_prints_detected(self, capsys):
        r = ConsoleReporter()
        ss = _SS(sweep_confirmed=True, sweep_direction="bullish", sweep_level=0.0795)
        r.display_sweep_status("BTCUSDT", ss, 21, 30)
        out = capsys.readouterr().out
        assert "SWEEP: DETECTED" in out


class TestFvgStatus:
    def test_idle_prints_no_fvg_line(self, capsys):
        """IDLE (sweep yok) — FVG_SCAN logu basilmaz."""
        r = ConsoleReporter()
        r.display_fvg_status("BTCUSDT", _RSM("IDLE"), 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "FVG_SCAN" not in out

    def test_sweep_detected_prints_araniyor(self, capsys):
        r = ConsoleReporter()
        r.display_fvg_status("BTCUSDT", _RSM("SWEEP_DETECTED"), 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "FVG ARANIYOR" in out

    def test_bias_locked_prints_araniyor(self, capsys):
        r = ConsoleReporter()
        r.display_fvg_status("BTCUSDT", _RSM("BIAS_LOCKED"), 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "FVG ARANIYOR" in out

    def test_trigger_ready_prints_hazir(self, capsys):
        r = ConsoleReporter()
        rsm = _RSM("TRIGGER_READY", _FVG(0.0795, 0.0800))
        r.display_fvg_status("BTCUSDT", rsm, 0.000330, 0.080)
        out = capsys.readouterr().out
        assert "HAZIR" in out
