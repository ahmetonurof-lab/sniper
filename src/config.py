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
RISK_PER_TRADE = 0.01
LEVERAGE = 20
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

# ── Risk parametreleri ─────────────────────────────────────────
SL_ATR_MULT = 1.5
TP_RR = 2.0
FVG_BUFFER_MULT = 0.25

# ── Binance API ────────────────────────────────────────────────
BINANCE_API_KEY = os.getenv("TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")
IS_TESTNET = os.getenv("TESTNET", "True").lower() == "true"
