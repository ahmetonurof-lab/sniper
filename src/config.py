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
    "OPUSDT",
    "ARBUSDT",
    "INJUSDT",
    "ALGOUSDT",
    "AAVEUSDT",
    "UNIUSDT",
    "DOGEUSDT",
]

# ── Coin bazli FVG esikleri (futures ATR bazli hesaplanacak) ──
FVG_SIZE_MAP: dict[str, float] = {
    "BTCUSDT": 0.0,
    "BNBUSDT": 0.0,
    "SOLUSDT": 0.0,
    "AVAXUSDT": 0.0,
    "LINKUSDT": 0.0,
    "XRPUSDT": 0.0,
    "ATOMUSDT": 0.0,
    "ADAUSDT": 0.0,
    "APTUSDT": 0.0,
    "DOTUSDT": 0.0,
    "NEARUSDT": 0.0,
    "ETHUSDT": 0.0,
    "SUIUSDT": 0.0,
    "OPUSDT": 0.0,
    "ARBUSDT": 0.0,
    "INJUSDT": 0.0,
    "ALGOUSDT": 0.0,
    "AAVEUSDT": 0.0,
    "UNIUSDT": 0.0,
    "DOGEUSDT": 0.0,
}

# ── ATR-bazlı dinamik FVG eşiği (statik FVG_SIZE_MAP yerine) ──
FVG_MIN_SIZE_ATR_MULT = 0.06

# ── CBDR Risk Matrisi (coin bazli session + bucket carpani) ─────
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
#
# weekend_bonus: Cumartesi/Pazar gunleri cbdr_mult ek carpan
# weekend_mult:  kac kat uygulanacak (ornek: 1.5 = %50 fazla)
CBDR_RISK_MATRIX: dict[str, dict] = {
    "BTCUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=625 WR=38.1% CI=[34.4%,41.9%]
            (1.0, 1.5, 1.5),  # n=366 WR=45.6% CI=[40.6%,50.8%]
            (1.5, 2.0, 1.5),  # n=173 WR=59.0% CI=[51.5%,66.0%]
            (2.0, 3.0, 1.5),  # n=200 WR=50.0% CI=[43.1%,56.9%]
            (3.0, 5.0, 1.0),  # n=31 WR=48.4% CI=[32.0%,65.2%]
            (5.0, 999.0, 1.0),  # n=10 WR=70.0% CI=[39.7%,89.2%]
        ],
    },
    "BNBUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.5),  # n=968 WR=49.2% CI=[46.0%,52.3%]
            (1.0, 1.5, 1.5),  # n=532 WR=52.8% CI=[48.6%,57.0%]
            (1.5, 2.0, 1.2),  # n=190 WR=46.3% CI=[39.4%,53.4%]
            (2.0, 3.0, 1.5),  # n=197 WR=58.9% CI=[51.9%,65.5%]
            (3.0, 5.0, 1.2),  # n=154 WR=46.8% CI=[39.0%,54.6%]
            (5.0, 999.0, 1.0),  # n=8 WR=37.5% CI=[13.7%,69.4%]
        ],
    },
    "SOLUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.5),  # n=401 WR=47.6% CI=[42.8%,52.5%]
            (1.0, 1.5, 1.2),  # n=443 WR=41.8% CI=[37.3%,46.4%]
            (1.5, 2.0, 1.5),  # n=471 WR=52.9% CI=[48.4%,57.3%]
            (2.0, 3.0, 1.5),  # n=315 WR=47.6% CI=[42.2%,53.1%]
            (3.0, 5.0, 1.5),  # n=140 WR=50.0% CI=[41.8%,58.2%]
            (5.0, 999.0, 1.0),  # n=73 WR=54.8% CI=[43.4%,65.7%]
        ],
    },
    "AVAXUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.5),  # n=151 WR=62.3% CI=[54.3%,69.6%]
            (1.0, 1.5, 1.5),  # n=308 WR=45.8% CI=[40.3%,51.4%]
            (1.5, 2.0, 1.5),  # n=424 WR=51.9% CI=[47.1%,56.6%]
            (2.0, 3.0, 1.5),  # n=382 WR=48.2% CI=[43.2%,53.2%]
            (3.0, 5.0, 1.5),  # n=284 WR=53.5% CI=[47.7%,59.2%]
            (5.0, 999.0, 1.2),  # n=124 WR=46.0% CI=[37.4%,54.7%]
        ],
    },
    "LINKUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=297 WR=39.4% CI=[34.0%,45.1%]
            (1.0, 1.5, 1.2),  # n=399 WR=40.6% CI=[35.9%,45.5%]
            (1.5, 2.0, 1.2),  # n=336 WR=43.2% CI=[38.0%,48.5%]
            (2.0, 3.0, 1.5),  # n=450 WR=50.4% CI=[45.8%,55.0%]
            (3.0, 5.0, 1.5),  # n=163 WR=56.4% CI=[48.8%,63.8%]
            (5.0, 999.0, 1.0),  # n=56 WR=55.4% CI=[42.4%,67.6%]
        ],
    },
    "XRPUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.5),  # n=791 WR=46.4% CI=[42.9%,49.9%]
            (1.0, 1.5, 1.5),  # n=430 WR=45.6% CI=[40.9%,50.3%]
            (1.5, 2.0, 1.5),  # n=281 WR=47.7% CI=[41.9%,53.5%]
            (2.0, 3.0, 1.5),  # n=297 WR=53.5% CI=[47.9%,59.1%]
            (3.0, 5.0, 1.5),  # n=145 WR=58.6% CI=[50.5%,66.3%]
            (5.0, 999.0, 1.0),  # n=33 WR=57.6% CI=[40.8%,72.8%]
        ],
    },
    "ATOMUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.5),  # n=378 WR=50.3% CI=[45.2%,55.3%]
            (1.0, 1.5, 1.2),  # n=508 WR=40.7% CI=[36.6%,45.1%]
            (1.5, 2.0, 1.5),  # n=338 WR=50.9% CI=[45.6%,56.2%]
            (2.0, 3.0, 1.5),  # n=386 WR=45.1% CI=[40.2%,50.1%]
            (3.0, 5.0, 1.5),  # n=184 WR=60.3% CI=[53.1%,67.1%]
            (5.0, 999.0, 1.0),  # n=28 WR=53.6% CI=[35.8%,70.5%]
        ],
    },
    "ADAUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.2),  # n=363 WR=42.4% CI=[37.4%,47.6%]
            (1.0, 1.5, 1.5),  # n=409 WR=47.9% CI=[43.1%,52.8%]
            (1.5, 2.0, 1.5),  # n=364 WR=50.0% CI=[44.9%,55.1%]
            (2.0, 3.0, 1.5),  # n=427 WR=46.1% CI=[41.5%,50.9%]
            (3.0, 5.0, 1.5),  # n=224 WR=52.2% CI=[45.7%,58.7%]
            (5.0, 999.0, 1.0),  # n=65 WR=50.8% CI=[38.9%,62.5%]
        ],
    },
    "APTUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=206 WR=36.4% CI=[30.1%,43.2%]
            (1.0, 1.5, 1.2),  # n=496 WR=42.9% CI=[38.7%,47.3%]
            (1.5, 2.0, 1.5),  # n=480 WR=51.7% CI=[47.2%,56.1%]
            (2.0, 3.0, 1.5),  # n=479 WR=56.8% CI=[52.3%,61.1%]
            (3.0, 5.0, 1.5),  # n=355 WR=49.6% CI=[44.4%,54.8%]
            (5.0, 999.0, 1.0),  # n=43 WR=53.5% CI=[38.9%,67.5%]
        ],
    },
    "DOTUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.5),  # n=380 WR=48.2% CI=[43.2%,53.2%]
            (1.0, 1.5, 1.5),  # n=442 WR=47.5% CI=[42.9%,52.2%]
            (1.5, 2.0, 1.2),  # n=403 WR=43.4% CI=[38.7%,48.3%]
            (2.0, 3.0, 1.5),  # n=450 WR=55.3% CI=[50.7%,59.9%]
            (3.0, 5.0, 1.2),  # n=179 WR=44.1% CI=[37.1%,51.5%]
            (5.0, 999.0, 1.0),  # n=79 WR=73.4% CI=[62.8%,81.9%]
        ],
    },
    "NEARUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.2),  # n=104 WR=47.1% CI=[37.8%,56.6%]
            (1.0, 1.5, 1.2),  # n=470 WR=44.7% CI=[40.2%,49.2%]
            (1.5, 2.0, 1.5),  # n=423 WR=50.6% CI=[45.8%,55.3%]
            (2.0, 3.0, 1.5),  # n=476 WR=46.2% CI=[41.8%,50.7%]
            (3.0, 5.0, 1.5),  # n=335 WR=52.8% CI=[47.5%,58.1%]
            (5.0, 999.0, 1.5),  # n=132 WR=61.4% CI=[52.8%,69.2%]
        ],
    },
    "ETHUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.2),  # n=645 WR=41.6% CI=[37.8%,45.4%]
            (1.0, 1.5, 1.2),  # n=407 WR=42.0% CI=[37.3%,46.9%]
            (1.5, 2.0, 1.2),  # n=213 WR=46.0% CI=[39.4%,52.7%]
            (2.0, 3.0, 1.2),  # n=202 WR=43.6% CI=[36.9%,50.5%]
            (3.0, 5.0, 1.0),  # n=51 WR=35.3% CI=[23.6%,49.0%]
            (5.0, 999.0, 1.0),  # n=20 WR=65.0% CI=[43.3%,81.9%]
        ],
    },
    "SUIUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.2),  # n=186 WR=44.6% CI=[37.7%,51.8%]
            (1.0, 1.5, 1.2),  # n=313 WR=42.2% CI=[36.8%,47.7%]
            (1.5, 2.0, 1.2),  # n=370 WR=44.3% CI=[39.3%,49.4%]
            (2.0, 3.0, 1.5),  # n=513 WR=47.6% CI=[43.3%,51.9%]
            (3.0, 5.0, 1.5),  # n=358 WR=45.3% CI=[40.2%,50.4%]
            (5.0, 999.0, 1.2),  # n=110 WR=44.5% CI=[35.6%,53.9%]
        ],
    },
    "OPUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=57 WR=50.9% CI=[38.3%,63.4%]
            (1.0, 1.5, 1.5),  # n=223 WR=48.9% CI=[42.4%,55.4%]
            (1.5, 2.0, 1.5),  # n=298 WR=46.6% CI=[41.1%,52.3%]
            (2.0, 3.0, 1.5),  # n=555 WR=53.7% CI=[49.5%,57.8%]
            (3.0, 5.0, 1.5),  # n=377 WR=54.4% CI=[49.3%,59.3%]
            (5.0, 999.0, 1.5),  # n=173 WR=53.8% CI=[46.3%,61.0%]
        ],
    },
    "ARBUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=48 WR=22.9% CI=[13.3%,36.5%]
            (1.0, 1.5, 1.5),  # n=236 WR=48.3% CI=[42.0%,54.7%]
            (1.5, 2.0, 1.5),  # n=345 WR=56.5% CI=[51.2%,61.7%]
            (2.0, 3.0, 1.5),  # n=419 WR=52.0% CI=[47.2%,56.8%]
            (3.0, 5.0, 1.5),  # n=299 WR=51.5% CI=[45.9%,57.1%]
            (5.0, 999.0, 1.5),  # n=145 WR=55.2% CI=[47.0%,63.0%]
        ],
    },
    "INJUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=120 WR=34.2% CI=[26.3%,43.0%]
            (1.0, 1.5, 1.5),  # n=383 WR=45.2% CI=[40.3%,50.2%]
            (1.5, 2.0, 1.2),  # n=378 WR=42.1% CI=[37.2%,47.1%]
            (2.0, 3.0, 1.2),  # n=500 WR=44.0% CI=[39.7%,48.4%]
            (3.0, 5.0, 1.5),  # n=471 WR=51.4% CI=[46.9%,55.9%]
            (5.0, 999.0, 1.0),  # n=68 WR=26.5% CI=[17.4%,38.0%]
        ],
    },
    "ALGOUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=44 WR=52.3% CI=[37.9%,66.2%]
            (1.0, 1.5, 1.5),  # n=284 WR=48.2% CI=[42.5%,54.0%]
            (1.5, 2.0, 1.5),  # n=280 WR=46.8% CI=[41.0%,52.6%]
            (2.0, 3.0, 1.5),  # n=421 WR=49.4% CI=[44.7%,54.2%]
            (3.0, 5.0, 1.2),  # n=251 WR=45.0% CI=[39.0%,51.2%]
            (5.0, 999.0, 1.5),  # n=180 WR=55.0% CI=[47.7%,62.1%]
        ],
    },
    "AAVEUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=192 WR=41.7% CI=[34.9%,48.7%]
            (1.0, 1.5, 1.5),  # n=468 WR=51.9% CI=[47.4%,56.4%]
            (1.5, 2.0, 1.2),  # n=293 WR=42.3% CI=[36.8%,48.0%]
            (2.0, 3.0, 1.5),  # n=435 WR=50.6% CI=[45.9%,55.2%]
            (3.0, 5.0, 1.5),  # n=301 WR=57.1% CI=[51.5%,62.6%]
            (5.0, 999.0, 1.5),  # n=114 WR=64.9% CI=[55.8%,73.1%]
        ],
    },
    "UNIUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=166 WR=39.2% CI=[32.1%,46.7%]
            (1.0, 1.5, 1.0),  # n=444 WR=37.6% CI=[33.2%,42.2%]
            (1.5, 2.0, 1.2),  # n=337 WR=43.0% CI=[37.8%,48.4%]
            (2.0, 3.0, 1.5),  # n=417 WR=60.2% CI=[55.4%,64.8%]
            (3.0, 5.0, 1.5),  # n=278 WR=51.4% CI=[45.6%,57.3%]
            (5.0, 999.0, 1.0),  # n=93 WR=34.4% CI=[25.5%,44.5%]
        ],
    },
    "DOGEUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=315 WR=38.7% CI=[33.5%,44.2%]
            (1.0, 1.5, 1.0),  # n=320 WR=36.9% CI=[31.8%,42.3%]
            (1.5, 2.0, 1.5),  # n=270 WR=48.5% CI=[42.6%,54.5%]
            (2.0, 3.0, 1.5),  # n=401 WR=50.4% CI=[45.5%,55.2%]
            (3.0, 5.0, 1.5),  # n=266 WR=58.3% CI=[52.3%,64.0%]
            (5.0, 999.0, 1.5),  # n=107 WR=51.4% CI=[42.0%,60.7%]
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
