# runtime.status Senkronizasyonu — TESLİM RAPORU

> **Tarih:** 2026-08-09
> **Durum:** ✅ TAMAMLANDI (tek commit, push edildi)
> **Kaynak:** `reports/runtime_status_senkronizasyon_kapsam_1.md` (plan onayı + görevler)

---

## 1. Kök neden (özet)

`ActiveTrade.status` (flat, `models.py:530`) ile `TradeRuntimeState.status` (nested, `models.py:443`) aynı state'in iki kopyasıydı ve senkron değildi. `trade["status"] = X` yazımları (`__setitem__`) yalnızca flat alana yazıyor, `runtime.status` hiç güncellenmiyordu → hep default `ACTIVE`. `exit_lifecycle.py:21-22` docstring'inin "TradeRuntimeState ... BAĞLANMADI" notu bunu doğruluyordu.

TestExitStateTransitions'ın 3 fail'i bu senkron eksikliğini kodluyordu (EXIT_REQUESTED / EXIT_VERIFYING / CLOSED geçişlerinde `runtime.status.value` ACTIVE'de kalıyordu).

---

## 2. Değişiklikler

### 2.1 `src/models.py` — `__setitem__` status → runtime senkronu (tek nokta)

```python
# src/models.py:584-593
def __setitem__(self, key: str, value) -> None:
    setattr(self, key, value)
    if key == "status":
        try:
            self.runtime.status = TradeStatus(value)
        except ValueError:
            logger.debug(
                "[MODELS] status %r TradeStatus'a cevrilemedi — "
                "runtime.status senkronu atlandi",
                value,
            )
```

**Sessiz `pass` YOK** — direktif gereği `logger.debug` ile iz bırakıyor (bot.py:983 dersinden hareketle).

**Kapsam kanıtı (rapor 5'ten):** üretimdeki 15 `trade["status"] = ...` noktasının tamamı bu tek geçitten geçiyor:
- `bot.py:598,1218` — EXIT_REQUESTED, ACTIVE
- `order_manager.py:159,177,374` — TRAIL_REPLACING, ACTIVE
- `exit_lifecycle.py:253,279,351,377,379,428,618,657,684` — ACTIVE, EXIT_SUBMITTED, EXIT_VERIFYING, REPAIR_REQUIRED, CLOSED, BROKEN_MANUAL_INTERVENTION_REQUIRED

**Proaktif kontrol:** `TradeStatus` enum'u 9 üyenin tamamını kapsıyor (`models.py:329-338`; `BROKEN_MANUAL_INTERVENTION_REQUIRED` dahil) → **tüm üretim değerleri çevrilebilir, hiçbiri ValueError'a düşmez.** `TradeStatus("")` (UNRESTRICTED sette, `models.py:320`) → ValueError → `logger.debug` + runtime ACTIVE'de kalır (beklenen davranış).

### 2.2 `tests/test_integration_lifecycle.py` — `_trade()` fixture temizliği

- `:64` base dict'e `tick_size=0.001`
- `:77` `ActiveTrade(...)` kuruluşuna `tick_size=base.get("tick_size", 0.001)`

Öncesinde fixture tick_size'sız `ACTIVE` kuruyordu → savunmacı `[MODELS] tick_size olmadan kuruldu` CRITICAL log kirliliği üretiyordu. Bu değişiklikle temizlendi (PENDING muafiyeti korundu — `__post_init__`'e dokunulmadı).

---

## 3. Kanıt — test sonuçları

| Suite | Önce | Sonra | Fark |
|---|---|---|---|
| `TestExitStateTransitions` | **3 failed** | **3 passed** | ✅ |
| `test_integration_lifecycle.py` (tam dosya) | 9 passed / 3 failed | **12 passed / 0 failed** | ✅ |
| `test_models.py` | 51 passed | **51 passed** | ✅ baseline |
| `test_bot.py` | 32 passed / 13 failed | **32 passed / 13 failed** | ✅ 0 yeni fail |
| `test_recovery_manager.py` | 6 passed | **6 passed** | ✅ baseline |

**Pre-existing fail sayısı değişmedi:** test_bot'taki 13 fail bayat refactor testleri (`mark_trade_closed` / `_stage` / `MIN_FVG_SIZE`) — bu değişiklikle ilişkisiz, sayı birebir aynı.

---

## 4. Dokunulmayanlar (kapsam dışı direktifi uygulandı)

- `src/state_writer.py` — dokunulmadı (zaten flat'ten türetiyor, BULGU-05).
- `src/trading/order_manager.py::_sync_runtime_protection` — dokunulmadı (P2-4 yeşil kaldı).

---

## 5. Nüks / yan etki kontrolü

- **P2-4 (runtime.protection senkronu):** yeşil kaldı, etkilenmedi.
- **PENDING muafiyeti:** `__post_init__`'e dokunulmadı — PENDING placeholder'lar bilinçli muaf, gerçek kuruluşlar CRITICAL patlamaya devam ediyor (savunma korundu).
- **Sentinel davranışı (P2-3):** değişmedi.
- **`runtime is None` senaryosu:** `runtime` dataclass default'u (`TradeRuntimeState()`) her zaman kurulu; `__setitem__` yalnızca kurulu nesnelerde çağrılır — guard'a gerek yok.

---

## 6. Commit + Push

```
Commit hash: 5fd6f11  fix(models): runtime.status senkronu (__setitem__) - TestExitStateTransitions 3 fail yesil, 12/0
```

- **Kapsanan dosyalar:** `src/models.py`, `tests/test_integration_lifecycle.py`, `memory-bank/activeContext.md`, `memory-bank/progress.md`, bu rapor
- **Push:** `e0580b3..5fd6f11 main -> main` — doğrulandı

---

## 7. Açık kalan / sıradaki iz

| Öğe | Durum | Not |
|---|---|---|
| P2-8 APTUSDT dust-close gap | bugs.md notu (fix yok) | place_market_order `{}` → ACİL KAPANIŞ BAŞARISIZ; minNotional altı dust stratejisi önerildi |
| PRE-ENTRY canlı gözlem | pasif iz | `[PRE-ENTRY]` reddi canlıda henüz gözlemlenmedi |
| Deploy kararı | **kullanıcıda** | `aa27b6f` (tick_size sentinel fix) + bu commit sunucuya henüz alınmadı (screen 390682.bot) |
