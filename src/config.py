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

# ── ATR-bazlı dinamik FVG eşiği (statik FVG_SIZE_MAP yerine) ──
# MULT scan 2026-07-03: 195 run (0.02-0.30). Best: 0.06 (en sağlam orta nokta)
FVG_MIN_SIZE_ATR_MULT = 0.06

# ── Risk parametreleri ─────────────────────────────────────────
SL_ATR_MULT = 1.5
TP_RR = 2.0
FVG_BUFFER_MULT = 0.50
EARLY_LONDON_RISK_MULT = 1.5  # 02-08 UTC risk carpani (Altin Oran)

# ── Magic Numbers (Faz 1.2) ────────────────────────────────────
CBDR_DEAD_THRESHOLD_PCT = 0.5  # CBDR dead eşiği (% olarak)
ASIA_DEAD_THRESHOLD_PCT = 0.3  # Asya range dead eşiği (% olarak)
TRAIL_MIN_MOVE_MULT = 0.2  # Min trailing hareket çarpanı
BE_RISK_MULT = 1.0  # Break-even: 1R kârda SL->entry
BE_SPREAD_PTS = 0.0  # Break-even spread/komisyon offseti
ATR_TRAIL_MULT = 0.25  # Trailing buffer = ATR * 0.25
MIN_STOP_DIST_PCT = 0.006  # Min SL mesafesi (entry %0.6)
MAX_MARGIN_PCT = 0.20  # Tek pozisyonda max marjin (%20)
MIN_RISK_DIST_ATR_MULT = 0.1  # Min risk mesafesi ATR çarpanı
MAX_SL_DIST_MULT = 2.0  # FVG bazlı SL max risk_pts çarpanı (aşarsa fallback)
DEFAULT_ATR_FALLBACK_PCT = 0.0001  # Varsayılan ATR fallback (%)
CBDR_SWEEP_ATR_TOLERANCE_MULT = 0.5  # CBDR sweep toleransı ATR çarpanı
CBDR_SWEEP_DEFAULT_TOLERANCE = 10.0  # CBDR sweep varsayılan tolerans (ATR=0 ise)
FVG_BUFFER_MIN_FACTOR = 0.10  # FVG buffer minimum çarpanı (fvg.size * factor)
FVG_WICK_RATIO_MAX = 0.75  # eskisi: 0.90 — %88 gibi mumları da artik yakalar

# ── Binance API ────────────────────────────────────────────────
BINANCE_API_KEY = os.getenv("TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")
IS_TESTNET = os.getenv("TESTNET", "True").lower() == "true"
