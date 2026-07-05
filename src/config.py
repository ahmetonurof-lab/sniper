# config.py — sniper paper trade
import os
from pathlib import Path

from dotenv import load_dotenv

# Root .env'i oku (sniper/src/ altındayken root'a çık)
_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
_env_file = _ROOT_DIR / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    load_dotenv()  # fallback: mevcut dizinde ara

INITIAL_BALANCE = 10000.0
RISK_PER_TRADE = 0.003
LEVERAGE = 5
LOG_LEVEL = "INFO"

# ── Semboller (aktif trade listesi) ───────────────────────────
SYMBOLS = [
    "BTCUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "XRPUSDT",
    "ATOMUSDT",
    "ADAUSDT",
    "APTUSDT",
    "DOTUSDT",
    "NEARUSDT",
    "ETHUSDT",
    "SUIUSDT",
]
# Not: ETHUSDT 2026-07-06 nihai kararla REAL_CBDR'e atandi, SUIUSDT DEFAULT'ta kaldi.

# ── Coin bazli FVG esikleri ────────────────────────────────────
FVG_SIZE_MAP: dict[str, float] = {
    "BTCUSDT": 10.0,
    "ETHUSDT": 1.5,
    "BNBUSDT": 0.8,
    "SOLUSDT": 0.14,
    "AVAXUSDT": 0.01,
    "LINKUSDT": 0.01,
    "XRPUSDT": 0.002,
    "ATOMUSDT": 0.005,
    "ADAUSDT": 0.0003,
    "SUIUSDT": 0.001,
    "APTUSDT": 0.003,
    "DOTUSDT": 0.003,
    "NEARUSDT": 0.001,
}

# ── ATR-bazlı dinamik FVG eşiği (statik FVG_SIZE_MAP yerine) ──
# MULT scan 2026-07-03: 195 run (0.02-0.30). Best: 0.06 (en sağlam orta nokta)
FVG_MIN_SIZE_ATR_MULT = 0.06

# ── CBDR Risk Matrisi (coin bazli session + bucket carpani) ─────
# ── Session ismi -> saat araligi ──────────────────────────────
SESSION_HOURS: dict[str, dict[str, int]] = {
    "DEFAULT": {"start": 22, "end": 2},
    "REAL_CBDR": {"start": 19, "end": 1},
    "ASIA_RANGE": {"start": 1, "end": 5},
}
# Carpan mantigi:
#   1.5x = Altin Vurus (WR > %44 veya BE+ > %67)
#   1.2x = Standart Ustu (net avantaj)
#   1.0x = Standart
#   0.8x = Defansif (WR dusuk ama PnL pozitif)
#   0.5x = Zayif (edge kayboluyor)
#   0.0x = ZEHIRLI / YASAKLI (sinyal gelse bile girme)
CBDR_RISK_MATRIX: dict[str, dict] = {
    "ADAUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0.0, 1.0, 1.0),
            (1.0, 1.5, 0.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 1.5),
            (5.0, 999.0, 1.0),
        ],
    },
    "APTUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0.0, 1.0, 0.0),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.5),
            (3.0, 5.0, 1.2),
            (5.0, 999.0, 1.5),
        ],
    },
    "ATOMUSDT": {
        "session": "REAL_CBDR",
        "buckets": [
            (0.0, 1.0, 0.0),
            (1.0, 1.5, 0.0),
            (1.5, 2.0, 1.5),
            (2.0, 3.0, 1.5),
            (3.0, 5.0, 1.0),
            (5.0, 999.0, 0.0),
        ],
    },
    "AVAXUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0.0, 1.0, 1.0),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.5),
            (3.0, 5.0, 1.0),
            (5.0, 999.0, 0.8),
        ],
    },
    "BNBUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0.0, 1.0, 0.0),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.5),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 1.0),
            (5.0, 999.0, 1.0),
        ],
    },
    "BTCUSDT": {
        "session": "REAL_CBDR",
        "buckets": [
            (0.0, 1.0, 0.0),
            (1.0, 1.5, 1.2),
            (1.5, 2.0, 1.2),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 0.8),
            (5.0, 999.0, 0.8),
        ],
    },
    "DOTUSDT": {
        "session": "REAL_CBDR",
        "buckets": [
            (0.0, 1.0, 0.8),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.2),
            (3.0, 5.0, 1.0),
            (5.0, 999.0, 1.5),
        ],
    },
    "ETHUSDT": {
        "session": "REAL_CBDR",
        "buckets": [
            (0.0, 1.0, 0.8),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.2),
            (2.0, 3.0, 0.8),
            (3.0, 5.0, 1.5),
            (5.0, 999.0, 0.8),
        ],
    },
    "LINKUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0.0, 1.0, 0.0),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.5),
            (3.0, 5.0, 1.0),
            (5.0, 999.0, 1.0),
        ],
    },
    "NEARUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0.0, 1.0, 0.8),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.2),
            (3.0, 5.0, 1.2),
            (5.0, 999.0, 1.5),
        ],
    },
    "SOLUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0.0, 1.0, 1.2),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 0.8),
            (5.0, 999.0, 1.2),
        ],
    },
    "SUIUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0.0, 1.0, 1.0),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.2),
            (3.0, 5.0, 1.0),
            (5.0, 999.0, 1.0),
        ],
    },
    "XRPUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0.0, 1.0, 1.0),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 1.5),
            (5.0, 999.0, 1.0),
        ],
    },
}

# ── Risk parametreleri ─────────────────────────────────────────
SL_ATR_MULT = 1.5
TP_RR = 2.0
FVG_BUFFER_MULT = 0.50
EARLY_LONDON_RISK_MULT = 1.5  # 02-08 UTC risk carpani (Altin Oran)

# ── Dinamik FVG filtreleri (ATR bazli) ────────────────────────
MIN_REL_FVG_THRESHOLD = 0.50

# ── FVG zaman asimi (expiry) ───────────────────────────────────
GLOBAL_FVG_EXPIRY_BARS = 45

# ── Magic Numbers (Faz 1.2) ────────────────────────────────────
CBDR_DEAD_THRESHOLD_PCT = 0.5
ASIA_DEAD_THRESHOLD_PCT = 0.3
TRAIL_MIN_MOVE_MULT = 0.2
BE_RISK_MULT = 1.0
BE_SPREAD_PTS = 0.0
ATR_TRAIL_MULT = 0.25
MIN_STOP_DIST_PCT = 0.006
MAX_MARGIN_PCT = 0.20
MIN_RISK_DIST_ATR_MULT = 0.1
MAX_SL_DIST_MULT = 2.0
DEFAULT_ATR_FALLBACK_PCT = 0.0001
CBDR_SWEEP_ATR_TOLERANCE_MULT = 0.5
CBDR_SWEEP_DEFAULT_TOLERANCE = 10.0
FVG_BUFFER_MIN_FACTOR = 0.10
FVG_WICK_RATIO_MAX = 0.75

# ── Binance API ────────────────────────────────────────────────
BINANCE_API_KEY = os.getenv("TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")
IS_TESTNET = os.getenv("TESTNET", "True").lower() == "true"
