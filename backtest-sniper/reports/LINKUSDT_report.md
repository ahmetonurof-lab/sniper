# SNIPER BACKTEST RAPORU — LINKUSDT

## Parametreler
| Parametre | Deger |
|-----------|-------|
| Sembol | LINKUSDT |
| Min FVG Size | 0.02 |
| SL ATR Mult | 1.5 |
| TP R:R | 2.0 |
| FVG Buffer Mult | 0.25 |
| Risk/Trade | %1 |
| Session | ALL (London+NY, ASIA red) |
| Initial Capital | 10000 USDT |
| 1m Bars | 97000 |
| 15m Bars | 6466 |

## Pipeline
| Asama | Sayi |
|-------|------|
| cbdr_locked | 980 |
| sweep_detected | 203 |
| sweep_fed | 203 |
| fvg_scanned | 203 |
| wick_rejection | 93 |
| trigger_ready | 0 |
| asia_rejected | 198 |
| new_entry | 93 |
| trailing_sl_updates | 119 |
| trailing_tp_updates | 119 |
| closed | 93 |
| total_signals | 93 |
| rejected_other | 0 |

## Genel Performans
| Metrik | Deger |
|--------|-------|
| Toplam Islem | 93 |
| Kazanan | 54 (%58.1) |
| Kaybeden | 39 (%41.9) |
| TP ile kapanan | 18 (%19.4) |
| SL ile kapanan | 75 (%80.6) |
| Acik kalan | 0 |
| Toplam PnL | **+10091.17 USDT** |
| Max Drawdown | 2.3% |
| Max Ardisik Kayip | 4 islem |
| Ort. Trailing Sayisi | 1.3 |

## R:R Analizi
| Metrik | Deger |
|--------|-------|
| Ort. Kazanan R:R | +2.32 |
| Ort. Kaybeden R:R | -0.63 |
| Profit Factor | 3.70 |

## Long / Short Karsilastirma
| Yon | Islem | WR | PnL | Avg Win RR | Ort Trail |
|-----|-------|----|-----|-----------|----------|
| LONG | 30 | 50.0% | +5559.24 | +4.34 | 1.6 |
| SHORT | 63 | 61.9% | +4531.93 | +1.55 | 1.1 |

## Trailing Etkisi
| Durum | Islem | PnL | WR |
|-------|-------|-----|----|
| Trailing aktif | 61 | +10418.25 | 62.3% |
| Trailing yok | 32 | -327.08 | 50.0% |

## Son 20 Trade
| # | Side | Entry | Exit | PnL | R:R | Result | Trail | FVG |
|---|------|-------|------|-----|-----|--------|-------|-----|
| 1 | short | 13.98 | 13.95 | +17.27 | +0.17 | TP | 0 | YES |
| 2 | short | 14.07 | 13.91 | +200.00 | +2.00 | TP | 1 | YES |
| 3 | short | 14.03 | 13.89 | +131.76 | +1.32 | TP | 0 | YES |
| 4 | short | 14.21 | 14.10 | +78.90 | +0.79 | SL | 7 | YES |
| 5 | short | 14.05 | 14.10 | -100.00 | -1.00 | SL | 0 | YES |
| 6 | short | 14.09 | 14.10 | -22.22 | -0.22 | SL | 2 | YES |
| 7 | short | 14.10 | 14.10 | -5.88 | -0.06 | SL | 1 | YES |
| 8 | short | 14.03 | 14.11 | -100.00 | -1.00 | SL | 0 | YES |
| 9 | long | 14.23 | 14.27 | +34.41 | +0.34 | TP | 0 | YES |
| 10 | long | 14.23 | 14.17 | -37.30 | -0.37 | SL | 5 | YES |
| 11 | long | 14.19 | 14.17 | -22.39 | -0.22 | SL | 3 | YES |
| 12 | long | 14.23 | 14.17 | -94.00 | -0.94 | SL | 1 | YES |
| 13 | long | 14.18 | 14.17 | -14.89 | -0.15 | SL | 1 | YES |
| 14 | long | 14.29 | 15.81 | +1133.64 | +11.34 | SL | 3 | YES |
| 15 | short | 15.24 | 15.16 | +69.57 | +0.70 | TP | 0 | YES |
| 16 | short | 15.37 | 15.22 | +70.48 | +0.70 | SL | 3 | YES |
| 17 | short | 15.26 | 15.22 | +43.28 | +0.43 | SL | 1 | YES |
| 18 | short | 15.19 | 15.23 | -100.00 | -1.00 | SL | 0 | YES |
| 19 | short | 15.17 | 15.19 | -30.43 | -0.30 | SL | 1 | YES |
| 20 | short | 15.28 | 15.21 | +85.51 | +0.86 | SL | 2 | YES |
