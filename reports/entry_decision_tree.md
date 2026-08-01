# Giriş Karar Ağacı Grafikleri

## Ortak canlı/backtest giriş ağacı

```mermaid
flowchart TD
 A[15m bar kapandı] --> B[SessionState.update]
 B --> C{CBDR yeni döngü?}
 C -- evet --> D[CBDR state reset]
 C -- hayır --> E
 D --> E{CBDR penceresi içinde?}
 E -- evet --> F[open/close body high-low takip]
 E -- hayır --> G{CBDR body kilitli mi?}
 F --> G
 G -- hayır --> H{Sweep var mı?}
 G -- evet --> H
 H -- hayır --> I[on_sweep çağırma]
 I --> J{RSM mevcut state}
 H -- evet --> K{RSM IDLE?}
 K -- hayır --> J
 K -- evet --> L[on_sweep direction + sweep_level]
 L --> M[SWEEP_DETECTED]
 J --> N{SWEEP_DETECTED?}
 M --> N
 N -- evet --> O[on_sweep_confirmed]
 N -- hayır --> P[mevcut state korunur]
 O --> Q{Sweep invalid mi?}
 Q -- evet --> R[reset IDLE]
 Q -- hayır --> S[Son 100 bar FVG tara]
 S --> T{Yön, eski FVG, wick touch, body kırılmadı?}
 T -- hayır --> U[SWEEP_DETECTED'de bekle]
 T -- evet --> V[TRIGGER_READY]
 V --> W{Yeni CBDR ve kilitsiz?}
 W -- evet --> X{Bias NEUTRAL?}
 X -- evet --> R
 X -- hayır --> Y
 W -- hayır --> Y
 Y --> Z{CBDR locked?}
 Z -- hayır --> AA[Entry yok]
 Z -- evet --> AB{Session aktif mi?}
 AB -- hayır --> AC[Entry yok]
 AB -- evet --> AD{should_trade + cbdr_mult}
 AD -- hayır --> AE[reset / entry yok]
 AD -- evet --> AF[SL/TP + risk + qty]
 AF --> AG{Risk geçerli ve qty > 0?}
 AG -- hayır --> AH[reset / entry yok]
 AG -- evet --> AI[Entry]
```

## Sembol bazlı session dalları

Karar ağacının geri kalanı tüm sembollerde aynıdır. Farklı olan yalnızca `CBDR start/end` değeridir.

### SOLUSDT

```mermaid
flowchart LR
 A[Bar] --> B{Saat 19-01 CBDR mı?}
 B -- evet --> C[CBDR body takip / entry yok]
 B -- hayır --> D[Session aktifse ortak giriş ağacına devam]
```

### BNBUSDT, ATOMUSDT, APTUSDT, DOTUSDT

```mermaid
flowchart LR
 A[Bar] --> B{Saat 19-01 CBDR mı?}
 B -- evet --> C[CBDR body takip / entry yok]
 B -- hayır --> D[Session aktifse ortak giriş ağacına devam]
```

### AVAXUSDT, XRPUSDT, ADAUSDT

```mermaid
flowchart LR
 A[Bar] --> B{Saat 22-02 CBDR mı?}
 B -- evet --> C[CBDR body takip / entry yok]
 B -- hayır --> D[Session aktifse ortak giriş ağacına devam]
```

### LINKUSDT

```mermaid
flowchart LR
 A[Bar] --> B{Saat 01-05 CBDR mı?}
 B -- evet --> C[CBDR body takip / entry yok]
 B -- hayır --> D[Session aktifse ortak giriş ağacına devam]
```

## Sembol matrisi

| Sembol | CBDR | Giriş için aktif dış pencere |
|---|---:|---|
| SOLUSDT | 19:00-01:00 | session router izin verirse |
| BNBUSDT | 19:00-01:00 | session router izin verirse |
| ATOMUSDT | 19:00-01:00 | session router izin verirse |
| APTUSDT | 19:00-01:00 | session router izin verirse |
| DOTUSDT | 19:00-01:00 | session router izin verirse |
| AVAXUSDT | 22:00-02:00 | 02:00-22:00 içindeki aktif session |
| XRPUSDT | 22:00-02:00 | 02:00-22:00 içindeki aktif session |
| ADAUSDT | 22:00-02:00 | 02:00-22:00 içindeki aktif session |
| LINKUSDT | 01:00-05:00 | 05:00-01:00 içindeki aktif session |

## Kritik not

Bu grafik sweep olmadan giriş akışını göstermiyor. Sweep yoksa RSM başlatılmaz, FVG taranmaz ve entry oluşmaz. TRIGGER_READY state'i yeni CBDR gününe taşınırsa parity kuralı gereği kilitsiz ve nötr durumda resetlenir.
