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
    "SUIUSDT",
    "OPUSDT",
    "ARBUSDT",
    "INJUSDT",
    "ALGOUSDT",
    "AAVEUSDT",
    "UNIUSDT",
    "DOGEUSDT",
    "TIAUSDT",
    "SEIUSDT",
    "ONDOUSDT",
    "PYTHUSDT",
    "RENDERUSDT",
    "ENAUSDT",
    "STRKUSDT",
    "GMXUSDT",
    "DYDXUSDT",
    "LDOUSDT",
]


# ── Global fallback: FVG.size / ATR eşiği (coin bazlı override yoksa) ──

FVG_MIN_SIZE_ATR_MULT = 0.06


# ── CBDR Risk Matrisi (coin bazli session + bucket carpani) ─────

SESSION_HOURS: dict[str, dict[str, int]] = {
    "DEFAULT": {"start": 22, "end": 2},
    "REAL_CBDR": {"start": 19, "end": 1},
    "ASIA_RANGE": {"start": 1, "end": 5},
}

# Carpan mantigi (v2 — composite score + PF gate, WR/BE+ tabanli degil):
#
#   Her bucket icin: PF(%41) + Sharpe(%27) + MaxDD(%17) + Confidence/n(%10) + PE(%5)
#   agirlikli skor hesaplanir (sabit mutlak referans noktalariyla, coin-ici
#   normalizasyon YOK). Skor -> multiplier:
#
#   1.5x = Score >= 0.80  (PF, Sharpe, MaxDD, n hepsi guclu)
#   1.2x = Score >= 0.65  (net avantaj)
#   1.0x = Score >= 0.50  (standart)
#   0.8x = Score >= 0.35  (defansif)
#   0.5x = Score >= 0.20  (zayif, edge kayboluyor)
#   0.0x = Score <  0.20  (ZEHIRLI / YASAKLI)
#
#   PF Gate (sert tavan, skordan bagimsiz):
#     PF < 1.3          -> tavan 0.0x
#     1.3 <= PF < 1.8   -> tavan 0.8x
#     1.8 <= PF < 2.5   -> tavan 1.2x
#     PF >= 2.5         -> tavan yok
#
#   Guvenlik kilidi: n < 100 -> nihai multiplier max 1.0x (skor/gate ne derse desin)
#
#   Uretim: bucket_data_extractor_v2.py + bucket_risk_engine.py
#   (analyzer_v5.py trade_records + daily_rows'undan, trailing dahil)
#   Detayli gerekce: reports/bucket_risk_report.md — [tarih: 2026-07-16]
#
#   ESKI (v1, WR/BE+ tabanli) yontem: config_backup_pre_v2.py'de arsivlendi.
#
# weekend_bonus: Cumartesi/Pazar gunleri cbdr_mult ek carpan
# weekend_mult:  kac kat uygulanacak (ornek: 1.5 = %50 fazla)

CBDR_RISK_MATRIX: dict[str, dict] = {
    "AAVEUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=481 PF=1.164 Sharpe=0.0722 MaxDD=16.297% Score=0.1375
            (1.0, 1.5, 0.8),  # n=1127 PF=2.409 Sharpe=0.2485 MaxDD=4.605% Score=0.4915
            (1.5, 2.0, 1.0),  # n=928 PF=2.503 Sharpe=0.2553 MaxDD=2.812% Score=0.5548
            (2.0, 3.0, 1.2),  # n=1057 PF=2.899 Sharpe=0.3195 MaxDD=1.496% Score=0.7276
            (3.0, 5.0, 1.5),  # n=698 PF=4.217 Sharpe=0.3756 MaxDD=2.132% Score=0.8579
            (5.0, 999.0, 1.5),  # n=243 PF=8.371 Sharpe=0.4324 MaxDD=1.244% Score=0.8771
        ],
    },
    "ADAUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=1049 PF=1.851 Sharpe=0.1868 MaxDD=3.662% Score=0.3827
            (1.5, 2.0, 1.0),  # n=909 PF=2.361 Sharpe=0.2496 MaxDD=2.393% Score=0.5545
            (2.0, 3.0, 1.2),  # n=1037 PF=3.241 Sharpe=0.3116 MaxDD=1.793% Score=0.754
            (3.0, 5.0, 1.0),  # n=577 PF=2.885 Sharpe=0.2945 MaxDD=2.312% Score=0.6245
            (5.0, 999.0, 1.5),  # n=169 PF=8.07 Sharpe=0.4676 MaxDD=1.513% Score=0.8605
        ],
    },
    "ALGOUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=451 PF=1.187 Sharpe=0.0385 MaxDD=22.056% Score=0.1113
            (1.0, 1.5, 0.8),  # n=872 PF=2.061 Sharpe=0.2186 MaxDD=2.93% Score=0.4568
            (1.5, 2.0, 1.2),  # n=886 PF=3.187 Sharpe=0.3467 MaxDD=1.824% Score=0.7644
            (2.0, 3.0, 1.2),  # n=1038 PF=3.308 Sharpe=0.2874 MaxDD=1.964% Score=0.7388
            (3.0, 5.0, 1.2),  # n=715 PF=3.734 Sharpe=0.2986 MaxDD=1.686% Score=0.7899
            (5.0, 999.0, 1.5),  # n=214 PF=4.618 Sharpe=0.4248 MaxDD=2.067% Score=0.826
        ],
    },
    "APTUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=140 PF=3.119 Sharpe=0.2798 MaxDD=2.716% Score=0.5786
            (1.0, 1.5, 0.5),  # n=577 PF=1.504 Sharpe=0.1444 MaxDD=3.624% Score=0.2643
            (1.5, 2.0, 0.8),  # n=859 PF=1.797 Sharpe=0.2074 MaxDD=2.47% Score=0.4362
            (2.0, 3.0, 1.0),  # n=1087 PF=2.669 Sharpe=0.2802 MaxDD=2.089% Score=0.6394
            (3.0, 5.0, 1.5),  # n=812 PF=4.203 Sharpe=0.3714 MaxDD=0.824% Score=0.9321
            (5.0, 999.0, 1.5),  # n=360 PF=4.119 Sharpe=0.375 MaxDD=2.154% Score=0.8193
        ],
    },
    "ARBUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=373 PF=1.482 Sharpe=0.1408 MaxDD=4.308% Score=0.22
            (1.0, 1.5, 1.0),  # n=1185 PF=2.094 Sharpe=0.2382 MaxDD=2.704% Score=0.5036
            (1.5, 2.0, 1.2),  # n=888 PF=2.955 Sharpe=0.303 MaxDD=1.36% Score=0.7251
            (2.0, 3.0, 1.2),  # n=1247 PF=3.135 Sharpe=0.3001 MaxDD=2.185% Score=0.7161
            (3.0, 5.0, 1.5),  # n=735 PF=3.852 Sharpe=0.3452 MaxDD=1.41% Score=0.8541
            (5.0, 999.0, 1.5),  # n=241 PF=11.726 Sharpe=0.3327 MaxDD=1.392% Score=0.8342
        ],
    },
    "ATOMUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=997 PF=1.879 Sharpe=0.1572 MaxDD=3.721% Score=0.3654
            (1.0, 1.5, 0.5),  # n=1177 PF=1.566 Sharpe=0.1497 MaxDD=5.059% Score=0.3008
            (1.5, 2.0, 1.2),  # n=1053 PF=2.791 Sharpe=0.2842 MaxDD=1.641% Score=0.6817
            (2.0, 3.0, 1.2),  # n=872 PF=3.114 Sharpe=0.2869 MaxDD=1.824% Score=0.7049
            (3.0, 5.0, 1.5),  # n=416 PF=4.343 Sharpe=0.3553 MaxDD=1.85% Score=0.8334
            (5.0, 999.0, 1.0),  # n=95 PF=4.888 Sharpe=0.3795 MaxDD=2.087% Score=0.8142
        ],
    },
    "AVAXUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 1.0),  # n=749 PF=2.345 Sharpe=0.2755 MaxDD=3.031% Score=0.5223
            (1.5, 2.0, 0.8),  # n=898 PF=1.643 Sharpe=0.1659 MaxDD=3.235% Score=0.3531
            (2.0, 3.0, 1.0),  # n=1000 PF=3.004 Sharpe=0.281 MaxDD=2.814% Score=0.648
            (3.0, 5.0, 1.0),  # n=717 PF=2.74 Sharpe=0.2969 MaxDD=1.933% Score=0.6424
            (5.0, 999.0, 1.2),  # n=358 PF=4.345 Sharpe=0.2832 MaxDD=3.172% Score=0.7055
        ],
    },
    "BNBUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=1216 PF=0.913 Sharpe=-0.034 MaxDD=42.501% Score=0.1246
            (1.0, 1.5, 0.8),  # n=1017 PF=2.079 Sharpe=0.2199 MaxDD=4.275% Score=0.433
            (1.5, 2.0, 0.8),  # n=484 PF=2.528 Sharpe=0.258 MaxDD=4.112% Score=0.467
            (2.0, 3.0, 1.2),  # n=571 PF=3.483 Sharpe=0.3259 MaxDD=3.082% Score=0.6965
            (3.0, 5.0, 1.5),  # n=292 PF=5.934 Sharpe=0.4831 MaxDD=1.605% Score=0.8707
            (5.0, 999.0, 1.0),  # n=51 PF=7.157 Sharpe=0.6395 MaxDD=1.376% Score=0.8626
        ],
    },
    "DOGEUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=712 PF=1.948 Sharpe=0.1942 MaxDD=3.435% Score=0.387
            (1.5, 2.0, 0.5),  # n=699 PF=1.551 Sharpe=0.1589 MaxDD=2.894% Score=0.3282
            (2.0, 3.0, 1.0),  # n=824 PF=2.298 Sharpe=0.2556 MaxDD=2.677% Score=0.5232
            (3.0, 5.0, 1.5),  # n=696 PF=5.227 Sharpe=0.416 MaxDD=1.405% Score=0.9141
            (5.0, 999.0, 1.5),  # n=355 PF=9.562 Sharpe=0.4433 MaxDD=1.291% Score=0.8971
        ],
    },
    "DOTUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=820 PF=1.621 Sharpe=0.1513 MaxDD=4.97% Score=0.295
            (1.0, 1.5, 1.0),  # n=1123 PF=2.296 Sharpe=0.2424 MaxDD=3.246% Score=0.5048
            (1.5, 2.0, 0.8),  # n=984 PF=2.166 Sharpe=0.2094 MaxDD=3.971% Score=0.4253
            (2.0, 3.0, 1.2),  # n=1093 PF=2.997 Sharpe=0.3118 MaxDD=1.743% Score=0.7244
            (3.0, 5.0, 1.2),  # n=475 PF=3.548 Sharpe=0.2879 MaxDD=1.855% Score=0.7181
            (5.0, 999.0, 1.2),  # n=188 PF=4.992 Sharpe=0.4262 MaxDD=4.311% Score=0.7288
        ],
    },
    "DYDXUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=360 PF=2.846 Sharpe=0.2655 MaxDD=5.842% Score=0.5082
            (1.0, 1.5, 1.0),  # n=806 PF=2.222 Sharpe=0.2737 MaxDD=2.509% Score=0.5322
            (1.5, 2.0, 1.0),  # n=973 PF=2.425 Sharpe=0.2511 MaxDD=1.897% Score=0.5906
            (2.0, 3.0, 1.2),  # n=1397 PF=2.981 Sharpe=0.3118 MaxDD=1.474% Score=0.7362
            (3.0, 5.0, 1.5),  # n=1023 PF=4.614 Sharpe=0.363 MaxDD=1.004% Score=0.9391
            (5.0, 999.0, 1.5),  # n=294 PF=8.09 Sharpe=0.4002 MaxDD=0.891% Score=0.9043
        ],
    },
    "ENAUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=135 PF=1.942 Sharpe=0.2728 MaxDD=2.503% Score=0.4225
            (1.0, 1.5, 0.8),  # n=390 PF=2.009 Sharpe=0.1962 MaxDD=2.665% Score=0.3926
            (1.5, 2.0, 0.8),  # n=600 PF=1.982 Sharpe=0.242 MaxDD=2.603% Score=0.4482
            (2.0, 3.0, 1.5),  # n=1079 PF=3.849 Sharpe=0.3667 MaxDD=1.208% Score=0.909
            (3.0, 5.0, 1.5),  # n=1068 PF=4.377 Sharpe=0.3601 MaxDD=0.792% Score=0.9444
            (5.0, 999.0, 1.5),  # n=580 PF=6.02 Sharpe=0.3634 MaxDD=0.739% Score=0.9102
        ],
    },
    "GMXUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=620 PF=2.088 Sharpe=0.2007 MaxDD=3.784% Score=0.3892
            (1.0, 1.5, 1.2),  # n=1218 PF=2.963 Sharpe=0.2874 MaxDD=1.872% Score=0.7008
            (1.5, 2.0, 1.5),  # n=919 PF=3.974 Sharpe=0.3216 MaxDD=0.986% Score=0.9005
            (2.0, 3.0, 1.5),  # n=1287 PF=4.298 Sharpe=0.3084 MaxDD=0.837% Score=0.9093
            (3.0, 5.0, 1.2),  # n=566 PF=4.158 Sharpe=0.2814 MaxDD=1.897% Score=0.7945
            (5.0, 999.0, 1.5),  # n=173 PF=8.911 Sharpe=0.4218 MaxDD=0.927% Score=0.8945
        ],
    },
    "INJUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=244 PF=1.045 Sharpe=0.0341 MaxDD=3.885% Score=0.0724
            (1.0, 1.5, 0.8),  # n=852 PF=1.804 Sharpe=0.188 MaxDD=3.713% Score=0.3547
            (1.5, 2.0, 1.0),  # n=825 PF=2.445 Sharpe=0.2691 MaxDD=2.699% Score=0.5525
            (2.0, 3.0, 1.0),  # n=1218 PF=1.998 Sharpe=0.2264 MaxDD=1.564% Score=0.5301
            (3.0, 5.0, 1.5),  # n=947 PF=3.951 Sharpe=0.397 MaxDD=1.049% Score=0.9428
            (5.0, 999.0, 1.5),  # n=272 PF=4.367 Sharpe=0.4036 MaxDD=1.931% Score=0.8366
        ],
    },
    "LDOUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=414 PF=1.935 Sharpe=0.2017 MaxDD=2.124% Score=0.4192
            (1.5, 2.0, 0.8),  # n=763 PF=1.909 Sharpe=0.187 MaxDD=4.783% Score=0.3507
            (2.0, 3.0, 1.5),  # n=1019 PF=4.023 Sharpe=0.3694 MaxDD=1.216% Score=0.931
            (3.0, 5.0, 1.5),  # n=953 PF=4.177 Sharpe=0.3518 MaxDD=1.535% Score=0.8981
            (5.0, 999.0, 1.5),  # n=629 PF=8.788 Sharpe=0.371 MaxDD=0.768% Score=0.9204
        ],
    },
    "LINKUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=424 PF=1.672 Sharpe=0.1817 MaxDD=4.054% Score=0.2889
            (1.0, 1.5, 0.5),  # n=801 PF=1.488 Sharpe=0.1332 MaxDD=3.203% Score=0.2993
            (1.5, 2.0, 0.8),  # n=943 PF=2.234 Sharpe=0.2759 MaxDD=4.644% Score=0.4794
            (2.0, 3.0, 1.0),  # n=1014 PF=2.525 Sharpe=0.2898 MaxDD=3.622% Score=0.5532
            (3.0, 5.0, 1.2),  # n=686 PF=3.108 Sharpe=0.3438 MaxDD=2.952% Score=0.669
            (5.0, 999.0, 1.5),  # n=226 PF=4.631 Sharpe=0.4543 MaxDD=2.242% Score=0.8283
        ],
    },
    "NEARUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=307 PF=2.491 Sharpe=0.23 MaxDD=3.712% Score=0.4337
            (1.0, 1.5, 0.5),  # n=942 PF=1.381 Sharpe=0.1211 MaxDD=5.095% Score=0.2463
            (1.5, 2.0, 1.0),  # n=900 PF=2.458 Sharpe=0.2453 MaxDD=3.417% Score=0.5059
            (2.0, 3.0, 1.0),  # n=1293 PF=2.437 Sharpe=0.2592 MaxDD=2.055% Score=0.5921
            (3.0, 5.0, 1.2),  # n=894 PF=3.189 Sharpe=0.2948 MaxDD=1.46% Score=0.7386
            (5.0, 999.0, 1.5),  # n=316 PF=5.869 Sharpe=0.3835 MaxDD=1.465% Score=0.8629
        ],
    },
    "ONDOUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=351 PF=2.107 Sharpe=0.1847 MaxDD=2.52% Score=0.3998
            (1.0, 1.5, 1.0),  # n=574 PF=2.955 Sharpe=0.2966 MaxDD=2.86% Score=0.6134
            (1.5, 2.0, 0.8),  # n=359 PF=2.5 Sharpe=0.2688 MaxDD=3.019% Score=0.4957
            (2.0, 3.0, 1.0),  # n=508 PF=3.121 Sharpe=0.2968 MaxDD=2.589% Score=0.6423
            (3.0, 5.0, 0.8),  # n=229 PF=2.535 Sharpe=0.2669 MaxDD=2.876% Score=0.4932
            (5.0, 999.0, 1.2),  # n=105 PF=3.418 Sharpe=0.4015 MaxDD=1.938% Score=0.7429
        ],
    },
    "OPUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=309 PF=2.026 Sharpe=0.2342 MaxDD=3.117% Score=0.4004
            (1.0, 1.5, 0.8),  # n=962 PF=1.972 Sharpe=0.2088 MaxDD=3.155% Score=0.4357
            (1.5, 2.0, 1.2),  # n=964 PF=3.237 Sharpe=0.3292 MaxDD=1.349% Score=0.7905
            (2.0, 3.0, 1.2),  # n=1073 PF=3.27 Sharpe=0.3295 MaxDD=1.428% Score=0.7889
            (3.0, 5.0, 1.2),  # n=989 PF=2.822 Sharpe=0.2753 MaxDD=1.694% Score=0.6751
            (5.0, 999.0, 1.5),  # n=215 PF=5.421 Sharpe=0.439 MaxDD=1.365% Score=0.8659
        ],
    },
    "PYTHUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=140 PF=1.899 Sharpe=0.2281 MaxDD=3.325% Score=0.3486
            (1.0, 1.5, 1.2),  # n=600 PF=3.677 Sharpe=0.3175 MaxDD=2.031% Score=0.7741
            (1.5, 2.0, 1.5),  # n=670 PF=3.819 Sharpe=0.3459 MaxDD=2.055% Score=0.8233
            (2.0, 3.0, 1.2),  # n=1409 PF=3.373 Sharpe=0.3149 MaxDD=1.451% Score=0.7941
            (3.0, 5.0, 1.5),  # n=863 PF=4.289 Sharpe=0.3789 MaxDD=1.318% Score=0.9186
            (5.0, 999.0, 1.5),  # n=388 PF=7.041 Sharpe=0.3974 MaxDD=0.955% Score=0.9087
        ],
    },
    "RENDERUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=243 PF=2.152 Sharpe=0.2369 MaxDD=4.505% Score=0.3737
            (1.0, 1.5, 0.5),  # n=500 PF=1.849 Sharpe=0.1838 MaxDD=3.796% Score=0.3207
            (1.5, 2.0, 1.0),  # n=752 PF=2.601 Sharpe=0.2625 MaxDD=2.845% Score=0.5599
            (2.0, 3.0, 1.2),  # n=998 PF=3.209 Sharpe=0.3407 MaxDD=1.324% Score=0.7965
            (3.0, 5.0, 1.5),  # n=772 PF=5.019 Sharpe=0.4109 MaxDD=1.143% Score=0.9353
            (5.0, 999.0, 1.5),  # n=261 PF=7.198 Sharpe=0.3526 MaxDD=1.096% Score=0.8596
        ],
    },
    "SEIUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=120 PF=2.33 Sharpe=0.2662 MaxDD=3.393% Score=0.4391
            (1.0, 1.5, 0.5),  # n=500 PF=1.833 Sharpe=0.2063 MaxDD=3.902% Score=0.3359
            (1.5, 2.0, 0.8),  # n=545 PF=2.271 Sharpe=0.233 MaxDD=2.686% Score=0.4774
            (2.0, 3.0, 1.5),  # n=1050 PF=4.057 Sharpe=0.3403 MaxDD=1.022% Score=0.922
            (3.0, 5.0, 1.5),  # n=1038 PF=4.022 Sharpe=0.3822 MaxDD=1.211% Score=0.9405
            (5.0, 999.0, 1.5),  # n=493 PF=6.835 Sharpe=0.4146 MaxDD=1.064% Score=0.9114
        ],
    },
    "SOLUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.5),  # n=561 PF=1.409 Sharpe=0.132 MaxDD=4.969% Score=0.2218
            (1.5, 2.0, 0.8),  # n=767 PF=1.797 Sharpe=0.1846 MaxDD=3.032% Score=0.3808
            (2.0, 3.0, 1.0),  # n=1009 PF=2.374 Sharpe=0.2666 MaxDD=2.737% Score=0.5609
            (3.0, 5.0, 1.5),  # n=698 PF=4.371 Sharpe=0.3674 MaxDD=1.136% Score=0.9024
            (5.0, 999.0, 1.5),  # n=238 PF=4.046 Sharpe=0.3423 MaxDD=1.788% Score=0.8058
        ],
    },
    "STRKUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=251 PF=2.286 Sharpe=0.2583 MaxDD=2.996% Score=0.4496
            (1.5, 2.0, 0.8),  # n=636 PF=2.375 Sharpe=0.2335 MaxDD=3.038% Score=0.4804
            (2.0, 3.0, 1.2),  # n=1122 PF=3.266 Sharpe=0.309 MaxDD=1.444% Score=0.7765
            (3.0, 5.0, 1.5),  # n=994 PF=3.422 Sharpe=0.3626 MaxDD=1.669% Score=0.8205
            (5.0, 999.0, 1.5),  # n=619 PF=6.74 Sharpe=0.3801 MaxDD=1.193% Score=0.9064
        ],
    },
    "SUIUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 (bos) - fix: extractor atlamisti, eklendi
            (1.0, 1.5, 0.8),  # n=809 PF=2.045 Sharpe=0.1699 MaxDD=2.794% Score=0.4183
            (1.5, 2.0, 1.0),  # n=720 PF=2.215 Sharpe=0.248 MaxDD=1.808% Score=0.5358
            (2.0, 3.0, 1.0),  # n=1117 PF=2.126 Sharpe=0.2234 MaxDD=2.276% Score=0.5082
            (3.0, 5.0, 1.2),  # n=936 PF=2.887 Sharpe=0.2993 MaxDD=2.24% Score=0.6676
            (5.0, 999.0, 1.5),  # n=409 PF=6.321 Sharpe=0.3822 MaxDD=1.044% Score=0.8983
        ],
    },
    "TIAUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=44 PF=3.054 Sharpe=0.3182 MaxDD=3.251% Score=0.5567
            (1.0, 1.5, 0.8),  # n=317 PF=2.091 Sharpe=0.2453 MaxDD=2.816% Score=0.4329
            (1.5, 2.0, 1.2),  # n=538 PF=3.178 Sharpe=0.3502 MaxDD=1.868% Score=0.7257
            (2.0, 3.0, 1.2),  # n=1208 PF=3.099 Sharpe=0.3201 MaxDD=1.464% Score=0.7562
            (3.0, 5.0, 1.5),  # n=1075 PF=3.996 Sharpe=0.3774 MaxDD=1.328% Score=0.9293
            (5.0, 999.0, 1.5),  # n=800 PF=6.544 Sharpe=0.436 MaxDD=0.847% Score=0.9554
        ],
    },
    "UNIUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.5),  # n=1033 PF=1.653 Sharpe=0.1542 MaxDD=4.891% Score=0.3168
            (1.5, 2.0, 1.0),  # n=921 PF=2.463 Sharpe=0.2575 MaxDD=1.746% Score=0.6017
            (2.0, 3.0, 1.2),  # n=1196 PF=3.422 Sharpe=0.3386 MaxDD=1.849% Score=0.7977
            (3.0, 5.0, 1.2),  # n=612 PF=2.823 Sharpe=0.3415 MaxDD=1.914% Score=0.6725
            (5.0, 999.0, 1.2),  # n=299 PF=3.509 Sharpe=0.3188 MaxDD=2.988% Score=0.6675
        ],
    },
    "XRPUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=1061 PF=0.972 Sharpe=-0.0134 MaxDD=27.159% Score=0.1221
            (1.0, 1.5, 0.5),  # n=945 PF=1.593 Sharpe=0.1528 MaxDD=5.166% Score=0.3053
            (1.5, 2.0, 0.0),  # n=569 PF=1.289 Sharpe=0.0825 MaxDD=9.536% Score=0.168
            (2.0, 3.0, 1.0),  # n=712 PF=3.016 Sharpe=0.2687 MaxDD=3.611% Score=0.5749
            (3.0, 5.0, 1.2),  # n=429 PF=3.806 Sharpe=0.347 MaxDD=2.182% Score=0.7829
            (5.0, 999.0, 1.2),  # n=159 PF=3.716 Sharpe=0.4021 MaxDD=2.696% Score=0.7565
        ],
    },
}

# ── Tek FVG eşik haritası (coin bazlı FVG.size / ATR) ──
# on_sweep_confirmed() + trailing buradan okur.
# Fallback: FVG_MIN_SIZE_ATR_MULT.

FVG_SIZE_MAP: dict[str, float] = {
    "AAVEUSDT": 0.030,
    "ADAUSDT": 0.050,
    "ALGOUSDT": 0.100,
    "APTUSDT": 0.130,
    "ARBUSDT": 0.040,
    "ATOMUSDT": 0.080,
    "AVAXUSDT": 0.080,
    "BNBUSDT": 0.110,
    "DOGEUSDT": 0.100,
    "DOTUSDT": 0.060,
    "DYDXUSDT": 0.040,
    "ENAUSDT": 0.020,
    "GMXUSDT": 0.020,
    "INJUSDT": 0.160,
    "LDOUSDT": 0.020,
    "LINKUSDT": 0.020,
    "NEARUSDT": 0.060,
    "ONDOUSDT": 0.040,
    "OPUSDT": 0.080,
    "PYTHUSDT": 0.130,
    "RENDERUSDT": 0.070,
    "SEIUSDT": 0.070,
    "SOLUSDT": 0.060,
    "STRKUSDT": 0.060,
    "SUIUSDT": 0.050,
    "TIAUSDT": 0.070,
    "UNIUSDT": 0.060,
    "XRPUSDT": 0.060,
}


# ── Risk parametreleri ─────────────────────────────────────────

SL_ATR_MULT = 1.5

TP_RR = 2.0

FVG_BUFFER_MULT = 0.50

EARLY_LONDON_RISK_MULT = 1.5  # 02-08 UTC risk carpani (Altin Oran)


# ── Dinamik FVG filtreleri (ATR bazli) ────────────────────────

MIN_REL_FVG_THRESHOLD = 0.40


# ── FVG zaman asimi (expiry) ───────────────────────────────────

GLOBAL_FVG_EXPIRY_BARS = 45


# ── Magic Numbers (Faz 1.2) ────────────────────────────────────

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
