# SNIPER BACKTEST RAPORU — AVAXUSDT

## Parametreler
| Parametre | Deger |
|-----------|-------|
| Sembol | AVAXUSDT |
| Min FVG Size | 0.03 |
| SL ATR Mult | 1.5 |
| TP R:R | 2.0 |
| FVG Buffer Mult | 0.25 |
| Risk/Trade | %1 |
| Session | ALL (London+NY, ASIA red) |
| Initial Capital | 10000 USDT |
| 1m Bars | 132000 |
| 15m Bars | 8800 |

## Pipeline
| Asama | Sayi |
|-------|------|
| cbdr_locked | 1368 |
| sweep_detected | 274 |
| sweep_fed | 274 |
| fvg_scanned | 274 |
| wick_rejection | 142 |
| trigger_ready | 0 |
| asia_rejected | 276 |
| new_entry | 142 |
| trailing_sl_updates | 193 |
| trailing_tp_updates | 193 |
| closed | 142 |
| total_signals | 142 |
| rejected_other | 0 |

## Genel Performans
| Metrik | Deger |
|--------|-------|
| Toplam Islem | 142 |
| Kazanan | 103 (%72.5) |
| Kaybeden | 39 (%27.5) |
| TP ile kapanan | 33 (%23.2) |
| SL ile kapanan | 109 (%76.8) |
| Acik kalan | 0 |
| Toplam PnL | **+20713.04 USDT** |
| Max Drawdown | 1.6% |
| Max Ardisik Kayip | 6 islem |
| Ort. Trailing Sayisi | 1.4 |

## R:R Analizi
| Metrik | Deger |
|--------|-------|
| Ort. Kazanan R:R | +2.26 |
| Ort. Kaybeden R:R | -0.65 |
| Profit Factor | 3.47 |

## Long / Short Karsilastirma
| Yon | Islem | WR | PnL | Avg Win RR | Ort Trail |
|-----|-------|----|-----|-----------|----------|
| LONG | 52 | 75.0% | +9182.80 | +2.60 | 1.3 |
| SHORT | 90 | 71.1% | +11530.24 | +2.05 | 1.4 |

## Trailing Etkisi
| Durum | Islem | PnL | WR |
|-------|-------|-----|----|
| Trailing aktif | 100 | +20240.84 | 78.0% |
| Trailing yok | 42 | +472.20 | 59.5% |

## Son 20 Trade
| # | Side | Entry | Exit | PnL | R:R | Result | Trail | FVG |
|---|------|-------|------|-----|-----|--------|-------|-----|
| 1 | long | 19.03 | 19.17 | +166.67 | +1.67 | SL | 1 | YES |
| 2 | long | 19.04 | 19.84 | +1361.70 | +13.62 | TP | 1 | YES |
| 3 | short | 19.69 | 19.66 | +25.00 | +0.25 | TP | 0 | YES |
| 4 | short | 20.29 | 19.84 | +290.24 | +2.90 | SL | 4 | YES |
| 5 | short | 20.24 | 19.81 | +245.32 | +2.45 | SL | 3 | YES |
| 6 | short | 21.49 | 19.86 | +655.78 | +6.56 | SL | 2 | YES |
| 7 | short | 21.57 | 19.83 | +1254.95 | +12.55 | SL | 2 | YES |
| 8 | short | 21.40 | 21.68 | -78.65 | -0.79 | SL | 2 | YES |
| 9 | short | 21.55 | 21.68 | -100.00 | -1.00 | SL | 0 | YES |
| 10 | short | 22.42 | 22.51 | -100.00 | -1.00 | SL | 0 | YES |
| 11 | short | 22.26 | 22.21 | +21.54 | +0.22 | SL | 1 | YES |
| 12 | long | 22.44 | 22.57 | +110.64 | +1.11 | TP | 0 | YES |
| 13 | long | 22.48 | 22.59 | +51.16 | +0.51 | TP | 1 | YES |
| 14 | long | 22.22 | 22.26 | +42.47 | +0.42 | SL | 2 | YES |
| 15 | long | 22.53 | 22.46 | -23.97 | -0.24 | SL | 2 | YES |
| 16 | long | 22.42 | 22.45 | +12.88 | +0.13 | SL | 2 | YES |
| 17 | long | 22.40 | 22.46 | +33.33 | +0.33 | SL | 2 | YES |
| 18 | long | 22.28 | 22.51 | +766.67 | +7.67 | SL | 2 | YES |
| 19 | long | 22.32 | 22.48 | +247.17 | +2.47 | SL | 1 | YES |
| 20 | long | 22.34 | 22.51 | +260.78 | +2.61 | SL | 1 | YES |
