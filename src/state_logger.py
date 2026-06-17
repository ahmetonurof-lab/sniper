"""
state_logger.py — NEXUS V3
Her 15m kapanışında tüm sembollerin state snapshot'ını CSV'ye yazar.
Dosya: output/summary/summary_YYYY-MM-DD.csv
Rotasyon: 10 günlük
"""

import csv
import logging
import os
import threading
from datetime import datetime, timedelta

log = logging.getLogger("nexus.state_logger")
SUMMARY_DIR = "output/summary"
MAX_DAYS = 10
_csv_lock = threading.Lock()

FIELDS = [
    "timestamp",
    "symbol",
    "d1_bias",
    "h4_bias",
    "bias_strength",
    "d1_bos_bar_index",
    "d1_bos_level",
    "h4_sl",
    "h1_tp",
    "sweep",
    "sweep_side",
    "sweep_tf",
    "sweep_level",
    "sweep_bar_index",
    "mss",
    "mss_level",
    "mss_bar_index",
    "mss_direction",
    "impulse_origin",
    "fvg_upper",
    "fvg_lower",
    "fvg_ce",
    "fvg_bar_index",
    "fvg_direction",
    "fvg_tf",
    "fvg_case",
    "retrace",
    "ltf",
    "fvg_missed",
    "state",
]


def _today_path() -> str:
    return os.path.join(SUMMARY_DIR, f"summary_{datetime.now().strftime('%Y-%m-%d')}.csv")


def _rotate() -> None:
    try:
        cutoff = datetime.now() - timedelta(days=MAX_DAYS)
        for fname in os.listdir(SUMMARY_DIR):
            if not (fname.startswith("summary_") and fname.endswith(".csv")):
                continue
            try:
                file_date = datetime.strptime(fname[8:-4], "%Y-%m-%d")
                if file_date < cutoff:
                    os.remove(os.path.join(SUMMARY_DIR, fname))
                    log.info("[STATE-LOG] Silindi: %s", fname)
            except ValueError:
                continue
    except Exception as e:
        log.warning("[STATE-LOG] Rotate hatası: %s", e)


def write_snapshot(symbol: str, state) -> None:
    try:
        os.makedirs(SUMMARY_DIR, exist_ok=True)
        path = _today_path()

        fvg_case = (
            "C" if getattr(state, "fvg_missed", False) else ("A" if getattr(state, "retrace_seen", False) else "")
        )
        fvg_upper = getattr(state, "fvg_upper", None)
        fvg_lower = getattr(state, "fvg_lower", None)
        fvg_ce = round((fvg_upper + fvg_lower) / 2, 6) if (fvg_upper and fvg_lower) else ""
        sweep_side = ""
        if getattr(state, "sweep_detected", False):
            sweep_side = "SSL" if getattr(state, "direction", "") == "LONG" else "BSL"
        st_val = getattr(state, "state", "")
        state_str = st_val.value if hasattr(st_val, "value") else str(st_val)

        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            getattr(state, "htf_bias", ""),
            getattr(state, "htf_bias", ""),
            getattr(state, "htf_strength", ""),
            "",
            "",
            getattr(state, "h4_swing_level", ""),
            getattr(state, "h1_liquidity_level", ""),
            getattr(state, "sweep_detected", False),
            sweep_side,
            getattr(state, "sweep_tf", ""),
            getattr(state, "sweep_level", ""),
            getattr(state, "sweep_bar_index", ""),
            getattr(state, "mss_confirmed", False),
            getattr(state, "mss_level", ""),
            getattr(state, "mss_bar_index", ""),
            getattr(state, "direction", ""),
            getattr(state, "displacement_origin", ""),
            fvg_upper or "",
            fvg_lower or "",
            fvg_ce,
            getattr(state, "fvg_entry_bar_index", ""),
            "bearish"
            if getattr(state, "direction", "") == "SHORT"
            else ("bullish" if getattr(state, "direction", "") == "LONG" else ""),
            getattr(state, "fvg_tf", ""),
            fvg_case,
            getattr(state, "retrace_seen", False),
            getattr(state, "ltf_confirmed", False),
            getattr(state, "fvg_missed", False),
            state_str,
        ]

        with _csv_lock, open(path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                _rotate()
                writer.writerow(FIELDS)
            writer.writerow(row)

    except OSError as e:
        log.critical("[STATE LOGGER] Disk yazma hatası — snapshot kaybedildi: %s", e)
    except Exception as e:
        log.error("[STATE-LOG] write_snapshot hatası — %s: %s", symbol, e)
