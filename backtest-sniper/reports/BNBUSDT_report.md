# SNIPER BACKTEST RAPORU — BNBUSDT

## Parametreler
| Parametre | Deger |
|-----------|-------|
| Sembol | BNBUSDT |
| Min FVG Size | 0.5 |
| SL ATR Mult | 1.5 |
| TP R:R | 2.0 |
| FVG Buffer Mult | 0.25 |
| Risk/Trade | %1 |
| Session | ALL (London+NY, ASIA red) |
| Initial Capital | 10000 USDT |
| 1m Bars | 205000 |
| 15m Bars | 13666 |

## Pipeline
| Asama | Sayi |
|-------|------|
| cbdr_locked | 2180 |
| sweep_detected | 487 |
| sweep_fed | 487 |
| fvg_scanned | 487 |
| wick_rejection | 207 |
| trigger_ready | 0 |
| asia_rejected | 438 |
| new_entry | 207 |
| trailing_sl_updates | 238 |
| trailing_tp_updates | 238 |
| closed | 207 |
| total_signals | 207 |
| rejected_other | 0 |

## Genel Performans
| Metrik | Deger |
|--------|-------|
| Toplam Islem | 207 |
| Kazanan | 123 (%59.4) |
| Kaybeden | 84 (%40.6) |
| TP ile kapanan | 38 (%18.4) |
| SL ile kapanan | 169 (%81.6) |
| Acik kalan | 0 |
| Toplam PnL | **+34711.05 USDT** |
| Max Drawdown | 3.2% |
| Max Ardisik Kayip | 10 islem |
| Ort. Trailing Sayisi | 1.1 |

## R:R Analizi
| Metrik | Deger |
|--------|-------|
| Ort. Kazanan R:R | +3.36 |
| Ort. Kaybeden R:R | -0.78 |
| Profit Factor | 4.28 |

## Long / Short Karsilastirma
| Yon | Islem | WR | PnL | Avg Win RR | Ort Trail |
|-----|-------|----|-----|-----------|----------|
| LONG | 124 | 62.1% | +19818.40 | +3.04 | 1.2 |
| SHORT | 83 | 55.4% | +14892.65 | +3.89 | 1.0 |

## Trailing Etkisi
| Durum | Islem | PnL | WR |
|-------|-------|-----|----|
| Trailing aktif | 133 | +36828.41 | 69.2% |
| Trailing yok | 74 | -2117.36 | 41.9% |

## Son 20 Trade
| # | Side | Entry | Exit | PnL | R:R | Result | Trail | FVG |
|---|------|-------|------|-----|-----|--------|-------|-----|
| 1 | long | 642.98 | 648.63 | +612.20 | +6.12 | SL | 1 | YES |
| 2 | long | 646.78 | 648.75 | +88.85 | +0.89 | SL | 1 | YES |
| 3 | long | 647.86 | 648.91 | +58.88 | +0.59 | SL | 1 | YES |
| 4 | short | 651.69 | 651.00 | +89.76 | +0.90 | TP | 0 | YES |
| 5 | short | 643.47 | 643.68 | -8.76 | -0.09 | SL | 1 | YES |
| 6 | short | 641.07 | 639.30 | +86.87 | +0.87 | TP | 0 | YES |
| 7 | short | 640.40 | 642.51 | -100.00 | -1.00 | SL | 0 | YES |
| 8 | short | 641.14 | 642.92 | -100.00 | -1.00 | SL | 0 | YES |
| 9 | short | 640.23 | 638.30 | +66.12 | +0.66 | TP | 0 | YES |
| 10 | short | 641.13 | 642.39 | -97.39 | -0.97 | SL | 1 | YES |
| 11 | short | 644.73 | 642.39 | +283.08 | +2.83 | SL | 2 | YES |
| 12 | short | 645.15 | 642.21 | +1248.94 | +12.49 | SL | 2 | YES |
| 13 | short | 643.99 | 642.54 | +184.44 | +1.84 | SL | 1 | YES |
| 14 | short | 643.25 | 644.17 | -100.00 | -1.00 | SL | 0 | YES |
| 15 | short | 642.65 | 644.17 | -100.00 | -1.00 | SL | 0 | YES |
| 16 | short | 642.27 | 641.82 | +46.67 | +0.47 | SL | 1 | YES |
| 17 | short | 643.67 | 641.48 | +605.88 | +6.06 | SL | 2 | YES |
| 18 | short | 641.95 | 641.61 | +15.25 | +0.15 | SL | 2 | YES |
| 19 | short | 641.48 | 641.45 | +1.21 | +0.01 | SL | 3 | YES |
| 20 | short | 642.15 | 641.45 | +96.56 | +0.97 | SL | 1 | YES |
