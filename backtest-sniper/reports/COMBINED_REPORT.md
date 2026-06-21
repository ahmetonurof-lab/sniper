# SNIPER BACKTEST — KOMBINASYON RAPORU (BNB / AVAX / LINK)
# Session: ALL (CBDR+London+NY) — NY filtre kaldirildi

## Ozet Tablo

| Sembol | Islem | WR | PnL (USDT) | Max DD | Profit Factor | Ort Trail | Long PnL | Short PnL |
|--------|-------|-----|------------|--------|---------------|-----------|-----------|-----------|
| BNBUSDT | 207 | 62.3% | **+20804.17** | 3.5% | 2.63 | 1.2 | +13449.19 | +7354.98 |
| AVAXUSDT | 142 | 72.5% | **+19527.42** | 1.6% | 3.28 | 1.4 | +7670.34 | +11857.08 |
| LINKUSDT | 93 | 51.6% | **+4618.26** | 5.7% | 2.42 | 1.0 | +849.01 | +3769.25 |
| **TOPLAM** | **442** | **63.3%** | **+44949.85** | — | — | — | **+21968.54** | **+22981.31** |

## NY-only ile Karsilastirma

| Sembol | NY-only Islem | NY-only PnL | ALL Islem | ALL PnL | PnL Artisi |
|--------|--------------|-------------|-----------|---------|------------|
| BNBUSDT | 119 | +14794.57 | 207 | +20804.17 | **+6009.60 (%41)** |
| AVAXUSDT | 77 | +12018.08 | 142 | +19527.42 | **+7509.34 (%62)** |
| LINKUSDT | 57 | +3690.46 | 93 | +4618.26 | **+927.80 (%25)** |
| **TOPLAM** | **253** | **+30503.11** | **442** | **+44949.85** | **+14446.74 (%47)** |

## Coin Bazli Detaylar

### BNBUSDT
- **FVG Esik:** 0.5 (coin fiyat ~600 USDT)
- **Toplam Islem:** 207 (Long: 124, Short: 83)
- **Kazanan:** 129 (%62.3) | **Kaybeden:** 78 (%37.7)
- **TP:** 42 (%20.3) | **SL:** 165 (%79.7)
- **Long WR:** 66.1% | **Short WR:** 56.6%
- **Long PnL:** +13449.19 | **Short PnL:** +7354.98
- **Trailing aktif:** 132 islem (PnL=+22682.17, WR=72.7%)
- **Trailing yok:** 75 islem (PnL=-1878.00, WR=44.0%)
- **Ort. Kazanan R:R:** +2.10 | **Ort. Kaybeden R:R:** -0.80
- **Max Drawdown:** 3.5% | **Max Ardisik Kayip:** 10

### AVAXUSDT
- **FVG Esik:** 0.03 (coin fiyat ~25 USDT)
- **Toplam Islem:** 142 (Long: 52, Short: 90)
- **Kazanan:** 103 (%72.5) | **Kaybeden:** 39 (%27.5)
- **TP:** 34 (%23.9) | **SL:** 108 (%76.1)
- **Long WR:** 73.1% | **Short WR:** 72.2%
- **Long PnL:** +7670.34 | **Short PnL:** +11857.08
- **Trailing aktif:** 98 islem (PnL=+18848.84, WR=78.6%)
- **Trailing yok:** 44 islem (PnL=+678.58, WR=59.1%)
- **Ort. Kazanan R:R:** +2.14 | **Ort. Kaybeden R:R:** -0.65
- **Max Drawdown:** 1.6% | **Max Ardisik Kayip:** 6

### LINKUSDT
- **FVG Esik:** 0.02 (coin fiyat ~18 USDT)
- **Toplam Islem:** 93 (Long: 30, Short: 63)
- **Kazanan:** 48 (%51.6) | **Kaybeden:** 45 (%48.4)
- **TP:** 18 (%19.4) | **SL:** 75 (%80.6)
- **Long WR:** 36.7% | **Short WR:** 58.7%
- **Long PnL:** +849.01 | **Short PnL:** +3769.25
- **Trailing aktif:** 58 islem (PnL=+5245.34, WR=55.2%)
- **Trailing yok:** 35 islem (PnL=-627.08, WR=45.7%)
- **Ort. Kazanan R:R:** +1.57 | **Ort. Kaybeden R:R:** -0.65
- **Max Drawdown:** 5.7% | **Max Ardisik Kayip:** 6

## Trailing Stop Etkisi (Tum Coinler)

| Durum | Islem | PnL | WR |
|-------|-------|-----|----|
| Trailing aktif | 288 | +46776.35 | 71.2% |
| Trailing yok | 154 | -1826.50 | 47.4% |

> **Trailing stop aktif islemlerde WR %71.2, trailing yok islemlerde WR %47.4.**
> Trailing stop PnL'yi **+48602.85 USDT** artirmistir.

## Long vs Short Performans (Tum Coinler)

| Yon | Islem | WR | PnL | Avg Win RR | Ort Trail |
|-----|-------|-----|------|-----------|----------|
| LONG | 206 | 61.2% | +21968.54 | +2.10 | 1.2 |
| SHORT | 236 | 64.4% | +22981.31 | +1.87 | 1.1 |

## Sonuc

- **AVAXUSDT** en iyi coin: %72.5 WR, +19527 PnL, %1.6 DD, PF 3.28
- **BNBUSDT** en yuksek PnL: +20804 (124 long + 83 short)
- **LINKUSDT** short agirlikli: 63 short vs 30 long, short WR %58.7
- **NY filtresi kalkinca islem sayisi %75 artti (+189 islem)**
- **Toplam PnL NY-only'e gore %47 artti: +30503 → +44950 USDT**
- **442 islemde +44949.85 USDT PnL** (10K sermaye ile ~%449 getiri)

---

*Rapor: analyzer_v3.py (FVG Wick Rejection + Dual Trailing SL/TP)*
*Parametreler: SL=FVG edge +/- buffer | TP=London High/Low veya 2R | Risk=%1 | Session=ALL*
