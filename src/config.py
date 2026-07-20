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

# Carpan mantigi (v2 — composite score + PF gate, TP%/PTrail% tabanli degil):
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
#   ESKI (v1, TP%/PTrail% tabanli) yontem: config_backup_pre_v2.py'de arsivlendi.
#
# weekend_bonus: Cumartesi/Pazar gunleri cbdr_mult ek carpan
# weekend_mult:  kac kat uygulanacak (ornek: 1.5 = %50 fazla)

CBDR_RISK_MATRIX: dict[str, dict] = {
    "AAVEUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=1120 PF=2.393 Sharpe=0.2492 MaxDD=4.185% Score=0.4903
            (1.5, 2.0, 1.0),  # n=879 PF=2.574 Sharpe=0.2578 MaxDD=2.639% Score=0.5709
            (2.0, 3.0, 1.0),  # n=998 PF=2.733 Sharpe=0.2949 MaxDD=2.703% Score=0.6274
            (3.0, 5.0, 1.5),  # n=691 PF=4.54 Sharpe=0.394 MaxDD=1.596% Score=0.8974
            (5.0, 999.0, 1.5),  # n=256 PF=7.546 Sharpe=0.4525 MaxDD=1.279% Score=0.8766
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
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=884 PF=1.928 Sharpe=0.1995 MaxDD=3.115% Score=0.4169
            (1.5, 2.0, 1.2),  # n=896 PF=3.229 Sharpe=0.3461 MaxDD=1.772% Score=0.773
            (2.0, 3.0, 1.2),  # n=1049 PF=3.305 Sharpe=0.2877 MaxDD=2.065% Score=0.7333
            (3.0, 5.0, 1.5),  # n=729 PF=3.856 Sharpe=0.2998 MaxDD=1.372% Score=0.8244
            (5.0, 999.0, 1.5),  # n=222 PF=5.425 Sharpe=0.3797 MaxDD=1.824% Score=0.8266
        ],
    },
    "APTUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=143 PF=2.97 Sharpe=0.2684 MaxDD=2.73% Score=0.5483
            (1.0, 1.5, 0.5),  # n=588 PF=1.48 Sharpe=0.1399 MaxDD=4.27% Score=0.2414
            (1.5, 2.0, 0.8),  # n=862 PF=1.901 Sharpe=0.2164 MaxDD=2.079% Score=0.4764
            (2.0, 3.0, 1.0),  # n=1086 PF=2.655 Sharpe=0.2791 MaxDD=2.115% Score=0.6352
            (3.0, 5.0, 1.5),  # n=831 PF=4.312 Sharpe=0.3712 MaxDD=0.867% Score=0.9323
            (5.0, 999.0, 1.5),  # n=364 PF=4.031 Sharpe=0.3657 MaxDD=2.107% Score=0.8165
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
            (
                5.0,
                999.0,
                1.5,
            ),  # n=241 PF=11.726 Sharpe=0.3327 MaxDD=1.392% Score=0.8342
        ],
    },
    "ATOMUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=370 PF=1.384 Sharpe=0.1228 MaxDD=10.02% Score=0.1931
            (1.0, 1.5, 0.8),  # n=1088 PF=1.885 Sharpe=0.2009 MaxDD=3.839% Score=0.3884
            (1.5, 2.0, 1.0),  # n=893 PF=2.356 Sharpe=0.2539 MaxDD=2.459% Score=0.5535
            (2.0, 3.0, 1.2),  # n=1011 PF=3.458 Sharpe=0.3057 MaxDD=1.429% Score=0.7991
            (3.0, 5.0, 1.5),  # n=715 PF=5.769 Sharpe=0.4157 MaxDD=1.576% Score=0.9142
            (5.0, 999.0, 1.0),  # n=166 PF=2.848 Sharpe=0.2979 MaxDD=2.563% Score=0.5627
        ],
    },
    "AVAXUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=1147 PF=1.762 Sharpe=0.1933 MaxDD=6.416% Score=0.357
            (1.5, 2.0, 0.8),  # n=870 PF=2.17 Sharpe=0.2259 MaxDD=2.745% Score=0.4829
            (2.0, 3.0, 1.5),  # n=1057 PF=3.902 Sharpe=0.3296 MaxDD=2.396% Score=0.8319
            (3.0, 5.0, 1.5),  # n=659 PF=4.137 Sharpe=0.3569 MaxDD=1.371% Score=0.8818
            (5.0, 999.0, 1.5),  # n=173 PF=5.806 Sharpe=0.4536 MaxDD=1.291% Score=0.8711
        ],
    },
    "BNBUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 1.0),  # n=1046 PF=2.32 Sharpe=0.2627 MaxDD=2.562% Score=0.568
            (1.5, 2.0, 0.8),  # n=492 PF=2.364 Sharpe=0.2577 MaxDD=4.198% Score=0.4455
            (2.0, 3.0, 1.5),  # n=603 PF=4.141 Sharpe=0.3487 MaxDD=2.189% Score=0.8345
            (3.0, 5.0, 1.5),  # n=285 PF=5.177 Sharpe=0.434 MaxDD=1.726% Score=0.8616
            (5.0, 999.0, 1.0),  # n=50 PF=7.427 Sharpe=0.6049 MaxDD=1.287% Score=0.8668
        ],
    },
    "DOGEUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=711 PF=2.047 Sharpe=0.2058 MaxDD=3.852% Score=0.3881
            (1.5, 2.0, 0.8),  # n=716 PF=1.628 Sharpe=0.1675 MaxDD=2.248% Score=0.3792
            (2.0, 3.0, 1.0),  # n=828 PF=2.478 Sharpe=0.2827 MaxDD=2.493% Score=0.5767
            (3.0, 5.0, 1.5),  # n=706 PF=5.2 Sharpe=0.4128 MaxDD=1.211% Score=0.9242
            (5.0, 999.0, 1.5),  # n=354 PF=9.273 Sharpe=0.4396 MaxDD=1.302% Score=0.8956
        ],
    },
    "DOTUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=304 PF=1.046 Sharpe=0.0263 MaxDD=5.574% Score=0.0755
            (1.0, 1.5, 0.5),  # n=822 PF=1.635 Sharpe=0.1676 MaxDD=6.172% Score=0.3061
            (1.5, 2.0, 1.0),  # n=834 PF=2.298 Sharpe=0.256 MaxDD=2.516% Score=0.5331
            (2.0, 3.0, 1.5),  # n=1093 PF=3.485 Sharpe=0.3299 MaxDD=1.025% Score=0.8402
            (3.0, 5.0, 1.0),  # n=710 PF=3.269 Sharpe=0.266 MaxDD=3.151% Score=0.6244
            (5.0, 999.0, 1.5),  # n=251 PF=7.627 Sharpe=0.497 MaxDD=1.939% Score=0.8514
        ],
    },
    "DYDXUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 1.0),  # n=360 PF=2.846 Sharpe=0.2655 MaxDD=5.842% Score=0.5082
            (1.0, 1.5, 1.0),  # n=806 PF=2.222 Sharpe=0.2737 MaxDD=2.784% Score=0.5188
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
            (1.0, 1.5, 0.5),  # n=390 PF=2.009 Sharpe=0.1962 MaxDD=3.752% Score=0.3398
            (1.5, 2.0, 0.8),  # n=600 PF=1.982 Sharpe=0.242 MaxDD=2.603% Score=0.4482
            (2.0, 3.0, 1.5),  # n=1079 PF=3.849 Sharpe=0.3667 MaxDD=1.208% Score=0.909
            (3.0, 5.0, 1.5),  # n=1068 PF=4.377 Sharpe=0.3601 MaxDD=0.792% Score=0.9444
            (5.0, 999.0, 1.5),  # n=580 PF=6.02 Sharpe=0.3634 MaxDD=0.739% Score=0.9102
        ],
    },
    "GMXUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=285 PF=2.004 Sharpe=0.1848 MaxDD=4.66% Score=0.3156
            (1.0, 1.5, 1.2),  # n=734 PF=3.257 Sharpe=0.2995 MaxDD=2.53% Score=0.6928
            (1.5, 2.0, 1.0),  # n=829 PF=2.975 Sharpe=0.2631 MaxDD=4.064% Score=0.5643
            (2.0, 3.0, 1.5),  # n=1248 PF=3.747 Sharpe=0.3088 MaxDD=1.172% Score=0.8601
            (3.0, 5.0, 1.5),  # n=775 PF=5.378 Sharpe=0.4111 MaxDD=0.752% Score=0.9542
            (
                5.0,
                999.0,
                1.5,
            ),  # n=295 PF=11.337 Sharpe=0.5316 MaxDD=1.263% Score=0.8924
        ],
    },
    "INJUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.0),  # n=438 PF=1.364 Sharpe=0.1227 MaxDD=4.506% Score=0.1953
            (1.5, 2.0, 1.0),  # n=749 PF=2.462 Sharpe=0.254 MaxDD=2.628% Score=0.5391
            (2.0, 3.0, 1.0),  # n=1066 PF=2.34 Sharpe=0.2755 MaxDD=2.372% Score=0.5758
            (3.0, 5.0, 1.2),  # n=1359 PF=3.167 Sharpe=0.3115 MaxDD=1.434% Score=0.7608
            (5.0, 999.0, 1.5),  # n=466 PF=6.215 Sharpe=0.4073 MaxDD=1.029% Score=0.9083
        ],
    },
    "LDOUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=414 PF=1.935 Sharpe=0.2017 MaxDD=2.98% Score=0.3776
            (1.5, 2.0, 0.8),  # n=763 PF=1.909 Sharpe=0.187 MaxDD=4.783% Score=0.3507
            (2.0, 3.0, 1.5),  # n=1019 PF=4.023 Sharpe=0.3694 MaxDD=1.216% Score=0.931
            (3.0, 5.0, 1.5),  # n=953 PF=4.177 Sharpe=0.3518 MaxDD=1.535% Score=0.8981
            (5.0, 999.0, 1.5),  # n=629 PF=8.788 Sharpe=0.371 MaxDD=0.768% Score=0.9204
        ],
    },
    "LINKUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=789 PF=1.069 Sharpe=0.0162 MaxDD=9.318% Score=0.1129
            (1.0, 1.5, 1.0),  # n=1072 PF=2.067 Sharpe=0.2382 MaxDD=2.568% Score=0.5064
            (1.5, 2.0, 1.2),  # n=974 PF=2.871 Sharpe=0.2908 MaxDD=2.017% Score=0.6778
            (2.0, 3.0, 1.2),  # n=1014 PF=3.199 Sharpe=0.3158 MaxDD=1.528% Score=0.7639
            (3.0, 5.0, 1.5),  # n=440 PF=3.883 Sharpe=0.3525 MaxDD=2.153% Score=0.8001
            (5.0, 999.0, 1.5),  # n=167 PF=6.172 Sharpe=0.3967 MaxDD=1.097% Score=0.8855
        ],
    },
    "NEARUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.5),  # n=107 PF=1.74 Sharpe=0.2078 MaxDD=5.849% Score=0.2789
            (1.0, 1.5, 0.0),  # n=464 PF=1.219 Sharpe=0.0841 MaxDD=3.321% Score=0.1845
            (1.5, 2.0, 1.0),  # n=701 PF=2.344 Sharpe=0.2458 MaxDD=2.529% Score=0.5153
            (2.0, 3.0, 1.0),  # n=1093 PF=2.679 Sharpe=0.2299 MaxDD=1.525% Score=0.6301
            (3.0, 5.0, 1.5),  # n=1107 PF=3.879 Sharpe=0.3599 MaxDD=1.074% Score=0.9129
            (5.0, 999.0, 1.5),  # n=508 PF=5.273 Sharpe=0.3584 MaxDD=1.154% Score=0.8698
        ],
    },
    "ONDOUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=351 PF=2.107 Sharpe=0.1847 MaxDD=3.492% Score=0.3526
            (1.0, 1.5, 1.0),  # n=574 PF=2.955 Sharpe=0.2966 MaxDD=2.86% Score=0.6134
            (1.5, 2.0, 0.8),  # n=359 PF=2.5 Sharpe=0.2688 MaxDD=3.019% Score=0.4957
            (2.0, 3.0, 1.0),  # n=508 PF=3.121 Sharpe=0.2968 MaxDD=2.589% Score=0.6423
            (3.0, 5.0, 0.8),  # n=229 PF=2.535 Sharpe=0.2669 MaxDD=2.876% Score=0.4932
            (5.0, 999.0, 1.2),  # n=105 PF=3.418 Sharpe=0.4015 MaxDD=1.938% Score=0.7429
        ],
    },
    "OPUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=139 PF=1.937 Sharpe=0.2378 MaxDD=2.077% Score=0.4227
            (1.0, 1.5, 0.8),  # n=494 PF=2.184 Sharpe=0.2434 MaxDD=3.289% Score=0.4392
            (1.5, 2.0, 0.8),  # n=753 PF=2.085 Sharpe=0.2339 MaxDD=3.511% Score=0.4313
            (2.0, 3.0, 1.2),  # n=1231 PF=3.139 Sharpe=0.3229 MaxDD=1.219% Score=0.7801
            (3.0, 5.0, 1.5),  # n=924 PF=3.858 Sharpe=0.3562 MaxDD=1.598% Score=0.8715
            (5.0, 999.0, 1.5),  # n=453 PF=4.641 Sharpe=0.3184 MaxDD=1.51% Score=0.8209
        ],
    },
    "PYTHUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=24 PF=1.776 Sharpe=0.1754 MaxDD=0.826% Score=0.4046
            (1.0, 1.5, 0.8),  # n=388 PF=2.563 Sharpe=0.2525 MaxDD=4.276% Score=0.4588
            (1.5, 2.0, 1.0),  # n=573 PF=2.616 Sharpe=0.2632 MaxDD=2.638% Score=0.5507
            (2.0, 3.0, 1.5),  # n=1108 PF=3.569 Sharpe=0.3185 MaxDD=1.102% Score=0.8411
            (3.0, 5.0, 1.5),  # n=1072 PF=5.314 Sharpe=0.4048 MaxDD=1.045% Score=0.9672
            (5.0, 999.0, 1.5),  # n=595 PF=9.723 Sharpe=0.3807 MaxDD=0.832% Score=0.9263
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
            (0.0, 1.0, 0.5),  # n=120 PF=1.815 Sharpe=0.1892 MaxDD=3.08% Score=0.3305
            (1.0, 1.5, 0.5),  # n=496 PF=1.794 Sharpe=0.2008 MaxDD=3.948% Score=0.3233
            (1.5, 2.0, 0.8),  # n=558 PF=2.148 Sharpe=0.2212 MaxDD=2.802% Score=0.4494
            (2.0, 3.0, 1.5),  # n=1082 PF=4.403 Sharpe=0.3554 MaxDD=0.907% Score=0.9396
            (3.0, 5.0, 1.5),  # n=1039 PF=3.906 Sharpe=0.3755 MaxDD=1.248% Score=0.9204
            (5.0, 999.0, 1.5),  # n=512 PF=7.971 Sharpe=0.4259 MaxDD=0.913% Score=0.9225
        ],
    },
    "SOLUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.5),  # n=570 PF=1.451 Sharpe=0.1382 MaxDD=4.582% Score=0.2339
            (1.5, 2.0, 0.8),  # n=775 PF=1.862 Sharpe=0.1958 MaxDD=3.419% Score=0.3809
            (2.0, 3.0, 1.0),  # n=1010 PF=2.415 Sharpe=0.2709 MaxDD=2.576% Score=0.5782
            (3.0, 5.0, 1.5),  # n=702 PF=4.571 Sharpe=0.3756 MaxDD=1.108% Score=0.9106
            (5.0, 999.0, 1.2),  # n=245 PF=3.755 Sharpe=0.3268 MaxDD=2.643% Score=0.7188
        ],
    },
    "STRKUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=253 PF=2.379 Sharpe=0.2628 MaxDD=2.936% Score=0.4704
            (1.5, 2.0, 0.8),  # n=637 PF=2.414 Sharpe=0.2377 MaxDD=3.013% Score=0.4903
            (2.0, 3.0, 1.2),  # n=1131 PF=3.289 Sharpe=0.3097 MaxDD=1.438% Score=0.7807
            (3.0, 5.0, 1.5),  # n=1006 PF=3.508 Sharpe=0.3698 MaxDD=1.622% Score=0.8409
            (5.0, 999.0, 1.5),  # n=629 PF=6.866 Sharpe=0.3718 MaxDD=0.908% Score=0.916
        ],
    },
    "SUIUSDT": {
        "session": "ASIA_RANGE",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.8),  # n=826 PF=2.113 Sharpe=0.1654 MaxDD=2.707% Score=0.4311
            (1.5, 2.0, 1.0),  # n=730 PF=2.312 Sharpe=0.2565 MaxDD=1.752% Score=0.56
            (2.0, 3.0, 1.0),  # n=1121 PF=2.135 Sharpe=0.2291 MaxDD=2.291% Score=0.5129
            (3.0, 5.0, 1.2),  # n=930 PF=2.94 Sharpe=0.2975 MaxDD=2.231% Score=0.6741
            (5.0, 999.0, 1.5),  # n=419 PF=6.255 Sharpe=0.3919 MaxDD=1.028% Score=0.9071
        ],
    },
    "TIAUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.8),  # n=46 PF=2.692 Sharpe=0.3059 MaxDD=3.824% Score=0.4675
            (1.0, 1.5, 0.8),  # n=310 PF=2.015 Sharpe=0.226 MaxDD=2.86% Score=0.4045
            (1.5, 2.0, 1.2),  # n=545 PF=3.383 Sharpe=0.3587 MaxDD=1.421% Score=0.7827
            (2.0, 3.0, 1.2),  # n=1212 PF=3.14 Sharpe=0.3192 MaxDD=1.265% Score=0.7704
            (3.0, 5.0, 1.5),  # n=1101 PF=4.024 Sharpe=0.3682 MaxDD=1.03% Score=0.9379
            (5.0, 999.0, 1.5),  # n=807 PF=7.088 Sharpe=0.4256 MaxDD=0.806% Score=0.9593
        ],
    },
    "UNIUSDT": {
        "session": "REAL_CBDR",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.5),  # n=689 PF=1.753 Sharpe=0.1725 MaxDD=4.657% Score=0.3118
            (1.5, 2.0, 1.0),  # n=814 PF=2.714 Sharpe=0.2958 MaxDD=1.949% Score=0.6465
            (2.0, 3.0, 1.2),  # n=1100 PF=2.837 Sharpe=0.2772 MaxDD=2.09% Score=0.6606
            (3.0, 5.0, 1.2),  # n=719 PF=3.676 Sharpe=0.3484 MaxDD=3.008% Score=0.7551
            (5.0, 999.0, 1.2),  # n=385 PF=3.373 Sharpe=0.3228 MaxDD=2.582% Score=0.6755
        ],
    },
    "XRPUSDT": {
        "session": "DEFAULT",
        "weekend_bonus": False,
        "weekend_mult": 1.0,
        "buckets": [
            (0.0, 1.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (1.0, 1.5, 0.5),  # n=1174 PF=1.629 Sharpe=0.162 MaxDD=3.457% Score=0.3467
            (1.5, 2.0, 0.0),  # n=0 PF=0.0 Sharpe=0.0 MaxDD=0.0% Score=0.0
            (2.0, 3.0, 1.0),  # n=694 PF=2.721 Sharpe=0.261 MaxDD=2.442% Score=0.5837
            (3.0, 5.0, 1.5),  # n=363 PF=5.489 Sharpe=0.4193 MaxDD=1.702% Score=0.8669
            (5.0, 999.0, 1.5),  # n=121 PF=3.961 Sharpe=0.4226 MaxDD=1.949% Score=0.8161
        ],
    },
}

# ── Tek FVG eşik haritası (coin bazlı FVG.size / ATR) ──
# on_sweep_confirmed() + trailing buradan okur.
# Fallback: FVG_MIN_SIZE_ATR_MULT.

FVG_SIZE_MAP: dict[str, float] = {
    "AAVEUSDT": 0.080,  # [DEFAULT] score=682
    "ADAUSDT": 0.050,  # [DEFAULT] score=434
    "ALGOUSDT": 0.080,  # [DEFAULT] score=617
    "APTUSDT": 0.110,  # [REAL_CBDR] score=560
    "ARBUSDT": 0.040,  # [DEFAULT] score=642
    "ATOMUSDT": 0.020,  # [REAL_CBDR] score=487
    "AVAXUSDT": 0.020,  # [DEFAULT] score=472
    "BNBUSDT": 0.020,  # [REAL_CBDR] score=474
    "DOGEUSDT": 0.080,  # [REAL_CBDR] score=859
    "DOTUSDT": 0.020,  # [REAL_CBDR] score=426
    "DYDXUSDT": 0.040,  # [ASIA_RANGE] score=723
    "ENAUSDT": 0.020,  # [DEFAULT] score=1160
    "GMXUSDT": 0.030,  # [REAL_CBDR] score=789
    "INJUSDT": 0.040,  # [REAL_CBDR] score=607
    "LDOUSDT": 0.020,  # [REAL_CBDR] score=1308
    "LINKUSDT": 0.020,  # [ASIA_RANGE] score=407
    "NEARUSDT": 0.020,  # [REAL_CBDR] score=674
    "ONDOUSDT": 0.040,  # [ASIA_RANGE] score=364
    "OPUSDT": 0.070,  # [REAL_CBDR] score=612
    "PYTHUSDT": 0.050,  # [REAL_CBDR] score=1323
    "RENDERUSDT": 0.070,  # [ASIA_RANGE] score=932
    "SEIUSDT": 0.020,  # [REAL_CBDR] score=1166
    "SOLUSDT": 0.030,  # [REAL_CBDR] score=466
    "STRKUSDT": 0.040,  # [REAL_CBDR] score=933
    "SUIUSDT": 0.020,  # [ASIA_RANGE] score=494
    "TIAUSDT": 0.020,  # [REAL_CBDR] score=1208
    "UNIUSDT": 0.020,  # [REAL_CBDR] score=489
    "XRPUSDT": 0.060,  # [DEFAULT] score=450
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

# Restart recovery sirasinda gercek ATR yoksa (bot yeni acildi, henuz bar
# birikmedi) kullanilan ACIL DURUM SL/TP mesafesi. DEFAULT_ATR_FALLBACK_PCT
# (0.01%) bu amac icin kullanilirsa SL/TP fiilen giris fiyatina yapisir ve
# Binance "immediately trigger" hatasiyla emri reddeder -> pozisyon
# korumasiz kalir. Bu yuzden ayri, gercekci bir yuzde kullaniyoruz.
RECOVERY_SL_FALLBACK_PCT = 0.02

CBDR_SWEEP_ATR_TOLERANCE_MULT = 0.5

CBDR_SWEEP_DEFAULT_TOLERANCE = 10.0

FVG_BUFFER_MIN_FACTOR = 0.10

FVG_WICK_RATIO_MAX = 0.75


# ── Binance API ────────────────────────────────────────────────

BINANCE_API_KEY = os.getenv("TESTNET_API_KEY") or os.getenv("BINANCE_API_KEY")

BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET") or os.getenv("BINANCE_API_SECRET")

IS_TESTNET = os.getenv("TESTNET", "True").lower() == "true"


# ── Refactor rollout flags (new_refactoring_plan1.md — Patch Set 2) ────

# True olursa PaperTrader._exit_trade() akışı ExitLifecycleService'e
# delege edilir. False (varsayılan) iken eski inline implementasyon
# (_exit_trade_legacy) aynen çalışmaya devam eder — rollback tek satır
# env değişikliği ile mümkün olsun diye.
EXIT_LIFECYCLE_SERVICE_ENABLED = (
    os.getenv("EXIT_LIFECYCLE_SERVICE_ENABLED", "False").lower() == "true"
)
