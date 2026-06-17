# config.py — NEXUS V4 (Production-Ready)
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

IS_TESTNET = os.getenv("TESTNET", "True").lower() == "true"

# ── Backtest zaman aralığı ──────────────────────────────
BACKTEST_START = datetime(2025, 1, 1)
BACKTEST_END = datetime(2025, 8, 31)

# ── Başlangıç bakiyesi ──────────────────────────────────
INITIAL_BALANCE = 10000.0
LEVERAGE = 20

# ── Risk parametreleri ───────────────────────────────────
RISK_PER_TRADE = 0.01
MIN_RR = 0.0
MIN_NET_RR = 1.5
DEFAULT_RR = 2.0
TAKER_FEE = 0.0004
SPREAD_PCT = 0.0001

# ── Slippage Modeli ──────────────────────────────────────
SLIPPAGE_ENTRY = 0.0002
SLIPPAGE_EXIT = 0.0002
SLIPPAGE_TOTAL = SLIPPAGE_ENTRY + SLIPPAGE_EXIT

# ── Momentum Filtresi (CHoCH kalite) ─────────────────────
CHoCH_MIN_BODY_RATIO = 1.0
CHoCH_ATR_OVERSHOOT = 0.2
CHoCH_ATR_PERIOD = 14
CHoCH_PIVOT_ADX_THRESHOLD = 35.0
CHOCH_BREAK_WINDOW = 15

MAX_SETUP_WAIT_HOURS: float = 8.0

# ── CHoCH Maksimum Yaş (Saat) ──────────────────────────
CHOCH_MAX_AGE_HOURS = 8

# ── ADX / DI Filtresi ────────────────────────────────────────
D1_ADX_THRESHOLD = 20
ADX_THRESHOLD = 20.0

# ── ADX > 35 TP Daraltma Kuralı ──────────────────────────
ADX_HIGH_TP_THRESHOLD = 35.0
DI_MARGIN = 0.0
EMA_PERIOD = 200

# ── H4 Market Structure ──────────────────────────────────
H4_SWING_LEFT = 2
H4_SWING_RIGHT = 2
H4_SWING_LOOKBACK = 120

# ── HTF Bias ─────────────────────────────────────────────
D1_BOS_LOOKBACK = 25
H4_BOS_LOOKBACK = 50
HTF_BIAS_SFP_N = 1

# ── FVG Kalite Skoru ─────────────────────────────────────
FVG_SCORE_THRESHOLD = 0.40
FVG_SCORE_THRESHOLD_IMPULSIVE = 0.35
FVG_IMPULSIVE_ADX_THRESHOLD = 25.0
FVG_IMPULSIVE_DISPLACEMENT_MIN: float = 0.45

# ── Minimum FVG Boyutu ───────────────────────────────────
MIN_FVG_SIZE = 0.0001

# ── Missed FVG Parametreleri ─────────────────────────────
MISSED_FVG_ATR_MULT: float = 0.75
POI_ATR_BUFFER: float = 0.3

# ── FVG Penetration Trade Zone ───────────────────────────
FVG_PENETRATION_MIN: float = 0.15
FVG_PENETRATION_MID: float = 0.30
FVG_PENETRATION_MAX: float = 0.70

# ── Adaptive LTF Gating ─────────────────────────────────
ADAPTIVE_LTF_ENABLE: bool = True

# ── WAIT_CONFIRM time-box + partial sizing ──────────────
WAIT_CONFIRM_TIMEBOX_MIN: int = 3
PARTIAL_RISK_SCALE: float = 0.40

# ── Entry order type variant ─────────────────────────────
ENTRY_ORDER_TYPE: str = "MARKET"
ENTRY_STOP_OFFSET_PCT: float = 0.0005

# ── Breakeven Logging ────────────────────────────────────
BREAKEVEN_LOG_ENABLED = True

# ── Kademeli Stop ────────────────────────────────────────
BREAKEVEN_R = 1.0
TRAILING_ACTIVATE_R = 2.0
TRAILING_STEP_RATIO = 0.25

# ── Relax Filtresi ───────────────────────────────────────
FVG_RELAX_THRESHOLD = 0.25
FVG_RELAX_THRESHOLD_IMPULSIVE = 0.20
FVG_RELAX_AFTER_BARS = 5

# ── Semboller ────────────────────────────────────────────
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT",
    "SOLUSDT", "DOTUSDT", "DOGEUSDT", "AVAXUSDT",
    "MATICUSDT", "LTCUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT",
    "XLMUSDT", "VETUSDT", "FTMUSDT", "TRXUSDT", "ALGOUSDT",
]

COIN_LIST = SYMBOLS

# ── Veri klasörü ─────────────────────────────────────────
DATA_DIR = "data"
OUTPUT_DIR = "output"

# ── Timeframe tanımları ──────────────────────────────────
LTF_TF = "1m"

# ── Bar sayıları ─────────────────────────────────────────
D1_BARS = 150
H4_BARS = 300
H1_BARS = 200
M15_BARS = 500
M1_BARS = 500
FVG_IMPULSIVE_LOW_DISP_CAP = 0.45

# ── FVG Maksimum Yaş (Bar) ──────────────────────────────
FVG_MAX_AGE_BARS = 32

# ── Sweep Filtreleri ─────────────────────────────────────
SWEEP_SWING_STRENGTH = 2
SWEEP_15M_STRENGTH: int = 1

# ── Warm-up ──────────────────────────────────────────────
WARMUP_D1_BARS = 110

# ── Log seviyesi ─────────────────────────────────────────
LOG_LEVEL = "INFO"

# ── Seans zaman dilimleri ───────────────────────────────
ASIA_SESSION_START = "00:00"
ASIA_SESSION_END = "09:00"
LONDON_SESSION_START = "09:00"
LONDON_SESSION_END = "17:00"
NEWYORK_SESSION_START = "17:00"
NEWYORK_SESSION_END = "22:00"

# ── Binance API ─────────────────────────────────────────
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# ── ATR Konfigürasyonu (Dinamik) ────────────────────────
DEFAULT_ATR: float = 100.0
ATR_MAP: dict[str, float] = {
    "BTCUSDT": 600.0, "ETHUSDT": 30.0, "SOLUSDT": 3.0, "BNBUSDT": 8.0,
    "AVAXUSDT": 0.5, "LINKUSDT": 0.4, "SUIUSDT": 0.12, "XRPUSDT": 0.02,
}
