# SNIPER BOT — DOĞRULANMIŞ ÇAPRAZ BAĞLAM TESTİ & BUG ANALİZİ RAPORU
## Tarih: 2026-08-01 | Commit: 03e6eaf8ed0ec88b5a1ff714c853cf8875587827
## Kapsam: src/ + src/trading/ tamamı (yeniden doğrulanmış)

---

## 📋 ÖNCEKİ RAPORUN DURUMU

Önceki rapor (`sniper_cross_context_bug_report.md`) 28 maddeden oluşuyordu. Doğrulama dosyası (`sniper_cross_context_bug_verification.md`) bunların bir kısmını "güncel main'de yok" olarak eleştirdi. **Ancak**, en güncel commit `03e6eaf8` üzerinden tekrar çekilen kodda eleştirilen birçok metodun **gerçekten mevcut olduğu** tespit edildi:

| Metod | Doğrulama Dosyası | Gerçek Durum (03e6eaf8) |
|-------|-------------------|-------------------------|
| `_emergency_close` | "yok" | ✅ VAR (`entry_manager.py:220`) |
| `normalize_order_event` | "yok" | ✅ VAR (`user_data_handler.py:52`) |
| `parse_market_fill` | "yok" | ✅ VAR (`entry_manager.py:178`) |
| `validate_protection_with_actual_fill` | "yok" | ✅ VAR (`entry_manager.py:196`) |
| `_repair_locks` | "yok" | ✅ VAR (`order_manager.py:47`) |
| `tp_unchanged` | "yok" | ✅ VAR (`order_manager.py:169`) |
| `order_qty` | "yok" | ✅ VAR (`entry_manager.py:332`) |

**Sonuç:** Doğrulama dosyasının kendisi de commit/branch tutarsızlığına uğramış görünüyor. Aşağıdaki analiz **doğrudan 03e6eaf8 commit'inin kaynak kodu** üzerinden yapılmıştır.

---

## 🔴 KRİTİK (P0) — Doğrulanmış Gerçek Bug'lar

### BUG-1: `_emergency_close` Her Zaman `success=False` Döner
**Dosya:** `src/trading/entry_manager.py` **Satır:** 220–259
**Durum:** ✅ GERÇEK BUG

```python
async def _emergency_close(self, sym: str, side: str, qty: float, reason: str):
    opp_side = "SELL" if side.upper() == "BUY" else "BUY"
    ...
    try:
        await self._rest.place_market_order(...)
        log.critical("[EMERGENCY] %s acil kapatma gonderildi", sym)
        pt_log(...)
    except Exception as e:
        ...
        return EntryExecutionResult(success=False, ...)
    return EntryExecutionResult(success=False, ...)  # ← ← ← SATIR 259
```

`try` bloğu başarılı bile olsa, metodun sonundaki `return EntryExecutionResult(success=False, ...)` çalışır.

**Etki:** Emergency close emri borsada gerçekleşir ama çağıran (`execute_live_entry`) `success=False` görüp pozisyonu hâlâ açık sanar. Sonraki cycle'larda:
- Tekrar emergency close denenebilir (tutarsız pozisyon durumu)
- SL/TP kurulamamış pozisyonu korumasız bırakabilir
- `execute_live_entry` `EntryExecutionResult(success=False, ...)` döner, çağıran bunu "entry başarısız" olarak yorumlayabilir

**Düzeltme:**
```python
    try:
        await self._rest.place_market_order(...)
        return EntryExecutionResult(success=True, ...)
    except Exception as e:
        return EntryExecutionResult(success=False, error=f"EMERGENCY CLOSE BASARISIZ — {e}")
```

---

### BUG-2: `NormalizedOrderEvent.fill_price` — Property Var Ama `ts_ms` Clock Skew
**Dosya:** `src/trading/user_data_handler.py` + `src/models.py`
**Durum:** ⚠️ KISMEN GERÇEK

`models.py`'de `NormalizedOrderEvent.fill_price` property'si **var** (satır 355–358):
```python
@property
def fill_price(self) -> float:
    if self.avg_price and self.avg_price > 0:
        return self.avg_price
    return self.last_price or 0.0
```

Ama `normalize_order_event()` (satır 52–68) `ts_ms`'yi **sistem saatinden** alıyor:
```python
ts_ms=int(time.time() * 1000),
```

Binance WS event'inin kendi timestamp'i (`E` alanı) kullanılmıyor.

**Etki:** `exit_lifecycle.py`'deki stale event cooldown (30 sn pencere) ve idempotency hesaplamaları yanlış çalışabilir. Clock skew varsa:
- Stale event'ler erken/ geç tetiklenebilir
- `exit_lifecycle.py` `self._stale_cooldown` hesaplaması bozulabilir

**Düzeltme:**
```python
ts_ms=int(raw.get("E", time.time() * 1000)),
```

---

### BUG-3: `trailing_manager.py` ↔ `order_manager.py` Key Tutarsızlığı
**Dosya:** `src/trading/trailing_manager.py` + `src/trading/order_manager.py`
**Durum:** ⚠️ LATENT RISK (Şu an çalışıyor ama kırılgan)

`trailing_manager.py` `orchestrate_trail()` (satır 168–169):
```python
trade["stop_loss"] = float(candidate.sl)
trade["take_profit"] = float(candidate.tp)
```

`order_manager.py` `update_trail_orders()` (satır 169–170):
```python
sl_really_unchanged = abs(new_sl - trade.get("sl", 0.0)) < 1e-8
tp_really_unchanged = abs(new_tp - trade.get("tp", 0.0)) < 1e-8
```

`models.py`'de `ActiveTrade` `stop_loss`/`take_profit` property'leri `sl`/`tp` alias'larıdır (satır 290–301):
```python
@property
def stop_loss(self) -> float:
    return self.sl
@stop_loss.setter
def stop_loss(self, val: float):
    self.sl = val
```

**Ama:** `trailing_manager.py` `compute_trail_candidate()` içinde `self._read_price(trade, "stop_loss", "sl")` kullanılıyor (fallback var). `check_exit()` da aynı şekilde. Yani şu an çalışıyor.

**Risk:** Eğer `trade` bir `ActiveTrade` nesnesi değil, düz `dict` ise `trade["stop_loss"] = x` `sl` alanını **güncellemez**. `order_manager.py` `trade.get("sl", 0.0)` eski değeri okur. Bu, `update_trail_orders()`'un `sl_really_unchanged` hesaplamasını bozar ve gereksiz trailing denemelerine veya yanlış state'e yol açar.

**Düzeltme:** `trailing_manager.py`'deki tüm `trade["stop_loss"]` ve `trade["take_profit"]` atamaları `trade["sl"]` ve `trade["tp"]` olarak değiştirilmeli.

---

### BUG-4: `fvg.py` Frozen Dataclass Mutasyonu
**Dosya:** `src/fvg.py` + `src/models.py`
**Durum:** ❌ YANLIŞ POZİTİF

`models.py`'de `FVG` `@dataclass(frozen=True)` (satır 128). `fvg.py`'de `object.__setattr__(fvg, "invalidated", True)` kullanılıyor.

Python'da `object.__setattr__` frozen dataclass üzerinde **çalışır** (bypass eder). Doğrulama dosyası da bunu doğruladı. Kötü tasarım olabilir ama `TypeError` üretmez.

---

### BUG-5: `state_manager.py` ↔ `session.py` Gün Tutarsızlığı (22:00–00:00)
**Dosya:** `src/state_manager.py` + `src/session.py`
**Durum:** ⚠️ STATE DIVERGENCE RİSKİ

| Modül | 22:00–23:59 UTC | 00:00–21:59 UTC |
|-------|-----------------|-----------------|
| `state_manager._today()` | "yarın" (next day) | "bugün" |
| `session.py cbdr_key` | "bugün" (today) | "bugün" |

`state_manager.py` (satır 31–35):
```python
now = datetime.now(UTC)
if now.hour >= 22:
    return (now + timedelta(days=1)).strftime("%Y-%m-%d")
return now.strftime("%Y-%m-%d")
```

`session.py` (satır 280–283):
```python
cbdr_key = (
    today if h >= sh else (dt - timedelta(days=1)).strftime("%Y-%m-%d")
)
```

22:00'de `session.py` "bugün" derken `state_manager` "yarın" der. `mark_trade_opened()` "yarın" tarihine yazar. Ertesi gün (00:00–21:59) `can_open_trade()` "bugün"e bakar, state'de "yarın" (dünden kalma) kaydı bulur → `date != _today()` → **True döner**.

**Ama:** `session.py` aynı zamanda `trades_today` sıfırlar. `can_open_trade()` state dosyasına bakar, `session.py` belleğe bakar. Eğer bot restart olursa state dosyası "yarın" tarihli kayıt içerir, `get_trade_count_today()` 0 döner (çünkü "bugün" farklı). Bu, restart sonrası **ekstra trade açılmasına** neden olabilir.

**Düzeltme:** `state_manager._today()` ve `session.py` aynı gün mantığını kullanmalı. Ortak bir helper fonksiyon tanımlanmalı.

---

### BUG-6: `recovery_manager.py` `ActiveTrade` Mutasyonu
**Dosya:** `src/trading/recovery_manager.py`
**Durum:** ❌ YANLIŞ POZİTİF

`ActiveTrade` frozen değil (`@dataclass` sadece, `frozen=True` yok). `__setitem__` ve `__getitem__` var. `existing["sl"] = sl_price` ataması çalışır. Doğrulama dosyası da bunu doğruladı.

---

## 🟠 YÜKSEK (P1) — Doğrulanmış Gerçek Bug'lar

### BUG-7: `_emergency_close` Side Parametresi Belirsizliği
**Dosya:** `src/trading/entry_manager.py`
**Durum:** ⚠️ LATENT RISK

Metod imzası `async def _emergency_close(self, sym: str, side: str, qty: float, reason: str)`. `side` parametresi "BUY"/"SELL" bekliyor (`side.upper() == "BUY"`).

`execute_live_entry()` içinden `mkt_side` ("BUY"/"SELL") gönderiliyor → doğru çalışır.

**Ama:** Eğer başka bir yerden "long"/"short" gönderilirse `opp_side` her zaman "BUY" olur (çünkü "LONG".upper() != "BUY"). Bu, short pozisyonu kapatmak için BUY emri gönderir (ters yönde yeni pozisyon açar).

**Düzeltme:** Parametre adı `mkt_side` olmalı ve docstring güncellenmeli.

---

### BUG-8: WS Event Clock Skew
**Dosya:** `src/trading/user_data_handler.py`
**Durum:** ✅ GERÇEK BUG

`normalize_order_event()` (satır 67):
```python
ts_ms=int(time.time() * 1000),
```

Binance WS event'inin `E` alanı (event timestamp) kullanılmıyor. Sistem saati ile sunucu arasında fark (clock skew) olabilir.

**Etki:** `exit_lifecycle.py`'deki `self._stale_cooldown` (30 sn) ve idempotency hesaplamaları yanlış çalışabilir.

**Düzeltme:**
```python
ts_ms=int(raw.get("E", time.time() * 1000)),
```

---

### BUG-9: `repair_protection` Lock Oluşturma Yeri
**Dosya:** `src/trading/order_manager.py`
**Durum:** ⚠️ LATENT RISK

`repair_protection()` (satır 360):
```python
lock = self._repair_locks.setdefault(sym, asyncio.Lock())
```

`asyncio.Lock()` event loop'da oluşturulmalıdır. `__init__`'te `self._repair_locks = {}` başlatılıyor ama lock'lar lazy oluşturuluyor. Nadiren görülebilir ama bot startup sırasında veya test ortamında race condition'a neden olabilir.

**Düzeltme:** `defaultdict(asyncio.Lock)` veya `__init__`'te tüm semboller için önceden oluşturma.

---

### BUG-10: `_bump_to_min_notional` Float Precision
**Dosya:** `src/trading/entry_manager.py`
**Durum:** ✅ GERÇEK BUG (Düşük olasılıklı)

```python
bumped = math.ceil(min_qty_n / step) * step
bumped = round(bumped, 8)
```

`step` float olduğunda float precision hatalarına açık. Örneğin:
- `step = 0.01`, `min_qty_n = 1.235`
- `1.235 / 0.01 = 123.49999999999999` (float)
- `math.ceil(...) = 124`
- `124 * 0.01 = 1.24` ← doğru

Ama bazı edge case'lerde `bumped * price < min_notional` kalabilir.

**Düzeltme:** `Decimal` kullanılmalı:
```python
from decimal import Decimal, ROUND_CEILING
step_d = Decimal(str(step))
min_qty_d = Decimal(str(min_qty_n))
bumped = float((min_qty_d / step_d).to_integral_value(rounding=ROUND_CEILING) * step_d)
```

---

### BUG-11: `exit_lifecycle.py` Duplicate `pending_exit_*` Normalization
**Dosya:** `src/trading/exit_lifecycle.py`
**Durum:** ✅ GERÇEK (Zararsız ama redundant)

`execute()` içinde aynı `pending_exit_*` alanları iki ayrı blokta normalize ediliyor:

1. İlk blok (satır 130–140):
```python
if trade.get("pending_exit_price"):
    trade["exit_price"] = trade["pending_exit_price"]
    ...
```

2. İkinci blok (satır 142–152):
```python
if trade.get("pending_exit_price") is not None:
    trade["exit_price"] = trade["pending_exit_price"]
    ...
```

İlk blok `pending_exit_price` truthy ise çalışır, ikinci blok `is not None` ise çalışır. Eğer `pending_exit_price = 0.0` ise ilk blok atlar, ikincisi çalışır. Ama genelde redundant.

**Düzeltme:** Tek blokta birleştirilmeli.

---

### BUG-12: Idempotency Key Collision
**Dosya:** `src/trading/exit_lifecycle.py`
**Durum:** ✅ GERÇEK BUG

```python
_trade_id = f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}"
```

Aynı bar'da aynı fiyattan iki farklı trade (örneğin stoplanıp tekrar entry) aynı key'e sahip olur. İkincisi engellenebilir.

**Düzeltme:** `entry_timestamp` veya `trade_id` (UUID) eklenmeli:
```python
_trade_id = f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}_{trade.get('entry_timestamp', 0)}"
```

---

### BUG-13: `parse_market_fill` `quote_qty` Redundant Hesaplama
**Dosya:** `src/trading/entry_manager.py`
**Durum:** ✅ GERÇEK (Zararsız, redundant)

```python
quote_qty = float(response.get("cummulativeQuoteQty", 0))
if quote_qty <= 0:
    quote_qty = float(response.get("cumQuote", 0))
if quote_qty <= 0:
    quote_qty = float(response.get("quoteQty", 0))
if quote_qty <= 0 and avg_price > 0 and executed_qty > 0:
    quote_qty = avg_price * executed_qty
```

`quote_qty` ilk satırda `cummulativeQuoteQty`'den alınıyor, sonra `cumQuote`'dan, sonra `quoteQty`'den. Sonra da `avg_price * executed_qty` hesaplanıyor. Redundant ama zararsız.

---

### BUG-14: `user_data_handler.py` Legacy vs Normalized Handler Tutarsızlığı
**Dosya:** `src/trading/user_data_handler.py`
**Durum:** ⚠️ LATENT RISK

- **Normalized handler** (satır 104): `trade["pending_exit_price"] = price`
- **Legacy handler** (satır 230): `trade["exit_price"] = price`

`exit_lifecycle.py` sadece `pending_exit_*` alanlarını normalize eder (satır 130–152). Legacy handler aktifse (`WS_EVENT_NORMALIZATION_ENABLED=False`):
- `trade["exit_price"]` doğrudan doldurulur
- `trade["pending_exit_price"]` `None` kalır
- `exit_lifecycle.py`'deki ilk `if trade.get("pending_exit_price"):` atlar (çünkü `None`)
- İkinci `if trade.get("pending_exit_price") is not None:` da atlar (çünkü `None`)
- Ama legacy handler zaten `trade["exit_price"]` doldurduğu için `exit_lifecycle.py`'nin sonraki adımları çalışır

**Ama:** `exit_lifecycle.py`'deki idempotency guard (`self._exit_log`) legacy handler için de çalışır. `pending_exit_*` alanları sadece WS-FALLBACK ve repair senaryolarında önemli.

**Risk:** Legacy handler + WS-FALLBACK senaryosunda `pending_exit_*` alanları boş kalabilir, `exit_lifecycle.py` stale event olarak işaretleyebilir.

**Düzeltme:** Legacy handler da `pending_exit_*` pattern'ini kullanmalı.

---

### BUG-15: `entry_manager.py` `calculate_sl_tp` Fallback Risk Distance
**Dosya:** `src/trading/entry_manager.py`
**Durum:** ⚠️ DESIGN RISK

Fallback SL: `raw_sl = entry_price - risk_pts * 2`. Sonra `apply_min_sl_distance` çağrılır. Eğer `min_sl_distance < risk_pts * 2` ise `sl = raw_sl` kalır. `risk_dist = entry_price - sl = risk_pts * 2`.

Ama `calculate_qty()`'de `risk_dist` parametresi kullanılıyor. Eğer `calculate_sl_tp()`'den dönen `risk_dist` (yani `abs(sl - entry_price)`) `calculate_qty()`'ye verilen `risk_pts`'den farklıysa, backtest parity bozulabilir.

**Düzeltme:** `risk_pts` ve `risk_dist` aynı canonical değer üzerinden hesaplanmalı.

---

### BUG-16: `session.py` Dead Code
**Dosya:** `src/session.py`
**Durum:** ✅ GERÇEK (Zararsız)

```python
if isinstance(dt, int):
    return SessionPhase.CLOSED
```

`dt` hiçbir zaman `int` gelmez (her zaman `datetime`). Dead code.

---

### BUG-17: `CircuitBreaker.is_open` Race Condition
**Dosya:** `src/bot_infra.py`
**Durum:** ✅ GERÇEK (Düşük risk)

```python
@property
def is_open(self) -> bool:
    if self._failure_count < self._failure_threshold:
        return False
    elapsed = time.time() - self._open_time
    if elapsed >= self._recovery_timeout:
        return False
    return True
```

Lock'sız okuma. `record_failure` ile aynı anda okunursa eski değer görülebilir. Async tek event loop'ta risk düşük ama teknik olarak race condition.

---

### BUG-18: `validate_protection_with_actual_fill` Epsilon
**Dosya:** `src/trading/entry_manager.py`
**Durum:** ✅ GERÇEK (Düşük risk)

```python
epsilon = float(tick_size) * epsilon_ticks
```

`tick_size` `Decimal` olarak geliyor, `float(tick_size)` precision kaybına uğrayabilir. Örneğin `Decimal("0.01")` → `float` → `0.01` (doğru), ama `Decimal("0.00000001")` → `float` → `1e-08` (doğru). Genelde güvenli ama edge case'lerde sorun olabilir.

**Düzeltme:** `Decimal` ile kıyaslama:
```python
epsilon = float(tick_size) * epsilon_ticks  # Şu anki hali
# Düzeltme: Decimal comparison
# if Decimal(str(actual_fill)) - Decimal(str(sl)) <= Decimal(str(tick_size)) * epsilon_ticks:
```

---

### BUG-19: `recovery_manager.py` Nested Function Overhead
**Dosya:** `src/trading/recovery_manager.py`
**Durum:** ✅ GERÇEK (Design issue)

`_is_max_qty_error`, `_try_close_position_sl_tp`, `_try_split_qty_sl_tp` her `recover_positions()` çağrısında yeniden tanımlanıyor. Closure variable capture riski ve performans overhead (minimal ama var).

**Düzeltme:** Sınıf metodu olarak taşınmalı.

---

### BUG-20: `order_manager.py` `update_trail_orders` `tp_unchanged` Edge Case
**Dosya:** `src/trading/order_manager.py`
**Durum:** ✅ GERÇEK (Edge case)

```python
tp_unchanged = abs(new_tp - old_tp_price) < 1e-8
```

Eğer `old_tp_price = 0.0` (ilk trailing öncesi) ve `new_tp = 0.0` ise (ki bu bir bug olur), `tp_unchanged = True` olur. `tp_ok = True` yapılır ama TP hiç kurulmamış olabilir.

**Ama:** `new_tp` `calculate_sl_tp()`'den gelir ve 0.0 olmamalı (validation var). Yani bu edge case çok nadir.

---

## 🟡 ORTA (P2) — Doğrulanmış Gerçek Bug'lar

### BUG-21: `entry_manager.py` `execute_live_entry` `order_qty` Tutarsızlığı
**Dosya:** `src/trading/entry_manager.py`
**Durum:** ✅ GERÇEK

```python
order_qty = actual_qty if actual_qty > 0 else valid_qty
```

`actual_qty` borsadan gelen (precision'sız), `valid_qty` precision uygulanmış. SL/TP emirlerinde `order_qty` kullanılıyor. Eğer `actual_qty` precision farkı varsa, Binance `LOT_SIZE` hatası verebilir.

**Düzeltme:** `order_qty` precision uygulanmış değer olmalı:
```python
order_qty = await self._rest.apply_amount_precision(sym, actual_qty) if actual_qty > 0 else valid_qty
```

---

### BUG-22: `exit_lifecycle.py` `_commit_confirmed_exit` `trade` Pop Sonrası Kullanım
**Dosya:** `src/trading/exit_lifecycle.py`
**Durum:** ❌ YANLIŞ POZİTİF

```python
trade = self._active_trades.pop(sym, None)
if not trade:
    return False
```

Pop sonrası `None` kontrolü var. Güvenli.

---

### BUG-23: `session_router.py` `should_trade` Zehirli Bölge
**Dosya:** `src/session_router.py`
**Durum:** ✅ GERÇEK (Design issue)

```python
if cbdr_width_pct is not None:
    cbdr_mult = get_cbdr_multiplier(symbol, cbdr_width_pct)
    if cbdr_mult == 0.0:
        return False, "..."
return True, ""
```

`cbdr_width_pct = None` ise `cbdr_mult` hesaplanmaz ve `True` döner. CBDR ölçülemiyorsa (veri yoksa) trade'e izin vermek güvenli değil.

**Düzeltme:** `cbdr_width_pct is None` ise `False` dönmeli (fail-closed).

---

### BUG-24: `fvg.py` `cleanup_fvgs` Yaş Hesaplama
**Dosya:** `src/fvg.py`
**Durum:** ❌ BUG DEĞİL (Politika)

```python
not (f.filled and (current_abs - f.real_index) > max_age)
not (not f.filled and (current_abs - f.real_index) > max_age * 2)
```

Asimetrik ama açıkça kodlanmış politika. Filled FVG'ler 500 bar, unfilled FVG'ler 1000 bar yaşar.

---

### BUG-25: `risk_manager.py` `update_peak` Initial Equity
**Dosya:** `src/risk_manager.py`
**Durum:** ✅ GERÇEK GÜVENLİK BUG'I

```python
def _load_state(self) -> dict:
    try:
        with lock:
            with open(self.state_file, "r") as f:
                return json.load(f)
    except json.JSONDecodeError:
        return {"peak_equity": 0.0, "is_circuit_broken": False}
```

Bozuk JSON'da `peak_equity = 0.0` olur. `get_current_dd()`:
```python
if self.peak_equity <= 0:
    return 0.0
```

Devre kesici asla tetiklenmez (`current_dd = 0.0` her zaman).

**Düzeltme:** Bozuk state'de `initial_equity` fallback:
```python
except json.JSONDecodeError:
    logger.error("State dosyasi bozuk, initial_equity ile baslatiliyor.")
    return {"peak_equity": self.initial_equity, "is_circuit_broken": False}
```

---

## 🟢 DÜŞÜK (P3) — Stil / Dokümantasyon

### BUG-26: `bot_infra.py` `_RateLimiter` İlk Çağrı
**Dosya:** `src/bot_infra.py`
**Durum:** ❌ BUG DEĞİL

`self._last = 0.0` başlangıçta. İlk çağrıda `wait = interval - (now - 0.0)` negatif olur, bekleme yapmaz. Bu beklenen davranış.

---

### BUG-27: `paper_trade_logger.py` `_RUN_ID` Race Condition
**Dosya:** `src/paper_trade_logger.py`
**Durum:** DOĞRULANAMADI

Dosya çekilmedi. Async tek loop'ta interleaving mümkün olabilir ama kanıt yok.

---

### BUG-28: `event_log.py` `cleanup_old_event_logs` Dizin Yoksa
**Dosya:** `src/event_log.py`
**Durum:** ❌ YANLIŞ POZİTİF

`os.listdir(_OUTPUT_DIR)` `_OUTPUT_DIR` yoksa `FileNotFoundError`. `try/except` var. Güvenli.

---

## 📊 DOĞRULANMIŞ ÖZET TABLO

| ID | Öncelik | Dosya | Doğrulama | Etki |
|----|---------|-------|-----------|------|
| BUG-1 | P0 | `entry_manager.py` | ✅ GERÇEK | Emergency close başarısız sanılır, pozisyon açık kalır |
| BUG-2 | P0 | `user_data_handler.py` | ⚠️ KISMEN (ts_ms) | Clock skew, stale event cooldown bozulabilir |
| BUG-3 | P0 | `trailing_manager.py` | ⚠️ LATENT | Dict trade ise `sl`/`tp` güncellenmez |
| BUG-4 | — | `fvg.py` | ❌ YANLIŞ | `object.__setattr__` frozen'da çalışır |
| BUG-5 | P0 | `state_manager.py` | ⚠️ STATE RISK | Restart sonrası ekstra trade riski |
| BUG-6 | — | `recovery_manager.py` | ❌ YANLIŞ | `ActiveTrade` frozen değil |
| BUG-7 | P1 | `entry_manager.py` | ⚠️ LATENT | Yanlış side ile emergency close (ters pozisyon) |
| BUG-8 | P1 | `user_data_handler.py` | ✅ GERÇEK | Stale event cooldown yanlış hesaplanır |
| BUG-9 | P1 | `order_manager.py` | ⚠️ LATENT | `asyncio.Lock()` oluşturma zamanı riskli |
| BUG-10 | P1 | `entry_manager.py` | ✅ GERÇEK | `min_notional` altında kalma riski |
| BUG-11 | P1 | `exit_lifecycle.py` | ✅ GERÇEK | Duplicate kod, redundant |
| BUG-12 | P1 | `exit_lifecycle.py` | ✅ GERÇEK | Aynı bar/fiyat tekrar trade engellenebilir |
| BUG-13 | P1 | `entry_manager.py` | ✅ GERÇEK | `quote_qty` redundant hesaplama |
| BUG-14 | P1 | `user_data_handler.py` | ⚠️ LATENT | Legacy/Normalized handler farklı state yazıyor |
| BUG-15 | P1 | `entry_manager.py` | ⚠️ DESIGN | Backtest parity riski |
| BUG-16 | P2 | `session.py` | ✅ GERÇEK | `isinstance(dt, int)` dead code |
| BUG-17 | P2 | `bot_infra.py` | ✅ GERÇEK | `is_open` lock'sız okunuyor |
| BUG-18 | P2 | `entry_manager.py` | ✅ GERÇEK | `float(Decimal)` epsilon hesabı |
| BUG-19 | P2 | `recovery_manager.py` | ✅ GERÇEK | Nested function overhead |
| BUG-20 | P2 | `order_manager.py` | ✅ GERÇEK | `tp_unchanged` edge case |
| BUG-21 | P2 | `entry_manager.py` | ✅ GERÇEK | `actual_qty` vs `valid_qty` precision farkı |
| BUG-22 | P2 | `exit_lifecycle.py` | ❌ YANLIŞ | Pop sonrası None kontrolü var |
| BUG-23 | P2 | `session_router.py` | ✅ GERÇEK | `cbdr_width_pct=None` güvenli değil |
| BUG-24 | P2 | `fvg.py` | ❌ BUG DEĞİL | Asimetrik FVG yaşlandırma (politika) |
| BUG-25 | P2 | `risk_manager.py` | ✅ GERÇEK | Bozuk state → devre kesici çalışmaz |
| BUG-26 | P3 | `bot_infra.py` | ❌ BUG DEĞİL | Rate limiter ilk çağrı hemen geçer (normal) |
| BUG-27 | P3 | `paper_trade_logger.py` | DOĞRULANAMADI | Global `_RUN_ID` riski belirsiz |
| BUG-28 | P3 | `event_log.py` | ❌ YANLIŞ | Güvenli, false positive |

---

## 🔗 ÇAPRAZ BAĞLAM ETKİLEŞİM HARİTASI (Doğrulanmış)

```
signal_engine.py ──► entry_manager.py ──► order_manager.py
       │                    │                    │
       │                    │                    ▼
       │                    │            protection_lifecycle.py
       │                    │                    │
       ▼                    ▼                    ▼
session.py ◄──────── exit_lifecycle.py ◄── user_data_handler.py
       │                    │                    │
       │                    │                    ▼
       │                    │            websocket.py
       │                    │                    │
       ▼                    ▼                    ▼
state_manager.py ◄── recovery_manager.py ◄── bot_binance.py
       │                    │                    │
       │                    │                    ▼
       │                    │            trailing_manager.py
       │                    │                    │
       ▼                    ▼                    ▼
fvg.py ◄──────────── retrace_state.py ◄── indicators.py
```

**En Riskli Akışlar (Doğrulanmış):**
1. **Entry → Emergency Close:** BUG-1 (emergency close başarısız sanılır) + BUG-7 (yanlış side riski)
2. **WS Fill → Exit:** BUG-8 (clock skew) + BUG-12 (idempotency collision) + BUG-14 (legacy/normalized tutarsızlığı)
3. **Trailing → State:** BUG-3 (dict trade ise state divergence)
4. **Restart → Recovery:** BUG-5 (state gün tutarsızlığı) + BUG-25 (bozuk risk state)
5. **Risk State:** BUG-25 (devre kesici devre dışı)

---

## 🛠️ ÖNERİLEN DÜZELTME SIRASI (Doğrulanmış)

1. **BUG-1** (Emergency close `success=False`) — 1 satır, en kritik etki
2. **BUG-25** (Bozuk risk state) — Güvenlik bug'ı, devre kesici devre dışı
3. **BUG-12** (Idempotency collision) — Tek satır, trade kaybı riski
4. **BUG-8** (Clock skew) — `raw.get("E", ...)` kullan
5. **BUG-5** (Gün tutarsızlığı) — Ortak helper
6. **BUG-10** (Float precision) — `Decimal` kullan
7. **BUG-21** (Order qty precision) — `apply_amount_precision`
8. **BUG-23** (CBDR None) — Fail-closed
9. **BUG-3** (Trailing key) — `sl`/`tp` canonical alanları
10. **BUG-11** (Duplicate normalization) — Tek blok
