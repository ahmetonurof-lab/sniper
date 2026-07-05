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
# Not: ETHUSDT ve SUIUSDT 2026-07-05 nihai kararla DEFAULT session'a atanarak geri eklendi.

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
            (0, 1, 1.0),
            (1, 1.5, 0.0),
            (1.5, 2, 1.0),
            (2, 3, 1.0),
            (3, 5, 1.5),
            (5, 999, 1.0),
        ],
    },
    "AVAXUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0.0, 1.0, 1.0),
            (1.0, 1.5, 1.0),  # veri yok, nötr fallback
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.5),  # WR 46.8 / BE+ 67.9 — altın vuruş
            (3.0, 5.0, 1.5),  # WR 45.8 / BE+ 66.1 — altın vuruş
            (5.0, 999.0, 1.2),  # n=191 küçük, temkinli
        ],
    },
    "DOTUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0, 1, 1.0),
            (1, 1.5, 1.0),
            (1.5, 2, 0.0),
            (2, 3, 1.5),
            (3, 5, 1.0),
            (5, 999, 1.5),
        ],
    },
    "NEARUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0.0, 1.0, 0.8),
            (1.0, 1.5, 1.0),
            (1.5, 2.0, 1.0),  # veri yok, nötr fallback
            (2.0, 3.0, 1.5),  # WR 44.4 / BE+ 66.8 — altın vuruş
            (3.0, 5.0, 1.5),  # WR 45.3 / BE+ 67.2 — altın vuruş
            (5.0, 999.0, 1.0),
        ],
    },
    "SOLUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0.0, 1.0, 1.2),
            (1.0, 1.5, 1.0),  # veri yok, nötr fallback
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 0.8),
            (5.0, 999.0, 1.2),
        ],
    },
    "XRPUSDT": {
        "session": "DEFAULT",
        "buckets": [
            (0, 1, 1.0),
            (1, 1.5, 1.0),
            (1.5, 2, 1.0),
            (2, 3, 1.0),
            (3, 5, 1.5),
            (5, 999, 1.0),
        ],
    },
    "ATOMUSDT": {
        "session": "REAL_CBDR",
        "buckets": [
            (0, 1, 0.0),
            (1, 1.5, 0.0),
            (1.5, 2, 1.5),
            (2, 3, 1.5),
            (3, 5, 1.0),
            (5, 999, 0.0),
        ],
    },
    "BTCUSDT": {
        "session": "REAL_CBDR",
        "buckets": [
            (0.0, 1.0, 0.0),  # n=41, PnL negatif — zehirli
            (1.0, 1.5, 1.2),  # en büyük PnL katkısı (+46491)
            (1.5, 2.0, 1.2),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 0.8),
            (5.0, 999.0, 0.8),  # n=36 çok küçük
        ],
    },
    "ETHUSDT": {
        "session": "DEFAULT",
        "buckets": [
            # 0-1%:   WR=34.5% BE+=60.7% PnL=+18018 → Standart
            # 1-1.5%: WR=38.0% BE+=63.7% PnL=+25358 → Standart Ustu (en yuksek PnL bucket)
            # 1.5-2%: WR=38.8% BE+=62.8% PnL=+8453  → Standart
            # 2-3%:   WR=36.1% BE+=58.7% PnL=+9769  → Standart
            # 3-5%:   WR=32.2% BE+=65.3% PnL=+5755  → Defansif (WR dusuk)
            # >5%:    WR=56.7% BE+=68.7% PnL=+3449  → Altin Vurus (WR>44% VE BE+>67%)
            (0.0, 1.0, 1.0),
            (1.0, 1.5, 1.2),  # en yüksek PnL bucket (+25k)
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.0),
            (3.0, 5.0, 0.8),
            (5.0, 999.0, 1.5),
        ],
    },
    "SUIUSDT": {
        "session": "DEFAULT",
        "buckets": [
            # DEFAULT WR=39.4% BE+=62.1% PnL=+104030 (1.0x nötr + EL 1.5x)
            # 0-1%:   WR=37.3% BE+=62.3% PnL=+5214  → Standart
            # 1-1.5%: WR=36.0% BE+=60.5% PnL=+15326 → Standart
            # 1.5-2%: WR=38.6% BE+=60.7% PnL=+12618 → Standart
            # 2-3%:   WR=42.7% BE+=65.2% PnL=+33806 → Standart Ustu (WR 42.7% yaklasik 44%)
            # 3-5%:   WR=39.4% BE+=61.0% PnL=+20710 → Standart
            # >5%:    WR=40.9% BE+=60.8% PnL=+16355 → Standart
            (0, 1, 1.0),
            (1, 1.5, 1.0),
            (1.5, 2, 1.0),
            (2, 3, 1.2),
            (3, 5, 1.0),
            (5, 999, 1.0),
        ],
    },
    "APTUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0.0, 1.0, 0.0),  # n=9, PnL negatif — zehirli (sample küçük ama negatif)
            (1.0, 1.5, 1.0),  # veri yok, nötr fallback
            (1.5, 2.0, 1.0),
            (2.0, 3.0, 1.5),  # WR 45.6 / BE+ 65.6 — altın vuruş
            (3.0, 5.0, 1.2),
            (5.0, 999.0, 1.5),  # WR 53.0 / BE+ 72.2 — altın vuruş
        ],
    },
    "BNBUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0, 1, 0.0),
            (1, 1.5, 1.0),
            (1.5, 2, 1.5),
            (2, 3, 1.0),
            (3, 5, 1.0),
            (5, 999, 1.0),
        ],
    },
    "LINKUSDT": {
        "session": "ASIA_RANGE",
        "buckets": [
            (0, 1, 0.0),
            (1, 1.5, 1.0),
            (1.5, 2, 1.0),
            (2, 3, 1.5),
            (3, 5, 1.0),
            (5, 999, 1.0),
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
