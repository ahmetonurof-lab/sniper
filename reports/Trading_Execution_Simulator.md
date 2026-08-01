# Trading Execution Simulator

## Amaç

Backtest motorunun yalnızca strateji sinyalini değil, gerçek emir yürütme koşullarını da simüle etmesi:

- Emir gecikmesi
- Spread
- Slippage
- Düşük likidite
- Kısmi fill
- Emir reddi
- SL/TP yazılamaması
- Cancel/replace gecikmesi
- Immediate-trigger reddi
- Network/API hataları

Bu katman strateji kurallarını değiştirmemeli. Giriş kararını ve execution sonucunu ayrı ölçmelidir.

## Temel mimari

```text
15m strategy signal
        |
        v
ExecutionSimulator.submit_entry()
        |
        +--> queue delay
        +--> market state snapshot
        +--> spread model
        +--> slippage model
        +--> liquidity/partial-fill model
        +--> order acceptance/rejection
        |
        v
Filled position or failed entry
        |
        +--> protection order placement
        +--> SL/TP activation delay
        +--> trailing replace simulation
        +--> exit execution
        |
        v
Realistic fill, fee, slippage and execution metrics
```

## Tasarım ilkeleri

### 1. Strategy ve execution ayrımı

Strateji şu kararı verir:

```text
ENTRY_SIGNAL
side
signal_price
sl
 tp
qty
signal_timestamp
```

Execution simulator şu kararı verir:

```text
FILLED
PARTIAL_FILL
REJECTED
EXPIRED
PROTECTION_FAILED
```

Bir setup'ın reddedilmesi strateji kaybı değildir; execution kaybı olarak ayrıca raporlanmalıdır.

### 2. Deterministik ve olasılıksal modlar

İki çalışma modu kullanılmalı:

- **Deterministic mode:** Aynı seed ile aynı sonucu üretir. CI ve regression testleri için.
- **Monte Carlo mode:** Delay, slippage, reject ve partial-fill olasılıklarını dağılımdan örnekler. Gerçekçilik ve risk analizi için.

Her run mutlaka seed kaydetmeli:

```python
ExecutionConfig(seed=42)
```

## Veri modelleri

```python
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

class OrderStatus(str, Enum):
    QUEUED = "QUEUED"
    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

@dataclass(frozen=True)
class MarketSnapshot:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread_bps: float
    available_liquidity: float

@dataclass
class SimulatedOrder:
    order_id: str
    symbol: str
    side: str
    order_type: str
    requested_qty: float
    requested_price: float | None
    status: OrderStatus
    filled_qty: float = 0.0
    average_fill_price: float | None = None
    submitted_at: int = 0
    accepted_at: int | None = None
    filled_at: int | None = None
    rejected_reason: str | None = None
    fees: float = 0.0
    slippage: float = 0.0

@dataclass(frozen=True)
class ExecutionConfig:
    seed: int = 42
    entry_delay_bars_min: int = 0
    entry_delay_bars_max: int = 1
    protection_delay_bars: int = 0
    cancel_replace_delay_bars: int = 0
    base_slippage_bps: float = 1.0
    volatility_slippage_mult: float = 0.5
    spread_bps: float = 2.0
    reject_probability: float = 0.0
    protection_reject_probability: float = 0.0
    partial_fill_probability: float = 0.0
    partial_fill_ratio_min: float = 0.25
    partial_fill_ratio_max: float = 0.90
    max_order_age_bars: int = 2
    fee_rate: float = 0.0005
```

## Entry simülasyonu

### Girdi

- Sinyal barı ve timestamp
- Sinyal yönü
- Sinyal fiyatı
- İstenen qty
- SL/TP
- Sonraki 1m market barları
- Sembolün execution config'i

### Akış

```text
1. Signal oluşur.
2. Entry order QUEUED olur.
3. Delay örneklenir.
4. Delay boyunca fiyat ve likidite izlenir.
5. Order reject olasılığı uygulanır.
6. Fill fiyatı spread + slippage ile hesaplanır.
7. Liquidity yetersizse partial fill uygulanır.
8. Minimum fill oranı sağlanmazsa order EXPIRED/CANCELLED olur.
9. Dolan qty üzerinden gerçek risk ve koruma emirleri hesaplanır.
```

### Market order fill fiyatı

Long entry için:

```python
fill_price = market_price * (1 + spread / 2 + slippage)
```

Short entry için:

```python
fill_price = market_price * (1 - spread / 2 - slippage)
```

Daha doğru uygulama için spread ve slippage bps olarak ayrı tutulmalı:

```python
spread_component = spread_bps / 10_000
slippage_component = slippage_bps / 10_000
```

### Slippage modeli

Başlangıçta üç model yeterli:

#### Sabit model

```python
slippage_bps = base_slippage_bps
```

#### Volatilite modeli

```python
slippage_bps = base_slippage_bps + atr_pct * volatility_slippage_mult
```

#### Likidite modeli

```python
impact = order_notional / available_liquidity
slippage_bps = base_slippage_bps + impact * liquidity_impact_mult
```

Canlı veriden kalibrasyon yapılana kadar agresif varsayım kullanmayın. Ã–nce düşük, orta ve yüksek execution maliyetli üç senaryo raporlayın.

## Kısmi fill

Kısmi fill, istenen qty'nin tamamının tek fiyattan dolmadığı durumdur.

```python
if available_liquidity >= requested_qty:
    filled_qty = requested_qty
else:
    filled_qty = available_liquidity
```

Olasılıksal modelde:

```python
filled_ratio = uniform(min_ratio, max_ratio)
filled_qty = requested_qty * filled_ratio
```

Minimum fill policy açıkça belirtilmeli:

- `filled_qty == 0`: trade yok
- `filled_qty < 25%`: order iptal, trade yok
- `25% <= filled_qty < 100%`: partial position
- `filled_qty == 100%`: full position

Partial position açıldıysa SL/TP qty'si yalnızca gerçekten dolan qty olmalı.

## Protection order simülasyonu

Entry fill olduktan sonra SL ve TP emirleri ayrı event olarak işlenmeli:

```text
ENTRY_FILLED
  -> SL_SUBMIT
  -> TP_SUBMIT
  -> SL_ACTIVE / TP_ACTIVE
```

Her protection order için şunlar simüle edilmeli:

- Yazılma gecikmesi
- Price precision
- Immediate-trigger rejection
- API reject
- Retry
- Emergency market close

### Koruma emirlerinden biri başarısızsa

Ã–nerilen policy:

| Durum | Simülatör davranışı |
|---|---|
| SL başarısız, TP başarılı | Pozisyonu korumasız kabul et, emergency-close event üret |
| SL başarısız, TP başarısız | Pozisyonu hemen emergency market close dene |
| TP başarısız, SL başarılı | Pozisyon açık, `TP_UNPROTECTED` metric üret |
| İki emir de aktif | Normal trade akışı |
| Emergency close başarısız | `UNPROTECTED_LIVE_POSITION` olarak işaretle |

Backtestte emergency close fiyatı sonraki erişilebilir market fiyatı olmalı, ideal entry/SL fiyatı olmamalı.

## Trailing ve cancel/replace

Trailing event'i 1m kapanışında oluşmalı:

```text
TRAIL_SIGNAL
  -> REPLACE_REQUEST
  -> delay
  -> old protection remains active
  -> new protection accepted or rejected
```

Replace tamamlanana kadar eski SL/TP aktif kalmalı. Bu kritik: backtest yeni SL'yi aynı anda aktif kabul ederse canlıdan daha iyi sonuç üretir.

Replace rejection durumunda:

- Eski emir aktif kalır.
- Reject nedeni kaydedilir.
- Aynı fingerprint tekrar tekrar gönderilmez.
- Retry policy uygulanır.
- Fiyat eski SL tarafını geçtiyse exit event üretilir.

## Exit simülasyonu

Exit önceliği açıkça tanımlanmalı. Aynı 1m mumunda hem SL hem TP görülürse yalnızca OHLC verisiyle sıra bilinemez.

Başlangıç policy seçenekleri:

1. **Conservative:** SL önce kabul edilir.
2. **Optimistic:** TP önce kabul edilir.
3. **Intrabar data:** Daha düşük timeframe veya trade tape ile gerçek sıra tahmin edilir.

Risk raporu için conservative policy varsayılan olmalı. Optimistic sonuç yalnızca üst sınır senaryosu olarak raporlanmalı.

Exit fill fiyatı:

```text
normal TP/SL fill: trigger price + tick rounding
gap-through SL: ilk erişilebilir fiyat + slippage
market emergency close: mevcut market fiyatı + spread/slippage
```

## Tick ve miktar precision

Simulator her order için şu sırayı izlemeli:

```text
1. Ham fiyatı hesapla.
2. Sembol tick size ile round et.
3. Yön bazlı SL/TP güvenlik yuvarlaması uygula.
4. Yuvarlanmış risk mesafesini yeniden hesapla.
5. Qty ve margin limitlerini kontrol et.
```

SL/TP'nin hesaplanıp sonra precision uygulanması yeterli değil. Risk ve qty, exchange'e gidecek son değerler üzerinden hesaplanmalı.

## Emir reddi modeli

Reject sebepleri ayrı metric olmalı:

```text
MIN_QTY
MIN_NOTIONAL
INVALID_PRICE
IMMEDIATE_TRIGGER
INSUFFICIENT_MARGIN
NETWORK_ERROR
RATE_LIMIT
EXCHANGE_ERROR
PROTECTION_TIMEOUT
```

İlk versiyonda reject olasılıklarını rastgele vermek yerine canlı loglardan kalibre edin.

## Sembol bazlı execution profili

Her sembol için ayrı profil tutulmalı:

```python
EXECUTION_PROFILES = {
    "SOLUSDT": {
        "spread_bps": 2.0,
        "base_slippage_bps": 1.5,
        "reject_probability": 0.002,
        "partial_fill_probability": 0.01,
    },
    "BNBUSDT": {
        "spread_bps": 1.0,
        "base_slippage_bps": 0.8,
        "reject_probability": 0.001,
        "partial_fill_probability": 0.005,
    },
}
```

Bu değerler varsayım olarak değil, canlı order loglarından tahmin edilmeli.

## Gerekli event logları

Her order event'i append-only formatta kaydedilmeli:

```json
{
  "symbol": "SOLUSDT",
  "trade_id": "SOLUSDT-12345",
  "order_id": "sim-001",
  "event": "ENTRY_FILLED",
  "signal_ts": 1720000000000,
  "submit_ts": 1720000001200,
  "fill_ts": 1720000001800,
  "requested_qty": 100.0,
  "filled_qty": 98.5,
  "signal_price": 150.0,
  "fill_price": 150.08,
  "spread_bps": 2.0,
  "slippage_bps": 3.3,
  "delay_ms": 600,
  "status": "PARTIAL"
}
```

## Ã–lçülecek metrikler

### Execution metrikleri

- Entry fill rate
- Full fill rate
- Partial fill rate
- Reject rate
- Average delay
- P95/P99 delay
- Average slippage bps
- P95 slippage bps
- Protection activation delay
- Protection reject rate
- Trailing replace success rate
- Emergency close rate
- Unprotected position count

### Performans farkı

Aynı strateji sinyali için üç sonuç ayrı raporlanmalı:

```text
Strategy theoretical PnL
Execution-adjusted PnL
Realized/live PnL
```

Fark şu şekilde ayrıştırılmalı:

```text
strategy edge loss
+ entry slippage cost
+ exit slippage cost
+ spread cost
+ fee cost
+ rejected-entry cost
+ missed-trade cost
+ partial-fill effect
+ protection failure cost
```

## Kalibrasyon planı

### Aşama 1: Deterministic baseline

- Delay = 0
- Slippage = 0
- Spread = 0
- Reject = 0
- Full fill = 100%

Bu sonuç mevcut teorik backtest ile aynı olmalı.

### Aşama 2: Sabit maliyet senaryoları

Ãœç senaryo çalıştırın:

| Senaryo | Spread | Slippage | Reject | Partial fill |
|---|---:|---:|---:|---:|
| Low cost | düşük | düşük | düşük | düşük |
| Base | orta | orta | canlı ortalaması | canlı ortalaması |
| Stress | yüksek | yüksek | P95 | P95 |

### Aşama 3: Canlı log calibration

Canlı loglardan her sembol için dağılım çıkarın:

- Sinyal â†’ submit gecikmesi
- Submit â†’ ACK gecikmesi
- ACK â†’ fill gecikmesi
- Sinyal fiyatı â†’ fill fiyatı farkı
- Reject oranı
- Partial fill oranı
- SL/TP activation delay

### Aşama 4: Replay validation

Gerçek canlı trade'i aynı timestamp ve market barlarıyla simulator'dan replay edin. Simulated fill ile gerçekleşen fill arasındaki fark tolerans aralığında olmalı.

Ã–nerilen ilk hedefler:

```text
median fill price error <= 1 tick
P95 fill price error <= 3 ticks
median delay error <= 1 bar
trade outcome agreement >= 90%
```

## Test planı

### Unit testler

- Long/short spread yönü
- Slippage yönü
- Tick rounding
- Min qty/notional
- Partial fill
- Reject
- Delay
- SL/TP protection failure
- Immediate trigger
- Gap-through SL
- Same-bar SL/TP policy
- Cancel/replace race

### Property testler

- Long entry fill fiyatı ask tarafında veya üzerinde olmalı.
- Short entry fill fiyatı bid tarafında veya altında olmalı.
- Filled qty requested qty'yi aşmamalı.
- Reject edilen order pozisyon yaratmamalı.
- Protection reject edilirse event log oluşmalı.
- Replace tamamlanana kadar eski protection aktif kalmalı.
- Aynı fingerprint duplicate order üretmemeli.

### Regression testler

- Zero-friction simulator = mevcut backtest sonuçları.
- Fixed seed = aynı trade/event log.
- Aynı input replay = aynı output.
- Her sembol profile değişimi yalnızca execution sonuçlarını değiştirmeli, strategy trigger trace'ini değiştirmemeli.

## Uygulama sırası

1. `execution_simulator.py` içinde event ve order modellerini oluştur.
2. Zero-friction mode ile mevcut backtesti bağla.
3. Fill price + spread + slippage ekle.
4. Delay queue ekle.
5. Partial fill ve reject modellerini ekle.
6. Protection order lifecycle ekle.
7. 1m trailing replace lifecycle ekle.
8. Gap ve same-bar exit policy ekle.
9. Canlı loglardan sembol bazlı calibration yap.
10. Theoretical, execution-adjusted ve realized sonuçları yan yana raporla.

## Kritik uyarı

Simulator strateji sinyalini değiştirmemeli. `CBDR -> sweep -> FVG -> trigger` parity testi ayrı kalmalı. Execution simulator yalnızca sinyalden sonra emrin hangi fiyatla, ne kadar gecikmeyle ve ne oranda gerçekleştiğini modellemeli.

Aksi halde strateji değişikliği ile execution maliyetini birbirine karıştırır ve hangi problemin kârı yok ettiğini anlayamazsınız.
