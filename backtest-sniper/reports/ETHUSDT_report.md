# SNIPER BACKTEST RAPORU — ETHUSDT

## Parametreler
| Parametre | Deger |
|-----------|-------|
| Sembol | ETHUSDT |
| Min FVG Size | 0.5 |
| SL ATR Mult | 1.5 |
| TP R:R | 2.0 |
| FVG Buffer Mult | 0.25 |
| Risk/Trade | %1 |
| Session | ALL (London+NY, ASIA red) |
| Initial Capital | 10000 USDT |
| 1m Bars | 134000 |
| 15m Bars | 8933 |

## Pipeline
| Asama | Sayi |
|-------|------|
| cbdr_locked | 1390 |
| sweep_detected | 315 |
| sweep_fed | 315 |
| fvg_scanned | 315 |
| wick_rejection | 151 |
| trigger_ready | 0 |
| asia_rejected | 281 |
| new_entry | 151 |
| trailing_sl_updates | 212 |
| trailing_tp_updates | 212 |
| closed | 151 |
| total_signals | 151 |
| rejected_other | 0 |

## Genel Performans
| Metrik | Deger |
|--------|-------|
| Toplam Islem | 151 |
| Kazanan | 102 (%67.5) |
| Kaybeden | 49 (%32.5) |
| TP ile kapanan | 32 (%21.2) |
| SL ile kapanan | 116 (%76.8) |
| Acik kalan | 3 |
| Toplam PnL | **-4494.08 USDT** |
| Max Drawdown | 75.4% |
| Max Ardisik Kayip | 5 islem |
| Ort. Trailing Sayisi | 1.4 |

## R:R Analizi
| Metrik | Deger |
|--------|-------|
| Ort. Kazanan R:R | +1.51 |
| Ort. Kaybeden R:R | -4.06 |
| Profit Factor | 0.37 |

## Long / Short Karsilastirma
| Yon | Islem | WR | PnL | Avg Win RR | Ort Trail |
|-----|-------|----|-----|-----------|----------|
| LONG | 84 | 71.4% | -9631.27 | +1.45 | 1.6 |
| SHORT | 67 | 62.7% | +5137.19 | +1.59 | 1.1 |

## Trailing Etkisi
| Durum | Islem | PnL | WR |
|-------|-------|-----|----|
| Trailing aktif | 112 | +7115.67 | 71.4% |
| Trailing yok | 39 | -11609.75 | 56.4% |

## Son 20 Trade
| # | Side | Entry | Exit | PnL | R:R | Result | Trail | FVG |
|---|------|-------|------|-----|-----|--------|-------|-----|
| 1 | short | 2545.97 | 2534.49 | +147.33 | +1.47 | SL | 1 | YES |
| 2 | short | 2544.69 | 2536.67 | +71.29 | +0.71 | SL | 1 | YES |
| 3 | long | 2528.87 | 2530.17 | +7.33 | +0.07 | SL | 3 | YES |
| 4 | long | 2533.01 | 2530.17 | -37.10 | -0.37 | SL | 1 | YES |
| 5 | long | 2520.92 | 2530.63 | +89.33 | +0.89 | SL | 3 | YES |
| 6 | long | 2509.51 | 2526.04 | +61.56 | +0.62 | SL | 3 | YES |
| 7 | long | 2499.58 | 2526.36 | +161.21 | +1.61 | SL | 3 | YES |
| 8 | long | 2522.57 | 2527.30 | +73.50 | +0.74 | TP | 0 | YES |
| 9 | long | 2522.75 | 2527.30 | +138.19 | +1.38 | TP | 0 | YES |
| 10 | long | 2522.49 | 2525.80 | +13.78 | +0.14 | SL | 6 | YES |
| 11 | long | 2522.80 | 2525.80 | +8.49 | +0.08 | SL | 6 | YES |
| 12 | long | 2520.69 | 2525.80 | +15.30 | +0.15 | SL | 6 | YES |
| 13 | long | 2511.94 | 2525.80 | +80.47 | +0.80 | SL | 5 | YES |
| 14 | long | 2533.63 | 2523.82 | -100.00 | -1.00 | SL | 0 | YES |
| 15 | long | 2517.18 | 2523.46 | +20.45 | +0.20 | SL | 3 | YES |
| 16 | long | 2507.25 | 2525.05 | +384.19 | +3.84 | SL | 2 | YES |
| 17 | long | 2508.91 | 2525.71 | +298.22 | +2.98 | SL | 1 | YES |
| 18 | long | 2516.84 | 2258.42 | -4772.30 | -47.72 | OPEN | 1 | YES |
| 19 | long | 2521.47 | 2258.42 | -8849.45 | -88.49 | OPEN | 0 | YES |
| 20 | long | 2525.99 | 2258.42 | -3230.06 | -32.30 | OPEN | 0 | YES |
