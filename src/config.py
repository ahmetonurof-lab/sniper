# config.py — sniper paper trade
import os
from dotenv import load_dotenv
load_dotenv()

INITIAL_BALANCE = 10000.0
RISK_PER_TRADE = 0.01
LEVERAGE = 20
LOG_LEVEL = "INFO"

# ── Semboller (data dosyasi olanlar) ───────────────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT", "XRPUSDT"]

# ── Coin bazli FVG esikleri ────────────────────────────────────
FVG_SIZE_MAP: dict[str, float] = {
    "BTCUSDT": 10.0,
    "ETHUSDT": 0.5,
    "BNBUSDT": 0.5,
    "SOLUSDT": 0.05,
    "AVAXUSDT": 0.03,
    "LINKUSDT": 0.02,
    "XRPUSDT": 0.005,
}

# ── Risk parametreleri ─────────────────────────────────────────
SL_ATR_MULT = 1.5
TP_RR = 2.0
FVG_BUFFER_MULT = 0.25

# ── Binance API ────────────────────────────────────────────────
BINANCE_API_KEY = os.getenv("TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")
IS_TESTNET = os.getenv("TESTNET", "True").lower() == "true"
