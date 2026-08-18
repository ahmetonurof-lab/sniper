# SNIPER BOT — ÇAPRAZ BAĞLAM TESTİ & BUG ANALİZİ RAPORU
## Tarih: 2026-08-01 | Kapsam: src/ + src/trading/ tamamı

---

## 🔴 KRİTİK (P0) — Üretimde Para Kaybına / Crash'e Neden Olur

### BUG-1: `_emergency_close` Her Zaman `success=False` Döner
**Dosya:** `src/trading/entry_manager.py`
**Metod:** `_emergency_close()`

```python
# SATIR ~320-340
    try:
        await self._rest.place_market_order(...)
        log.critical("[EMERGENCY] %s acil kapatma gonderildi", sym)
        pt_log(...)
    except Exception as e:
        ...
        return EntryExecutionResult(success=False, ...)
    return EntryExecutionResult(success=False, ...)  # ← ← ← HER ZAMAN ÇALIŞIR
```

**Etki:** Emergency close emri borsada gerçekleşir ama çağıran `execute_live_entry` `success=False` görüp pozisyonu hâlâ açık sanar. Sonraki cycle'larda tekrar emergency close denenebilir veya SL/TP kurulamamış pozisyonu korumasız bırakır.

**Düzeltme:**
```python
    try:
        await self._rest.place_market_order(...)
        return EntryExecutionResult(success=True, ...)
    except Exception as e:
        return EntryExecutionResult(success=False, error=f"EMERGENCY CLOSE BASARISIZ — {e}")
```

---

### BUG-2: `NormalizedOrderEvent.fill_price` Attribute'u Yok
**Dosya:** `src/trading/user_data_handler.py`
**Metod:** `_on_order_update_normalized()` — `price = evt.fill_price`

`normalize_order_event()` oluşturulan nesnenin alanları: `avg_price`, `last_price`, `cum_qty`, `cum_quote_qty`, `ts_ms`, `raw`. **`fill_price` yok.**

**Etki:** WS normalization aktifken her `FILLED`/`TRIGGERED` event'inde `AttributeError`. Bot canlı emir takibini tamamen kaybeder.

**Düzeltme:** `price = evt.avg_price or evt.last_price or 0.0`

---

### BUG-3: `trailing_manager.py` Key Tutarsızlığı — Trailing Stop Çalışmaz
**Dosya:** `src/trading/trailing_manager.py`
**Metod:** `orchestrate_trail()`

```python
trade["stop_loss"] = float(candidate.sl)   # ← yanlış key
trade["take_profit"] = float(candidate.tp)  # ← yanlış key
```

Tüm diğer modüller (`entry_manager`, `exit_lifecycle`, `order_manager`, `recovery_manager`, `signal_engine`, `retrace_state`) **`trade["sl"]`** ve **`trade["tp"]`** kullanır.

**Etki:** Trailing stop hesaplanır, borsada emir güncellenir ama `trade` dict'indeki SL/TP değerleri eski kalır. Sonraki cycle'larda yanlış fiyatlarla exit kontrolü yapılır, repair/reverify eski değerleri kullanır.

**Düzeltme:**
```python
trade["sl"] = float(candidate.sl)
trade["tp"] = float(candidate.tp)
```

---

### BUG-4: `fvg.py` Frozen Dataclass Mutasyonu
**Dosya:** `src/fvg.py`
**Metod:** `update_fvg_states()`
**Satır:** `object.__setattr__(fvg, "invalidated", True)`

Eğer `models.py`'de `FVG` `@dataclass(frozen=True)` tanımlıysa, `object.__setattr__` Python 3.8+ frozen dataclass'larda **engellenir**.

**Etki:** `TypeError: cannot assign to field 'invalidated'`. FVG state makinesi çalışmaz, sweep/FVG trigger'ları bozulur.

**Düzeltme:** `FVG` frozen değilse sorun yok; frozen ise `__setattr__` yerine yeni nesne oluşturulmalı veya frozen kaldırılmalı.

---

### BUG-5: `state_manager.py` ↔ `session.py` Gün Tutarsızlığı (22:00-00:00)
**Dosyalar:** `src/state_manager.py`, `src/session.py`

| Modül | 22:00-23:59 UTC | 00:00-21:59 UTC |
|-------|-----------------|-----------------|
| `state_manager._today()` | "yarın" | "bugün" |
| `session.py cbdr_key` | "bugün" | "bugün" |

**Etki:** 22:00-00:00 arasında `session.py` yeni CBDR başlatırken `state_manager` "yarın" olarak kaydeder. Ertesi gün state tutarsızlığı → günde 2 trade riski veya state kaybı.

**Düzeltme:** Ortak `_today()` helper'ı kullanılmalı; `session.py`'deki `cbdr_key` mantığı `state_manager`'a taşınmalı.

---

### BUG-6: `recovery_manager.py` `ActiveTrade` Mutasyonu (Eğer Frozen)
**Dosya:** `src/trading/recovery_manager.py`
**Satır:** `existing["sl"] = sl_price`

`existing` bir `ActiveTrade` nesnesi. Eğer `models.py`'de frozen dataclass ise atama `TypeError` atar.

**Etki:** Bot restart sonrası pozisyon kurtarma çalışmaz, açık pozisyonlar envantere alınamaz.

**Not:** `models.py`'de `ActiveTrade` tanımını görmedim. Eğer frozen değilse false positive'dir.

---

## 🟠 YÜKSEK (P1) — Race Condition / Tutarsızlık / Yanlış Hesaplama

### BUG-7: `_emergency_close` Side Parametresi Belirsizliği
**Dosya:** `src/trading/entry_manager.py`

Metod imzası `side` bekliyor ama `opp_side = "SELL" if side.upper() == "BUY" else "BUY"` "BUY"/"SELL" bekliyor. Eğer "long"/"short" gelirse `opp_side` her zaman "BUY" olur → short pozisyonu kapatmak için BUY emri (ters yönde yeni pozisyon).

**Düzeltme:** Parametre adı `mkt_side` olmalı veya "long"/"short" kabul edilmeli.

---

### BUG-8: WS Event Clock Skew
**Dosya:** `src/trading/user_data_handler.py`
**Satır:** `ts_ms=int(time.time() * 1000)`

Binance'in kendi timestamp'i (`E` alanı) kullanılmıyor. Sistem saati ile sunucu arasında fark olabilir. Stale event cooldown (30 sn) ve idempotency hesaplamaları bozulabilir.

**Düzeltme:** `ts_ms=int(raw.get("E", time.time() * 1000))`

---

### BUG-9: `repair_protection` Lock Oluşturma Yeri
**Dosya:** `src/trading/order_manager.py`
**Satır:** `lock = self._repair_locks.setdefault(sym, asyncio.Lock())`

`asyncio.Lock()` event loop dışında oluşturulursa hata verebilir. Startup sırasında race condition riski.

**Düzeltme:** `__init__`'te `defaultdict(asyncio.Lock)` veya lazy factory kullanılmalı.

---

### BUG-10: `_bump_to_min_notional` Float Precision
**Dosya:** `src/trading/entry_manager.py`

`bumped = math.ceil(min_qty_n / step) * step` — float precision hatalarına açık. `Decimal` kullanılmamış.

**Etki:** Çok nadirde olsa `bumped * price < min_notional` kalabilir, Binance `MIN_NOTIONAL` hatası döner.

---

### BUG-11: `exit_lifecycle.py` Duplicate `pending_exit_*` Normalization
**Dosya:** `src/trading/exit_lifecycle.py`
**Metod:** `execute()`

Aynı `pending_exit_*` alanları iki ayrı blokta normalize ediliyor (Patch Set 4 migration artığı). Redundant ama zararsız.

---

### BUG-12: Idempotency Key Collision
**Dosya:** `src/trading/exit_lifecycle.py`
**Satır:** `_trade_id = f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}"`

Aynı bar'da aynı fiyattan iki farklı trade (örneğin stoplanıp tekrar entry) aynı key'e sahip olur. İkincisi engellenebilir.

**Düzeltme:** `entry_timestamp` veya `trade_id` (UUID) eklenmeli.

---

### BUG-13: `parse_market_fill` `quote_qty` Redundant Hesaplama
**Dosya:** `src/trading/entry_manager.py`
**Metod:** `parse_market_fill()`

`quote_qty` iki kez hesaplanıyor. İkincisi redundant.

---

### BUG-14: `user_data_handler.py` Legacy vs Normalized Handler Tutarsızlığı
**Dosya:** `src/trading/user_data_handler.py`

- **Normalized:** `trade["pending_exit_price"] = price` (gecikmeli commit)
- **Legacy:** `trade["exit_price"] = price` (doğrudan mutasyon)

`exit_lifecycle.py` sadece `pending_exit_*` alanlarını normalize eder. Legacy handler aktifse `exit_lifecycle.py`'deki idempotency guard çalışmaz (çünkü `pending_exit_price` None'dur, `exit_price` doludur).

**Düzeltme:** Legacy handler da `pending_exit_*` pattern'ini kullanmalı.

---

### BUG-15: `entry_manager.py` `calculate_sl_tp` Fallback Risk Distance
**Dosya:** `src/trading/entry_manager.py`
**Metod:** `calculate_sl_tp()`

Fallback SL: `raw_sl = entry_price - risk_pts * 2`. Sonra `apply_min_sl_distance` çağrılır. Eğer `min_sl_distance < risk_pts * 2` ise `sl = raw_sl` kalır. `risk_dist = entry_price - sl = risk_pts * 2`. Ama `risk_pts` parametresi `calculate_qty`'de kullanılan risk distance'dan farklı olabilir.

**Etki:** Backtest parity bozulabilir. `risk_pts` ve `risk_dist` aynı şey mi emin olunmalı.

---

### BUG-16: `session.py` Dead Code
**Dosya:** `src/session.py`
**Metod:** `detect_phase()`
**Satır:** `if isinstance(dt, int): return SessionPhase.CLOSED`

`dt` hiçbir zaman `int` gelmez (her zaman `datetime`). Dead code.

---

### BUG-17: `CircuitBreaker.is_open` Race Condition
**Dosya:** `src/bot_infra.py`
**Metod:** `CircuitBreaker.is_open`

Lock'sız okuma. `record_failure` ile aynı anda okunursa eski değer görülebilir. Zararsız ama teknik olarak race condition.

---

### BUG-18: `entry_manager.py` `validate_protection_with_actual_fill` Epsilon
**Dosya:** `src/trading/entry_manager.py`

`epsilon = float(tick_size) * epsilon_ticks` — `float(Decimal)` precision kaybına uğrayabilir. `Decimal` ile kıyaslama daha güvenli olur.

---

### BUG-19: `recovery_manager.py` Nested Function Overhead
**Dosya:** `src/trading/recovery_manager.py`

`_is_max_qty_error`, `_try_close_position_sl_tp`, `_try_split_qty_sl_tp` her `recover_positions()` çağrısında yeniden tanımlanıyor. Closure variable capture riski ve performans overhead (minimal ama var).

**Düzeltme:** Sınıf metodu olarak taşınmalı.

---

### BUG-20: `order_manager.py` `update_trail_orders` `tp_unchanged` Edge Case
**Dosya:** `src/trading/order_manager.py`

`tp_unchanged = abs(new_tp - old_tp_price) < 1e-8` — eğer `old_tp_price = 0.0` (ilk trailing öncesi) ve `new_tp = 0.0` ise (ki bu bir bug olur), `tp_unchanged = True` olur. `tp_ok = True` yapılır ama TP hiç kurulmamış olabilir.

---

## 🟡 ORTA (P2) — Mimari / Clean Code

### BUG-21: `entry_manager.py` `execute_live_entry` `order_qty` Tutarsızlığı
**Dosya:** `src/trading/entry_manager.py`

`order_qty = actual_qty if actual_qty > 0 else valid_qty` — `actual_qty` borsadan gelen (precision'sız), `valid_qty` precision uygulanmış. SL/TP emirlerinde `actual_qty` kullanılıyor ama precision farkı olabilir.

---

### BUG-22: `exit_lifecycle.py` `_commit_confirmed_exit` `trade` Pop Sonrası Kullanım
**Dosya:** `src/trading/exit_lifecycle.py`

`trade = self._active_trades.pop(sym, None)` sonrası `trade` kullanılıyor. Eğer `pop` `None` dönerse (başka bir akış zaten pop etmiş), `None` üzerinden attribute erişimi `AttributeError` atar.

**Düzeltme:** `if not trade: return False` eklendiği için şu an güvenli gibi görünüyor ama `trade` pop sonrası `None` kontrolü var.

---

### BUG-23: `session_router.py` `should_trade` Zehirli Bölge
**Dosya:** `src/session_router.py`

`cbdr_mult == 0.0` ise `False` döner ama `cbdr_width_pct` `None` ise `cbdr_mult` hesaplanmaz. `profile` varsa ve `cbdr_width_pct` `None` ise `True` döner. Bu beklenen davranış mı?

---

### BUG-24: `fvg.py` `cleanup_fvgs` Yaş Hesaplama
**Dosya:** `src/fvg.py`

```python
not (f.filled and (current_abs - f.real_index) > max_age)
```

`filled` True ve yaş > `max_age` (500 bar) ise temizlenir. Ama `filled` False ve yaş > `max_age * 2` (1000 bar) ise temizlenir. Bu asimetrik yaşlandırma mantığı doğru mu?

---

### BUG-25: `risk_manager.py` `update_peak` Initial Equity
**Dosya:** `src/risk_manager.py`

`peak_equity` initial değer constructor'dan geliyor ama state dosyası varsa dosyadaki değer kullanılıyor. Eğer state dosyası bozuksa (`JSONDecodeError`) `peak_equity = 0.0` olur. `get_current_dd` `peak_equity <= 0` ise 0.0 döner. Devre kesici asla tetiklenmez.

---

## 🟢 DÜŞÜK (P3) — Stil / Dokümantasyon

### BUG-26: `bot_infra.py` `_RateLimiter` İlk Çağrı
**Dosya:** `src/bot_infra.py`

`self._last = 0.0` başlangıçta. İlk çağrıda `wait = interval - (now - 0.0)` negatif olur, bekleme yapmaz. Bu bir bug değil ama rate limiter'ın ilk çağrıda hemen geçmesi beklenmeyebilir.

---

### BUG-27: `paper_trade_logger.py` `_RUN_ID` Race Condition
**Dosya:** `src/paper_trade_logger.py`

`_RUN_ID` global değişken. Paralel trade'lerde race condition olabilir (minimal risk).

---

### BUG-28: `event_log.py` `cleanup_old_event_logs` Dizin Yoksa
**Dosya:** `src/event_log.py`

`os.listdir(_OUTPUT_DIR)` `_OUTPUT_DIR` yoksa `FileNotFoundError`. `try/except` var ama `os.path.isdir` kontrolü `return` yapıyor. Güvenli.

---

## 📊 ÖZET TABLO

| ID | Öncelik | Dosya | Kategori | Etki |
|----|---------|-------|----------|------|
| BUG-1 | P0 | `entry_manager.py` | Logic | Emergency close başarısız sanılır, pozisyon açık kalır |
| BUG-2 | P0 | `user_data_handler.py` | Crash | WS normalization aktifken `AttributeError`, emir takibi durur |
| BUG-3 | P0 | `trailing_manager.py` | Key Tutarsızlığı | Trailing stop çalışmaz, eski SL/TP ile işlem yapılır |
| BUG-4 | P0 | `fvg.py` | Crash | Frozen dataclass ise `TypeError`, FVG motoru durur |
| BUG-5 | P0 | `state_manager.py` + `session.py` | State | Günde 2 trade riski, state tutarsızlığı |
| BUG-6 | P0 | `recovery_manager.py` | Crash | Restart sonrası pozisyon kurtarma çalışmaz |
| BUG-7 | P1 | `entry_manager.py` | Logic | Yanlış side ile emergency close (ters pozisyon) |
| BUG-8 | P1 | `user_data_handler.py` | Clock Skew | Stale event cooldown yanlış hesaplanır |
| BUG-9 | P1 | `order_manager.py` | Race Cond. | `asyncio.Lock()` oluşturma zamanı riskli |
| BUG-10 | P1 | `entry_manager.py` | Precision | `min_notional` altında kalma riski |
| BUG-11 | P1 | `exit_lifecycle.py` | Redundant | Duplicate kod, zararsız |
| BUG-12 | P1 | `exit_lifecycle.py` | Logic | Aynı bar/fiyat tekrar trade engellenebilir |
| BUG-13 | P1 | `entry_manager.py` | Redundant | `quote_qty` iki kez hesaplanıyor |
| BUG-14 | P1 | `user_data_handler.py` | Tutarsızlık | Legacy/Normalized handler farklı state yazıyor |
| BUG-15 | P1 | `entry_manager.py` | Logic | Backtest parity riski |
| BUG-16 | P2 | `session.py` | Dead Code | `isinstance(dt, int)` hiç True olmaz |
| BUG-17 | P2 | `bot_infra.py` | Race Cond. | `is_open` lock'sız okunuyor |
| BUG-18 | P2 | `entry_manager.py` | Precision | `float(Decimal)` epsilon hesabı |
| BUG-19 | P2 | `recovery_manager.py` | Design | Nested function overhead |
| BUG-20 | P2 | `order_manager.py` | Edge Case | `tp_unchanged` True olabilir ama TP hiç kurulmamış |
| BUG-21 | P2 | `entry_manager.py` | Tutarsızlık | `actual_qty` vs `valid_qty` precision farkı |
| BUG-22 | P2 | `exit_lifecycle.py` | Safety | Pop sonrası None kontrolü var ama riskli |
| BUG-23 | P2 | `session_router.py` | Logic | `cbdr_width_pct=None` davranışı belirsiz |
| BUG-24 | P2 | `fvg.py` | Logic | Asimetrik FVG yaşlandırma |
| BUG-25 | P2 | `risk_manager.py` | Safety | Bozuk state → devre kesici çalışmaz |
| BUG-26 | P3 | `bot_infra.py` | Design | Rate limiter ilk çağrı hemen geçer |
| BUG-27 | P3 | `paper_trade_logger.py` | Race Cond. | Global `_RUN_ID` |
| BUG-28 | P3 | `event_log.py` | Safety | Güvenli, false positive |

---

## 🔗 ÇAPRAZ BAĞLAM ETKİLEŞİM HARİTASI

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

**En Riskli Akışlar:**
1. **Entry → Emergency Close:** BUG-1 + BUG-7 (emergency close hem yanlış side hem de başarısız sanılır)
2. **WS Fill → Exit:** BUG-2 + BUG-8 + BUG-14 (WS event işlenemez, exit lifecycle tetiklenmez)
3. **Trailing → State:** BUG-3 (trailing borsada çalışır ama state güncellenmez)
4. **Restart → Recovery:** BUG-5 + BUG-6 (state tutarsız + recovery crash)
5. **FVG Update:** BUG-4 (FVG motoru durursa tüm sinyal akışı durur)

---

## 🛠️ ÖNERİLEN DÜZELTME SIRASI

1. **BUG-2** (WS `fill_price`) — Tek satır, en kritik etki
2. **BUG-1** (Emergency close `success=False`) — Tek satır, para kaybı riski
3. **BUG-3** (Trailing key tutarsızlığı) — 2 satır, strateji bütünlüğü
4. **BUG-5** (Gün tutarsızlığı) — State yönetimi merkezileştirilmeli
5. **BUG-4** (FVG frozen) — `models.py` kontrolü
6. **BUG-6** (Recovery frozen) — `models.py` kontrolü
7. **BUG-7** (Side belirsizliği) — Metod imzası düzeltilmeli
8. **BUG-8** (Clock skew) — `raw.get("E", ...)` kullanılmalı
9. **BUG-9** (Lock oluşturma) — `__init__` refactor
10. **BUG-14** (Legacy/Normalized tutarsızlığı) — Handler birleştirilmeli
