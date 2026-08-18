"""
Parity CI regression testi — canli/backtest giris state-machine akisinin
ayni 15m input barinda ayni karari uretmesini zorunlu kilan sabit benchmark.

Bu test execution riskini olcmez (emir gecikmesi, slippage, likidite, exchange
reddi ayri execution_simulation katmanindadir). Yalnizca giris/state parity'sini
CI'da garanti altina alir.

Sozlesme:
  - Ayni 87.600 bar (15m), ayni input, ayni ortak session.py / retrace_state.py / fvg.py
  - Her sembol icin core-diff == 0
  - TRIGGER ve sweep-lock sayilari backtest == live
  - Per-bar state trace esitligi
  - Benchmark fixture checksum'i ile sabitlenir; input veri veya ortak modul
    kurallari degisirse test bilincli olarak guncellenmelidir.

Calistirma:
  python -m pytest tests/parity -q --maxfail=1
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BT_SRC = str(REPO_ROOT / "backtest-sniper" / "src")
SNIPER_SRC = str(REPO_ROOT / "sniper" / "src")
for _p in (BT_SRC, SNIPER_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analyzer_v5 import load_data, resample_15m  # noqa: E402
import config as cfg  # noqa: E402
from retrace_state import RetraceState, RetraceStateMachine  # noqa: E402
from session import DailyBias, SessionState  # noqa: E402
from session_router import get_session_hours  # noqa: E402
from indicators import calculate_true_range, update_atr  # noqa: E402
from trading.signal_engine import SignalEngine  # noqa: E402

DATA_DIR = Path(BT_SRC) / "data" / "daily"

# ── Sabit benchmark sozlesmesi ──────────────────────────────────────────────
# Kaynak: 2026-07-31 parity calismasi. Input veri veya kurallar degisirse bu
# tablo bilincli olarak guncellenmelidir — sessizce kabul edilmemelidir.
BENCHMARK_CONTRACT: dict[str, dict] = {
    # 2026-08-18 guard-fix calismasinda tazelendi: 2026-07-31 benchmark'i
    # sweep-tuketim (SEIUSDT duzeltmesi) ve signal_engine refactoru SONRASI
    # stale olmustu. Input veri (sha256) AYNIDIR; state/trigger sayilari
    # guncel akisa gore donduruldu (sweep_lock artik tuketimden dolayi 0).
    "SOLUSDT": {
        "session": (19, 1),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (505, 505),
        "sweep_lock": (0, 0),
        "sha256": "19bac676cce10e9985c3db03d4b0f40c921b5faff455a24b87e95f3793cd7238",
    },
    "BNBUSDT": {
        "session": (19, 1),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (512, 512),
        "sweep_lock": (0, 0),
        "sha256": "7ecc021ab335f3bdf07e24812066897e0a217e72053357b014d98470a6ecd2ec",
    },
    "AVAXUSDT": {
        "session": (22, 2),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (570, 570),
        "sweep_lock": (0, 0),
        "sha256": "ca6bb170fc09033d625b40e71807502b60efc2a8a268278ac7339ef4dd1b69c0",
    },
    "LINKUSDT": {
        "session": (1, 5),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (591, 591),
        "sweep_lock": (0, 0),
        "sha256": "266800e8198ad59bd343a2cdc05573fa4b6decb8b5d7cd8b633f72f405605571",
    },
    "XRPUSDT": {
        "session": (22, 2),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (559, 559),
        "sweep_lock": (0, 0),
        "sha256": "0a6029cf1e79777b9b5a2dfeea9c77610996eba93df42e945ca58fa2ff5e9ec8",
    },
    "ATOMUSDT": {
        "session": (19, 1),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (539, 539),
        "sweep_lock": (0, 0),
        "sha256": "323187d0d1dbf56ef487c09b873e72cb69bdbab1853aba32eabfddc659d68217",
    },
    "ADAUSDT": {
        "session": (22, 2),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (570, 570),
        "sweep_lock": (0, 0),
        "sha256": "bef33eb05aacbff22b31dc68c5f1a3de5eb96326e44ec75f0b40ac517405cf90",
    },
    "APTUSDT": {
        "session": (19, 1),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (507, 507),
        "sweep_lock": (0, 0),
        "sha256": "5a374480f985ea2ed9790f64e767e761f8f0c2a1837376571ee28038554427ce",
    },
    "DOTUSDT": {
        "session": (19, 1),
        "bars": 87600,
        "core_diff": 0,
        "trigger": (532, 532),
        "sweep_lock": (0, 0),
        "sha256": "4108eb14f5572b4cd617eabbb80fd4fbeada9141e4f03fed31559515d2b63c80",
    },
}

# ── Per-bar trace sozlesmesi ────────────────────────────────────────────────
# Her bar icin karsilastirilan alanlar. Sira ve icerik degistirilemez.
TRACE_FIELDS = [
    "bar_index",
    "cbdr_locked",
    "sweep_confirmed",
    "sweep_direction",
    "rsm_state",
    "rsm_direction",
    "trigger_fvg_bar",
    "trigger_decision",
    "entry_gate_decision",
]


@dataclass
class ParityResult:
    symbol: str
    session: tuple[int, int]
    bars: int
    core_diff: int
    decision_only_diff: int
    trigger_backtest: int
    trigger_live: int
    sweep_lock_backtest: int
    sweep_lock_live: int
    ifvg_backtest: int = 0
    ifvg_live: int = 0
    first_divergence: dict | None = None
    trace_mismatches: list = field(default_factory=list)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_backtest(b15, symbol, sh, eh, total_bars):
    """analyzer_v5.py:258-305 cagiri siracisi (birebir) + process_sweep IFVG blogu."""
    spans_midnight = sh > eh
    ss = SessionState(start_hour=sh, end_hour=eh)
    rsm = RetraceStateMachine(max_wick_ratio=cfg.FVG_WICK_RATIO_MAX)
    atr_val = 0.0
    prev_close = b15[499].close
    log = []
    ifvg_triggers = 0
    for sb in range(500, total_bars):
        cur = b15[sb]
        tr = calculate_true_range(cur, prev_close)
        atr_val = update_atr(atr_val if atr_val > 0 else None, tr)
        prev_close = cur.close
        atr = atr_val
        edt = datetime.fromtimestamp(cur.timestamp / 1000, tz=timezone.utc)
        ss.update(edt, cur.open, cur.high, cur.low, cur.close, atr)
        # sweep_sync.process_sweep parity (IFVG kaynak etiketi her bar'da sifirla)
        rsm._last_trigger_source = "NORMAL"
        if ss.sweep_confirmed and rsm.state_name == "IDLE":
            rsm.on_sweep(
                direction=ss.sweep_direction or "bullish",
                level=ss.sweep_level or 0.0,
                bar_index=None,
            )
            ss.sweep_confirmed = False
        if rsm.state_name == "SWEEP_DETECTED":
            rsm.on_sweep_confirmed(b15[sb - 500 : sb + 1], cur, atr, symbol)
            if rsm.state_name == "IDLE":
                ss.sweep_confirmed = False
        # IFVG ikincil yol (sweep_sync.process_sweep parity)
        if rsm.state != RetraceState.TRIGGER_READY:
            ifvg_hit = rsm.check_ifvg_retest(cur)
            if ifvg_hit is not None:
                rsm._pre_ifvg_direction = rsm.direction
                rsm.state = RetraceState.TRIGGER_READY
                rsm.direction = ifvg_hit.direction
                rsm.trigger_fvg = ifvg_hit
                rsm._last_trigger_source = "IFVG"
        decision = "WAIT"
        entry_gate = "DENY"
        if rsm.can_trigger():
            sd = rsm.direction
            db = ss.daily_bias
            # IFVG kaynakli trigger'lar bias filtresinden MUAF (analyzer_v5
            # parity, devir eki karari). NORMAL icin eski davranis korunur.
            bias_reject = False
            if getattr(rsm, "_last_trigger_source", None) != "IFVG":
                bias_reject = (
                    (sd == "bullish" and db == DailyBias.BEARISH)
                    or (sd == "bearish" and db == DailyBias.BULLISH)
                    or db == DailyBias.NEUTRAL
                )
            if bias_reject:
                decision = "SKIP_BIAS"
                rsm.reset()
            else:
                h = edt.hour
                if (h >= sh or h < eh) if spans_midnight else (sh <= h < eh):
                    decision = "SKIP_SESSION"
                    rsm.reset()
                else:
                    decision = "TRIGGER"
                    entry_gate = "ALLOW"
                    if getattr(rsm, "_last_trigger_source", None) == "IFVG":
                        ifvg_triggers += 1
                    rsm.reset()  # analyzer_v5.py:440 ayni — entry sonrasi reset
        log.append(
            {
                "bar_index": sb,
                "cbdr_locked": ss.cbdr_locked,
                "sweep_confirmed": ss.sweep_confirmed,
                "sweep_direction": ss.sweep_direction,
                "rsm_state": rsm.state_name,
                "rsm_direction": rsm.direction,
                "trigger_fvg_bar": rsm.trigger_fvg.bar_index
                if rsm.trigger_fvg
                else None,
                "trigger_decision": decision,
                "entry_gate_decision": entry_gate,
            }
        )
    return log, ifvg_triggers


def _run_live(b15, symbol, sh, eh, total_bars):
    """bot.py on15m + SignalEngine (birebir)."""
    ss = SessionState(start_hour=sh, end_hour=eh)
    rsm = RetraceStateMachine(max_wick_ratio=cfg.FVG_WICK_RATIO_MAX)
    engine = SignalEngine(rsm)
    atr_val = 0.0
    prev_close = b15[499].close
    log = []
    ifvg_triggers = 0
    for sb in range(500, total_bars):
        cur = b15[sb]
        tr = calculate_true_range(cur, prev_close)
        atr_val = update_atr(atr_val if atr_val > 0 else None, tr)
        prev_close = cur.close
        edt = datetime.fromtimestamp(cur.timestamp / 1000, tz=timezone.utc)
        ss.update(edt, cur.open, cur.high, cur.low, cur.close, atr_val)
        engine.progress_rsm(b15[max(0, sb - 500) : sb + 1], cur, ss, atr_val, symbol)
        # bot.py parity — yeni CBDR gunune tasinan TRIGGER_READY'yi temizle
        if not ss.cbdr_locked and rsm.can_trigger():
            sd = rsm.direction
            db = ss.daily_bias
            bias_reject = (
                (sd == "bullish" and db == DailyBias.BEARISH)
                or (sd == "bearish" and db == DailyBias.BULLISH)
                or db == DailyBias.NEUTRAL
            )
            if bias_reject:
                rsm.reset()
        if not ss.cbdr_locked:
            log.append(
                {
                    "bar_index": sb,
                    "cbdr_locked": ss.cbdr_locked,
                    "sweep_confirmed": ss.sweep_confirmed,
                    "sweep_direction": ss.sweep_direction,
                    "rsm_state": rsm.state_name,
                    "rsm_direction": rsm.direction,
                    "trigger_fvg_bar": rsm.trigger_fvg.bar_index
                    if rsm.trigger_fvg
                    else None,
                    "trigger_decision": "SKIP_NO_LOCK",
                    "entry_gate_decision": "DENY",
                }
            )
            continue
        result = engine.evaluate_trigger(cur, ss)
        decision = result.decision
        entry_gate = "ALLOW" if decision == "TRIGGER" else "DENY"
        if decision == "TRIGGER":
            if getattr(rsm, "_last_trigger_source", None) == "IFVG":
                ifvg_triggers += 1
            rsm.reset()  # bot.py entry sonrasi reset
        log.append(
            {
                "bar_index": sb,
                "cbdr_locked": ss.cbdr_locked,
                "sweep_confirmed": ss.sweep_confirmed,
                "sweep_direction": ss.sweep_direction,
                "rsm_state": rsm.state_name,
                "rsm_direction": rsm.direction,
                "trigger_fvg_bar": rsm.trigger_fvg.bar_index
                if rsm.trigger_fvg
                else None,
                "trigger_decision": decision,
                "entry_gate_decision": entry_gate,
            }
        )
    return log, ifvg_triggers


def _compare(symbol, b15, sh, eh):
    total_bars = len(b15)
    bt, bt_ifvg = _run_backtest(b15, symbol, sh, eh, total_bars)
    lv, lv_ifvg = _run_live(b15, symbol, sh, eh, total_bars)
    assert len(bt) == len(lv)

    core = 0
    decision_only = 0
    first_divergence = None
    trace_mismatches = []
    for a, b in zip(bt, lv):
        core_a = (
            a["rsm_state"],
            a["rsm_direction"],
            a["sweep_confirmed"],
            a["sweep_direction"],
            a["cbdr_locked"],
        )
        core_b = (
            b["rsm_state"],
            b["rsm_direction"],
            b["sweep_confirmed"],
            b["sweep_direction"],
            b["cbdr_locked"],
        )
        if core_a != core_b:
            core += 1
            if first_divergence is None:
                first_divergence = {
                    "bar": a["bar_index"],
                    "backtest": f"{a['rsm_state']},{a['rsm_direction']},sweep={a['sweep_confirmed']},fvg={a['trigger_fvg_bar']}",
                    "live": f"{b['rsm_state']},{b['rsm_direction']},sweep={b['sweep_confirmed']},fvg={b['trigger_fvg_bar']}",
                }
        elif a["trigger_decision"] != b["trigger_decision"]:
            decision_only += 1
            if len(trace_mismatches) < 10:
                trace_mismatches.append(
                    (a["bar_index"], a["trigger_decision"], b["trigger_decision"])
                )

    bt_trig = sum(1 for x in bt if x["trigger_decision"] == "TRIGGER")
    lv_trig = sum(1 for x in lv if x["trigger_decision"] == "TRIGGER")
    bt_sw = sum(1 for x in bt if x["sweep_confirmed"])
    lv_sw = sum(1 for x in lv if x["sweep_confirmed"])

    return ParityResult(
        symbol=symbol,
        session=(sh, eh),
        bars=total_bars,
        core_diff=core,
        decision_only_diff=decision_only,
        trigger_backtest=bt_trig,
        trigger_live=lv_trig,
        sweep_lock_backtest=bt_sw,
        sweep_lock_live=lv_sw,
        ifvg_backtest=bt_ifvg,
        ifvg_live=lv_ifvg,
        first_divergence=first_divergence,
        trace_mismatches=trace_mismatches,
    )


def _fail_message(res: ParityResult) -> str:
    lines = [
        f"symbol={res.symbol}",
        f"session={res.session[0]}-{res.session[1]} bars={res.bars}",
        f"core_diff={res.core_diff} (contract: 0)",
        f"TRIGGER backtest={res.trigger_backtest} live={res.trigger_live}",
        f"sweep-lock backtest={res.sweep_lock_backtest} live={res.sweep_lock_live}",
    ]
    if res.first_divergence:
        d = res.first_divergence
        lines.append("first_divergence=rsm_state/direction mismatch")
        lines.append(f"bar={d['bar']}")
        lines.append(f"backtest={d['backtest']}")
        lines.append(f"live={d['live']}")
    for bar, bt_dec, lv_dec in res.trace_mismatches[:5]:
        lines.append(f"decision-divergence bar={bar} backtest={bt_dec} live={lv_dec}")
    return "\n".join(lines)


# ── IFVG-on parity sozlesmesi ────────────────────────────────────────────
# IFVG_ENABLED=True iken: canli ve backtest ayni IFVG trigger/reddi uretmeli
# (guard-fix 2026-08-18). NORMAL benchmark'tan AYRI — IFVG ek sinyalleri
# TRIGGER sayisini artirir. Her satirda: ifvg = (backtest, live) IFVG
# trigger sayisi, trigger = toplam TRIGGER sayisi (NORMAL+IFVG).
IFVG_ON_BENCHMARK_CONTRACT: dict[str, dict] = {
    "SOLUSDT": {"ifvg": (76, 76), "trigger": (541, 541)},
    "BNBUSDT": {"ifvg": (79, 79), "trigger": (551, 551)},
    "AVAXUSDT": {"ifvg": (58, 58), "trigger": (593, 593)},
    "LINKUSDT": {"ifvg": (78, 78), "trigger": (621, 621)},
    "XRPUSDT": {"ifvg": (61, 61), "trigger": (593, 593)},
    "ATOMUSDT": {"ifvg": (78, 78), "trigger": (570, 570)},
    "ADAUSDT": {"ifvg": (68, 68), "trigger": (595, 595)},
    "APTUSDT": {"ifvg": (73, 73), "trigger": (539, 539)},
    "DOTUSDT": {"ifvg": (79, 79), "trigger": (562, 562)},
}


@pytest.fixture(scope="module")
def _contract():
    return BENCHMARK_CONTRACT


@pytest.mark.parametrize("symbol", list(BENCHMARK_CONTRACT.keys()))
def test_parity_contract(symbol):
    """Her sembol icin sabit benchmark sozlesmesini assert eder.

    CI assertions:
      - core_diff == 0
      - TRIGGER backtest == live
      - sweep-lock backtest == live
      - bar sayisi ve session penceresi sozlesmeye uygun
    """
    contract = BENCHMARK_CONTRACT[symbol]
    feather = DATA_DIR / f"{symbol}_1m_raw.feather"
    assert feather.is_file(), f"[{symbol}] fixture yok: {feather}"

    if os.environ.get("PARITY_SKIP_CHECKSUM", "0") != "1":
        actual = _sha256(feather)
        assert actual == contract["sha256"], (
            f"[{symbol}] fixture checksum degisti.\n"
            f"  beklenen: {contract['sha256']}\n"
            f"  gercek:   {actual}\n"
            "Input veri degisti — benchmark sozlesmesi bilincli olarak "
            "guncellenmeli, sessizce kabul edilmemeli."
        )

    b1 = load_data(str(feather))
    b15 = resample_15m(b1)
    assert b15 and len(b15) >= 520
    assert (
        len(b15) == contract["bars"]
    ), f"[{symbol}] bar sayisi {len(b15)} != {contract['bars']}"

    _info = get_session_hours(symbol)
    sh, eh = _info["start"], _info["end"]
    assert (sh, eh) == contract[
        "session"
    ], f"[{symbol}] session {sh}-{eh} != {contract['session']}"

    res = _compare(symbol, b15, sh, eh)

    assert res.core_diff == 0, f"[{symbol}] core-diff != 0\n{_fail_message(res)}"
    assert (
        res.trigger_backtest == res.trigger_live
    ), f"[{symbol}] TRIGGER esit degil bt={res.trigger_backtest} live={res.trigger_live}\n{_fail_message(res)}"
    assert (
        res.sweep_lock_backtest == res.sweep_lock_live
    ), f"[{symbol}] sweep-lock esit degil bt={res.sweep_lock_backtest} live={res.sweep_lock_live}"
    assert (
        res.trigger_backtest == contract["trigger"][0]
    ), f"[{symbol}] TRIGGER sayisi sozlesmeden sapti bt={res.trigger_backtest} != {contract['trigger'][0]}"
    assert (
        res.sweep_lock_backtest == contract["sweep_lock"][0]
    ), f"[{symbol}] sweep-lock sozlesmeden sapti bt={res.sweep_lock_backtest} != {contract['sweep_lock'][0]}"


@pytest.mark.parametrize("symbol", list(IFVG_ON_BENCHMARK_CONTRACT.keys()))
def test_parity_ifvg_on(symbol, monkeypatch):
    """IFVG-on parity: flag acikken canli ve backtest AYNI IFVG trigger/reddi
    uretir (guard-fix). IFVG-off benchmark'indan ayri sozlesme:
      - core_diff == 0 (state parity)
      - IFVG trigger sayisi backtest == live
      - IFVG trigger sayisi > 0 (senaryo gercekten IFVG yolunu calistiriyor)
      - toplam TRIGGER ve IFVG sayilari sozlesmeye uygun
    """
    contract = IFVG_ON_BENCHMARK_CONTRACT[symbol]
    feather = DATA_DIR / f"{symbol}_1m_raw.feather"
    assert feather.is_file(), f"[{symbol}] fixture yok: {feather}"

    b1 = load_data(str(feather))
    b15 = resample_15m(b1)
    assert b15 and len(b15) >= 520

    _info = get_session_hours(symbol)
    sh, eh = _info["start"], _info["end"]

    monkeypatch.setattr(cfg, "IFVG_ENABLED", True)
    res = _compare(symbol, b15, sh, eh)

    assert (
        res.core_diff == 0
    ), f"[{symbol}] IFVG-on core-diff != 0\n{_fail_message(res)}"
    assert (
        res.trigger_backtest == res.trigger_live
    ), f"[{symbol}] IFVG-on TRIGGER esit degil bt={res.trigger_backtest} live={res.trigger_live}"
    assert (
        res.ifvg_backtest == res.ifvg_live
    ), f"[{symbol}] IFVG trigger esit degil bt={res.ifvg_backtest} live={res.ifvg_live}"
    assert res.ifvg_backtest > 0, f"[{symbol}] IFVG-on senaryosu IFVG trigger uretmedi"
    assert (
        res.ifvg_backtest == contract["ifvg"][0]
    ), f"[{symbol}] IFVG sayisi sozlesmeden sapti bt={res.ifvg_backtest} != {contract['ifvg'][0]}"
    assert (
        res.trigger_backtest == contract["trigger"][0]
    ), f"[{symbol}] IFVG-on TRIGGER sayisi sozlesmeden sapti bt={res.trigger_backtest} != {contract['trigger'][0]}"
