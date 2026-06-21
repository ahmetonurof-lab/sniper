# SNIPER BACKTEST RAPORU — BTCUSDT

## Parametreler
| Parametre | Deger |
|-----------|-------|
| Sembol | BTCUSDT |
| Min FVG Size | 10.0 |
| SL ATR Mult | 1.5 |
| TP R:R | 2.0 |
| FVG Buffer Mult | 0.25 |
| Risk/Trade | %1 |
| Session | ALL (London+NY, ASIA red) |
| Initial Capital | 10000 USDT |
| 1m Bars | 113647 |
| 15m Bars | 7576 |

## Pipeline
| Asama | Sayi |
|-------|------|
| cbdr_locked | 1166 |
| sweep_detected | 252 |
| sweep_fed | 252 |
| fvg_scanned | 252 |
| wick_rejection | 120 |
| trigger_ready | 0 |
| asia_rejected | 234 |
| new_entry | 120 |
| trailing_sl_updates | 185 |
| trailing_tp_updates | 185 |
| closed | 120 |
| total_signals | 120 |
| rejected_other | 0 |

## Genel Performans
| Metrik | Deger |
|--------|-------|
| Toplam Islem | 120 |
| Kazanan | 80 (%66.7) |
| Kaybeden | 40 (%33.3) |
| TP ile kapanan | 14 (%11.7) |
| SL ile kapanan | 106 (%88.3) |
| Acik kalan | 0 |
| Toplam PnL | **+15546.79 USDT** |
| Max Drawdown | 1.4% |
| Max Ardisik Kayip | 4 islem |
| Ort. Trailing Sayisi | 1.5 |

## R:R Analizi
| Metrik | Deger |
|--------|-------|
| Ort. Kazanan R:R | +2.21 |
| Ort. Kaybeden R:R | -0.53 |
| Profit Factor | 4.16 |

## Long / Short Karsilastirma
| Yon | Islem | WR | PnL | Avg Win RR | Ort Trail |
|-----|-------|----|-----|-----------|----------|
| LONG | 58 | 58.6% | +4394.69 | +1.65 | 1.7 |
| SHORT | 62 | 74.2% | +11152.10 | +2.62 | 1.4 |

## Trailing Etkisi
| Durum | Islem | PnL | WR |
|-------|-------|-----|----|
| Trailing aktif | 99 | +16046.15 | 68.7% |
| Trailing yok | 21 | -499.36 | 57.1% |

## Son 20 Trade
| # | Side | Entry | Exit | PnL | R:R | Result | Trail | FVG |
|---|------|-------|------|-----|-----|--------|-------|-----|
| 1 | long | 89270.00 | 89194.32 | -10.39 | -0.10 | SL | 1 | YES |
| 2 | long | 90408.00 | 90555.00 | +33.16 | +0.33 | TP | 0 | YES |
| 3 | long | 90566.74 | 90606.88 | +11.06 | +0.11 | TP | 0 | YES |
| 4 | long | 89923.99 | 90241.66 | +41.67 | +0.42 | SL | 6 | YES |
| 5 | long | 90316.64 | 90241.66 | -22.24 | -0.22 | SL | 2 | YES |
| 6 | long | 90220.00 | 90241.66 | +10.12 | +0.10 | SL | 1 | YES |
| 7 | short | 81415.44 | 81296.00 | +21.10 | +0.21 | TP | 0 | YES |
| 8 | short | 82562.26 | 81909.14 | +354.40 | +3.54 | SL | 2 | YES |
| 9 | short | 82311.24 | 81911.73 | +91.24 | +0.91 | SL | 2 | YES |
| 10 | short | 82212.08 | 81952.55 | +44.91 | +0.45 | SL | 2 | YES |
| 11 | short | 82404.60 | 82053.28 | +72.28 | +0.72 | SL | 2 | YES |
| 12 | short | 81923.05 | 81957.72 | -8.17 | -0.08 | SL | 1 | YES |
| 13 | short | 82216.99 | 81977.68 | +57.20 | +0.57 | SL | 1 | YES |
| 14 | short | 82742.10 | 82294.71 | +79.96 | +0.80 | SL | 1 | YES |
| 15 | short | 83152.76 | 82309.25 | +516.22 | +5.16 | SL | 1 | YES |
| 16 | short | 82955.14 | 82295.25 | +179.07 | +1.79 | SL | 2 | YES |
| 17 | long | 84286.89 | 84378.39 | +9.99 | +0.10 | SL | 1 | YES |
| 18 | long | 84591.60 | 84430.55 | -100.00 | -1.00 | SL | 0 | YES |
| 19 | long | 84245.50 | 84357.20 | +12.48 | +0.12 | SL | 1 | YES |
| 20 | long | 83983.20 | 84430.81 | +80.02 | +0.80 | SL | 1 | YES |
