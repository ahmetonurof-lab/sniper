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
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=1693 WR=21.3% CI=[19.4%,23.3%]
            (1.0, 1.5, 0.8),  # n=492 WR=33.3% CI=[29.3%,37.6%]
            (1.5, 2.0, 0.8),  # n=186 WR=33.3% CI=[27.0%,40.4%]
            (2.0, 3.0, 1.0),  # n=120 WR=35.8% CI=[27.8%,44.7%]
            (3.0, 5.0, 1.0),  # n=58 WR=27.6% CI=[17.8%,40.2%]
            (5.0, 999.0, 1.0),  # n=24 WR=50.0% CI=[31.4%,68.6%]
        ],
    },
    "BNBUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=740 WR=20.3% CI=[17.5%,23.3%]
            (1.0, 1.5, 0.8),  # n=586 WR=34.8% CI=[31.1%,38.8%]
            (1.5, 2.0, 1.0),  # n=241 WR=37.3% CI=[31.5%,43.6%]
            (2.0, 3.0, 1.0),  # n=329 WR=35.3% CI=[30.3%,40.6%]
            (3.0, 5.0, 1.2),  # n=145 WR=43.4% CI=[35.7%,51.6%]
            (5.0, 999.0, 1.0),  # n=22 WR=31.8% CI=[16.4%,52.7%]
        ],
    },
    "SOLUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=186 WR=28.0% CI=[22.0%,34.8%]
            (1.0, 1.5, 0.5),  # n=354 WR=27.1% CI=[22.8%,32.0%]
            (1.5, 2.0, 0.8),  # n=454 WR=33.5% CI=[29.3%,37.9%]
            (2.0, 3.0, 1.2),  # n=618 WR=40.8% CI=[37.0%,44.7%]
            (3.0, 5.0, 1.0),  # n=388 WR=38.1% CI=[33.4%,43.1%]
            (5.0, 999.0, 1.0),  # n=107 WR=39.3% CI=[30.5%,48.7%]
        ],
    },
    "AVAXUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=218 WR=41.3% CI=[35.0%,47.9%]
            (1.0, 1.5, 1.0),  # n=452 WR=38.9% CI=[34.6%,43.5%]
            (1.5, 2.0, 1.0),  # n=560 WR=35.2% CI=[31.3%,39.2%]
            (2.0, 3.0, 0.8),  # n=553 WR=34.5% CI=[30.7%,38.6%]
            (3.0, 5.0, 1.0),  # n=421 WR=37.8% CI=[33.3%,42.5%]
            (5.0, 999.0, 1.0),  # n=169 WR=38.5% CI=[31.5%,46.0%]
        ],
    },
    "LINKUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=248 WR=27.0% CI=[21.9%,32.9%]
            (1.0, 1.5, 0.5),  # n=476 WR=29.2% CI=[25.3%,33.4%]
            (1.5, 2.0, 1.0),  # n=540 WR=35.2% CI=[31.3%,39.3%]
            (2.0, 3.0, 0.8),  # n=532 WR=33.5% CI=[29.6%,37.6%]
            (3.0, 5.0, 1.2),  # n=365 WR=41.6% CI=[36.7%,46.8%]
            (5.0, 999.0, 1.0),  # n=68 WR=44.1% CI=[32.9%,55.9%]
        ],
    },
    "XRPUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=665 WR=23.8% CI=[20.7%,27.1%]
            (1.0, 1.5, 0.8),  # n=537 WR=32.0% CI=[28.2%,36.1%]
            (1.5, 2.0, 0.5),  # n=308 WR=27.3% CI=[22.6%,32.5%]
            (2.0, 3.0, 1.2),  # n=372 WR=43.3% CI=[38.3%,48.4%]
            (3.0, 5.0, 1.0),  # n=183 WR=36.1% CI=[29.5%,43.2%]
            (5.0, 999.0, 1.0),  # n=84 WR=46.4% CI=[36.2%,57.0%]
        ],
    },
    "ATOMUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=570 WR=34.4% CI=[30.6%,38.4%]
            (1.0, 1.5, 0.5),  # n=712 WR=29.9% CI=[26.7%,33.4%]
            (1.5, 2.0, 1.0),  # n=556 WR=35.3% CI=[31.4%,39.3%]
            (2.0, 3.0, 0.8),  # n=566 WR=34.8% CI=[31.0%,38.8%]
            (3.0, 5.0, 1.5),  # n=246 WR=47.2% CI=[41.0%,53.4%]
            (5.0, 999.0, 1.0),  # n=45 WR=51.1% CI=[37.0%,65.0%]
        ],
    },
    "ADAUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=599 WR=27.9% CI=[24.4%,31.6%]
            (1.0, 1.5, 0.8),  # n=565 WR=34.0% CI=[30.2%,38.0%]
            (1.5, 2.0, 1.0),  # n=543 WR=37.0% CI=[33.1%,41.2%]
            (2.0, 3.0, 1.0),  # n=606 WR=38.4% CI=[34.7%,42.4%]
            (3.0, 5.0, 0.8),  # n=306 WR=34.3% CI=[29.2%,39.8%]
            (5.0, 999.0, 1.0),  # n=99 WR=37.4% CI=[28.5%,47.2%]
        ],
    },
    "APTUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=107 WR=29.0% CI=[21.2%,38.2%]
            (1.0, 1.5, 0.5),  # n=379 WR=28.0% CI=[23.7%,32.7%]
            (1.5, 2.0, 0.8),  # n=605 WR=32.4% CI=[28.8%,36.2%]
            (2.0, 3.0, 0.8),  # n=715 WR=32.7% CI=[29.4%,36.3%]
            (3.0, 5.0, 1.2),  # n=455 WR=41.3% CI=[36.9%,45.9%]
            (5.0, 999.0, 1.5),  # n=173 WR=47.4% CI=[40.1%,54.8%]
        ],
    },
    "DOTUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=500 WR=32.4% CI=[28.4%,36.6%]
            (1.0, 1.5, 0.8),  # n=677 WR=34.9% CI=[31.4%,38.5%]
            (1.5, 2.0, 0.8),  # n=608 WR=31.1% CI=[27.5%,34.9%]
            (2.0, 3.0, 1.0),  # n=673 WR=38.6% CI=[35.0%,42.4%]
            (3.0, 5.0, 0.8),  # n=271 WR=34.3% CI=[28.9%,40.2%]
            (5.0, 999.0, 1.0),  # n=95 WR=54.7% CI=[44.7%,64.4%]
        ],
    },
    "NEARUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=202 WR=25.7% CI=[20.2%,32.2%]
            (1.0, 1.5, 0.8),  # n=587 WR=34.1% CI=[30.4%,38.0%]
            (1.5, 2.0, 1.0),  # n=561 WR=37.1% CI=[33.2%,41.1%]
            (2.0, 3.0, 1.0),  # n=725 WR=36.6% CI=[33.1%,40.1%]
            (3.0, 5.0, 1.2),  # n=495 WR=42.4% CI=[38.1%,46.8%]
            (5.0, 999.0, 1.5),  # n=181 WR=53.0% CI=[45.8%,60.2%]
        ],
    },
    "ETHUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=911 WR=23.5% CI=[20.9%,26.4%]
            (1.0, 1.5, 0.5),  # n=663 WR=28.4% CI=[25.1%,31.9%]
            (1.5, 2.0, 0.8),  # n=302 WR=32.5% CI=[27.4%,37.9%]
            (2.0, 3.0, 0.5),  # n=275 WR=29.5% CI=[24.4%,35.1%]
            (3.0, 5.0, 1.0),  # n=98 WR=31.6% CI=[23.3%,41.4%]
            (5.0, 999.0, 1.0),  # n=28 WR=71.4% CI=[52.9%,84.7%]
        ],
    },
    "SUIUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=273 WR=24.2% CI=[19.5%,29.6%]
            (1.0, 1.5, 0.8),  # n=455 WR=32.5% CI=[28.4%,37.0%]
            (1.5, 2.0, 0.5),  # n=446 WR=29.1% CI=[25.1%,33.5%]
            (2.0, 3.0, 0.5),  # n=699 WR=27.8% CI=[24.6%,31.2%]
            (3.0, 5.0, 1.0),  # n=564 WR=37.2% CI=[33.3%,41.3%]
            (5.0, 999.0, 1.2),  # n=213 WR=46.0% CI=[39.4%,52.7%]
        ],
    },
    "OPUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=201 WR=36.3% CI=[30.0%,43.2%]
            (1.0, 1.5, 0.8),  # n=592 WR=34.1% CI=[30.4%,38.0%]
            (1.5, 2.0, 1.2),  # n=569 WR=43.6% CI=[39.6%,47.7%]
            (2.0, 3.0, 0.8),  # n=650 WR=33.4% CI=[29.9%,37.1%]
            (3.0, 5.0, 1.0),  # n=541 WR=38.6% CI=[34.6%,42.8%]
            (5.0, 999.0, 1.0),  # n=133 WR=35.3% CI=[27.7%,43.8%]
        ],
    },
    "ARBUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=226 WR=29.6% CI=[24.1%,35.9%]
            (1.0, 1.5, 1.0),  # n=734 WR=36.4% CI=[33.0%,39.9%]
            (1.5, 2.0, 1.0),  # n=493 WR=39.1% CI=[34.9%,43.5%]
            (2.0, 3.0, 1.0),  # n=805 WR=37.4% CI=[34.1%,40.8%]
            (3.0, 5.0, 1.0),  # n=365 WR=37.3% CI=[32.5%,42.3%]
            (5.0, 999.0, 1.5),  # n=147 WR=49.0% CI=[41.0%,57.0%]
        ],
    },
    "INJUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=162 WR=21.0% CI=[15.4%,27.9%]
            (1.0, 1.5, 0.8),  # n=591 WR=34.3% CI=[30.6%,38.3%]
            (1.5, 2.0, 0.8),  # n=534 WR=33.5% CI=[29.6%,37.6%]
            (2.0, 3.0, 0.8),  # n=802 WR=31.4% CI=[28.3%,34.7%]
            (3.0, 5.0, 1.2),  # n=645 WR=40.0% CI=[36.3%,43.8%]
            (5.0, 999.0, 0.8),  # n=141 WR=31.2% CI=[24.1%,39.3%]
        ],
    },
    "ALGOUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=272 WR=18.8% CI=[14.6%,23.8%]
            (1.0, 1.5, 0.8),  # n=523 WR=34.6% CI=[30.7%,38.8%]
            (1.5, 2.0, 1.2),  # n=560 WR=42.9% CI=[38.8%,47.0%]
            (2.0, 3.0, 1.0),  # n=589 WR=36.2% CI=[32.4%,40.1%]
            (3.0, 5.0, 1.2),  # n=447 WR=40.5% CI=[36.0%,45.1%]
            (5.0, 999.0, 0.8),  # n=113 WR=31.0% CI=[23.2%,40.0%]
        ],
    },
    "AAVEUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=291 WR=23.7% CI=[19.2%,28.9%]
            (1.0, 1.5, 1.0),  # n=658 WR=35.1% CI=[31.6%,38.8%]
            (1.5, 2.0, 0.5),  # n=506 WR=29.8% CI=[26.0%,34.0%]
            (2.0, 3.0, 1.2),  # n=630 WR=41.4% CI=[37.6%,45.3%]
            (3.0, 5.0, 1.5),  # n=393 WR=45.8% CI=[40.9%,50.7%]
            (5.0, 999.0, 1.2),  # n=143 WR=46.2% CI=[38.2%,54.3%]
        ],
    },
    "UNIUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=251 WR=27.1% CI=[22.0%,32.9%]
            (1.0, 1.5, 0.8),  # n=645 WR=31.0% CI=[27.6%,34.7%]
            (1.5, 2.0, 0.8),  # n=531 WR=31.8% CI=[28.0%,35.9%]
            (2.0, 3.0, 1.2),  # n=587 WR=44.6% CI=[40.7%,48.7%]
            (3.0, 5.0, 1.0),  # n=350 WR=39.4% CI=[34.5%,44.6%]
            (5.0, 999.0, 1.0),  # n=164 WR=39.0% CI=[31.9%,46.7%]
        ],
    },
    "DOGEUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=206 WR=40.8% CI=[34.3%,47.6%]
            (1.0, 1.5, 0.5),  # n=406 WR=26.6% CI=[22.5%,31.1%]
            (1.5, 2.0, 0.8),  # n=439 WR=32.1% CI=[27.9%,36.6%]
            (2.0, 3.0, 1.0),  # n=425 WR=36.0% CI=[31.6%,40.7%]
            (3.0, 5.0, 1.2),  # n=364 WR=42.0% CI=[37.1%,47.2%]
            (5.0, 999.0, 1.5),  # n=200 WR=52.0% CI=[45.1%,58.8%]
        ],
    },
}

FVG_SIZE_MAP: dict[str, float] = {
    "BTCUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "BNBUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "SOLUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "AVAXUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "LINKUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "XRPUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "ATOMUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "ADAUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "APTUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "DOTUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "NEARUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "ETHUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "SUIUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "OPUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "ARBUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "INJUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "ALGOUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "AAVEUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "UNIUSDT": 0.0,  # TODO: futures ATR bazli hesapla
    "DOGEUSDT": 0.0,  # TODO: futures ATR bazli hesapla
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
