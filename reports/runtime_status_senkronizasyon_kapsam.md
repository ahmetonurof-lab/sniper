# runtime.status Senkronizasyonu — Kapsam Raporu

> **Tarih:** 2026-08-09
> **Durum:** KAPSAM ÇIKARILDI (kod değişikliği yok)
> **Kaynak görev:** "TestExitStateTransitions'ın 3 fail'i neyi kırıyor + integration_lifecycle ilişkisi → tek noktadan düzelt"

---

## 1. Özet

`runtime.status` (nested `TradeRuntimeState`) ile flat `ActiveTrade.status` aynı state'in **iki ayrı kopyası**dır ve **senkron değildir**. `runtime.status` üretim kodunda hiç yazılmaz — her zaman default `ACTIVE`. TestExitStateTransitions'ın 3 fail'i tam olarak bu senkron eksikliğini kodluyor.

Baş mühendis tahmini doğrulandı: bu, "state iki yerde tutuluyor, senkron değil" ailesinin yeni bir örneği.

---

## 2. TestExitStateTransitions — 3 fail neyi kırıyor

**Dosya:** `tests/test_integration_lifecycle.py:316-336`

| Test | State geçişi | Assertion | Gözlenen |
|---|---|---|---|
| `test_active_to_exit_requested` | flat `status` → `EXIT_REQUESTED` | `runtime.status.value == "EXIT_REQUESTED"` | ❌ `'ACTIVE'` |
| `test_submitted_to_verifying` | flat `status` → `EXIT_VERIFYING` | `runtime.status.value == "EXIT_VERIFYING"` | ❌ `'ACTIVE'` |
| `test_closed_terminal` | flat `status` → `CLOSED` | `runtime.status.value == "CLOSED"` | ❌ `'ACTIVE'` |

**Hepsi aynı mekanizma:** `trade["status"] = STATUS_X` flat alana yazılır; `runtime.status` güncellenmez → `ACTIVE`'de kalır.

**Teyit:** `python -m pytest tests/test_integration_lifecycle.py::TestExitStateTransitions -q` → `FFF` (3 failed).

---

## 3. Kök neden

### 3.1 State iki yerde tutuluyor

```python
# src/models.py
class ActiveTrade:                      # flat, str
    status: str = ""                    # models.py:530
    runtime: TradeRuntimeState = field(default_factory=TradeRuntimeState)  # :544

@dataclass
class TradeRuntimeState:                # nested, enum
    status: TradeStatus = TradeStatus.ACTIVE   # models.py:443
```

### 3.2 Senkron noktası eksik

```python
# src/models.py:580-586
def __setitem__(self, key: str, value) -> None:
    setattr(self, key, value)   # sadece flat alana yazar — runtime senkronu YOK
```

### 3.3 `runtime.status` üretimde hiç yazılmıyor

`grep -n "runtime" src/` → yalnızca:

| Dosya | Kullanım |
|---|---|
| `models.py:544` | tanım (`runtime` field) |
| `trading/order_manager.py:1212-1261` | `_sync_runtime_protection` — **sadece** `runtime.protection` ref'leri (P2-4 state-sync fix'i, status değil) |
| `state_writer.py:85` | yorum: *"flat field'lardan türet (trade.runtime.protection hep default)"* |
| `trading/exit_lifecycle.py:21-22` | docstring: *"TradeRuntimeState / TradeConfirmedState / PendingExitContext bu patch'te BAĞLANMADI"* |

Sonuç: `runtime.status` alanı refactor'da kuruldu ama **hiç bağlanmadı**. `exit_lifecycle.py` docstring'i bunu resmi olarak doğruluyor.

---

## 4. integration_lifecycle ilişkisi

- `test_integration_lifecycle.py` **tam dosya:** `9 passed / 3 failed` — fail'ler **yalnızca** `TestExitStateTransitions`.
- (Progress tablosundaki "57 passed / 9 fail" v2+lifecycle kombinasyonuydu; bu dosyanın tek kırık kaynağı bu 3 test.)
- `TestStateSyncTrailOrphanRecovery` (P2-4, `runtime.protection` senkronu) **yeşil** — protection tarafı zaten çözülmüş; eksik kalan yalnızca `runtime.status` tarafı.

---

## 5. Tek nokta düzeltme önerisi

**Dosya:** `src/models.py` — `ActiveTrade.__setitem__`

```python
def __setitem__(self, key: str, value) -> None:
    setattr(self, key, value)
    if key == "status" and getattr(self, "runtime", None) is not None:
        try:
            self.runtime.status = TradeStatus(value)
        except ValueError:
            pass  # "" veya bilinmeyen değer → runtime.status default ACTIVE'de kalır
```

### Neden tek nokta yeterli — kapsam kanıtı

Üretimde `trade["status"] = ...` yazan **15 noktanın tamamı** `__setitem__`'ten geçer:

| Dosya | Satırlar | Status değerleri |
|---|---|---|
| `src/bot.py` | 598, 1218 | EXIT_REQUESTED, ACTIVE |
| `src/trading/order_manager.py` | 159, 177, 374 | TRAIL_REPLACING, ACTIVE |
| `src/trading/exit_lifecycle.py` | 253, 279, 351, 377, 379, 428, 618, 657, 684 | ACTIVE, EXIT_SUBMITTED, EXIT_VERIFYING, REPAIR_REQUIRED, CLOSED, BROKEN_MANUAL |

Attribute yazımı (`trade.status = ...`) üretimde **yok** (tek eşleşme `bot_binance.py` HTTP `resp.status` — alakasız). → Tek değişiklik tüm yaşam döngüsü yollarını otomatik kapsar.

---

## 6. Yan etki analizi

| Konu | Sonuç |
|---|---|
| `state_writer.py` | ✅ Etkilenmez — `live_state` JSON tamamen flat `trade.get("status")`'ten türetiliyor (satır 75-92, BULGU-05). `runtime` JSON'a bile yazılmıyor. |
| `""` değeri | ✅ `UNRESTRICTED_STATUSES = {STATUS_ACTIVE, ""}` (`models.py:320`); `TradeStatus("")` → ValueError → `except ValueError: pass` ile güvenli atlanır (runtime ACTIVE'de kalır, set ile uyumlu). |
| `TradeStatus` çevirimi | ✅ `TradeStatus(str, Enum)` (`models.py:329-337`) — değerleri STATUS_* sabitleriyle birebir: `TradeStatus("EXIT_REQUESTED")` vb. geçerli. |
| `order_manager._sync_runtime_protection` | ✅ `runtime is None` guard'ı zaten var (1258-1259); `__setitem__` aynı guard'ı kullanır. |
| Test fixture (yeni gözlem) | ⚠️ `_trade()` (`test_integration_lifecycle.py:68`) tick_size'sız `ACTIVE` kuruyor → çalıştırmada savunmacı CRITICAL log kirliliği (`tick_size olmadan kuruldu`). Kapsam dışı ama düzeltme commit'ine `_trade()`'a `tick_size=0.001` eklenmesi log'u temizler. |

---

## 7. Önerilen uygulama (tek commit)

1. `src/models.py` — `__setitem__` status senkronu (yukarıdaki 6 satır).
2. `tests/test_integration_lifecycle.py` — `_trade()` fixture'ına `tick_size` (CRITICAL log kirliliği temizliği).
3. Doğrulama: `pytest tests/test_integration_lifecycle.py::TestExitStateTransitions -q` → **3 passed**; `test_integration_lifecycle.py` tamamı → **12 passed / 0 failed**; diğer suite'ler baseline ile birebir (0 yeni fail).

---

## 8. Referanslar

- Fail kaynağı: `tests/test_integration_lifecycle.py:316-336` (`TestExitStateTransitions`)
- `src/models.py:443` (`TradeRuntimeState.status`), `:530` (`ActiveTrade.status`), `:584` (`__setitem__`), `:329-337` (`TradeStatus`)
- `src/trading/exit_lifecycle.py:21-22` (BAĞLANMADI notu)
- `src/state_writer.py:75-92` (flat'ten türetme — BULGU-05)
- `src/trading/order_manager.py:1212-1261` (`_sync_runtime_protection`, P2-4)
