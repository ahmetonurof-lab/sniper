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
RISK_PER_TRADE = 0.001
LEVERAGE = 5
LOG_LEVEL = "INFO"

# ── Per-symbol risk map (primary entry / retrade) ─────────────
SYMBOL_RISK_MAP = {
    "BTCUSDT": {"primary": 0.012, "retrade": 0.010},
    "ETHUSDT": {"primary": 0.010, "retrade": 0.010},
    "BNBUSDT": {"primary": 0.010, "retrade": 0.010},
    "SOLUSDT": {"primary": 0.010, "retrade": 0.010},
    "AVAXUSDT": {"primary": 0.015, "retrade": 0.010},
    "LINKUSDT": {"primary": 0.010, "retrade": 0.008},
    "XRPUSDT": {"primary": 0.010, "retrade": 0.010},
    "ATOMUSDT": {"primary": 0.010, "retrade": 0.010},
    "ADAUSDT": {"primary": 0.010, "retrade": 0.010},
    "SUIUSDT": {"primary": 0.010, "retrade": 0.010},
    "APTUSDT": {"primary": 0.010, "retrade": 0.010},
    "DOTUSDT": {"primary": 0.012, "retrade": 0.009},
    "NEARUSDT": {"primary": 0.012, "retrade": 0.010},
}

# ── Semboller (data dosyasi olanlar) ───────────────────────────
SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "XRPUSDT",
    "ATOMUSDT",
    "ADAUSDT",
    "SUIUSDT",
    "APTUSDT",
    "DOTUSDT",
    "NEARUSDT",
]

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

# ── Risk parametreleri ─────────────────────────────────────────
SL_ATR_MULT = 1.5
TP_RR = 2.0
FVG_BUFFER_MULT = 0.50

# ── Magic Numbers (Faz 1.2) ────────────────────────────────────
LHR_RETEST_PCT = 0.003  # LHR zone genişliği (eskiden LONDON_RETEST_PCT)
RETRADE_SWEEP_WINDOW = 500  # Retrade sweep arama penceresi (eskiden WINDOW_15M)
RETRADE_FVG_SIZE_MULT = 0.3  # Retrade min_fvg çarpanı
CBDR_DEAD_THRESHOLD_PCT = 0.5  # CBDR dead eşiği (% olarak)
ASIA_DEAD_THRESHOLD_PCT = 0.3  # Asya range dead eşiği (% olarak)
TRAIL_MIN_MOVE_MULT = 0.2  # Min trailing hareket çarpanı
BE_RISK_MULT = 1.0  # Break-even: 1R kârda SL->entry
BE_SPREAD_PTS = 0.0  # Break-even spread/komisyon offseti
ATR_TRAIL_MULT = 0.25  # Trailing buffer = ATR * 0.25
MIN_STOP_DIST_PCT = 0.006  # Min SL mesafesi (entry %0.6)
MAX_MARGIN_PCT = 0.20  # Tek pozisyonda max marjin (%20)
RETRADE_FVG_MAX_ATTEMPTS = 3  # Retrade FVG max deneme sayısı
MIN_RISK_DIST_ATR_MULT = 0.1  # Min risk mesafesi ATR çarpanı
MAX_SL_DIST_MULT = 2.0  # FVG bazlı SL max risk_pts çarpanı (aşarsa fallback)
DEFAULT_ATR_FALLBACK_PCT = 0.0001  # Varsayılan ATR fallback (%)
LHR_RISK_ATR_MULT = 1.0  # LHR risk ATR çarpanı
CBDR_SWEEP_ATR_TOLERANCE_MULT = 0.5  # CBDR sweep toleransı ATR çarpanı
CBDR_SWEEP_DEFAULT_TOLERANCE = 10.0  # CBDR sweep varsayılan tolerans (ATR=0 ise)
FVG_BUFFER_MIN_FACTOR = 0.10  # FVG buffer minimum çarpanı (fvg.size * factor)

# ── Binance API ────────────────────────────────────────────────
BINANCE_API_KEY = os.getenv("TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")
IS_TESTNET = os.getenv("TESTNET", "True").lower() == "true"
