# P1-4 — reconcile_ghost_positions Periyodik Hale Getirme: Kapsam Raporu

Tarih: 09.08.2026
Durum: kapsam analizi tamam → fix + regression test uygulanacak
Baş mühendis direktifi: `reconcile_ghost_positions` mantığını **değiştirme**, sadece çağrı zamanlamasını değiştir (restart-only → periyodik + restart).

## 1. Mevcut Durum

| Çağrı | Nerede | Zamanlama |
|---|---|---|
| `reconcile_ghost_positions()` | `bot.py:1265` | restart-only (run() içinde, senkron) |
| `reconcile_orphan_orders()` | `bot.py:1268` (restart) + `bot.py:556` (`_on_1m_close`, her 5. 1m bar) + `recovery_manager.py:836` (periyodik) | restart + periyodik + bar-based |
| `periodic_check_loop()` | `recovery_manager.py:822-841` | her ~60sn: `recover_positions(quiet=True)` + `reconcile_orphan_orders()` |

Boşluk: restart'lar arasında oluşan ghost pozisyon (bot düşer/silent-fail → Binance'te pozisyon kapanır ama state `open=true` kalır) fark edilmeden bekler; yalnızca bir sonraki restart'ta temizlenir.

## 2. Değişiklik (tek nokta)

`recovery_manager.py:822-841` `periodic_check_loop()` içine `reconcile_orphan_orders()` çağrısının hemen yanına `reconcile_ghost_positions()` eklenir:

```python
await self.recover_positions(quiet=True)
await self.reconcile_orphan_orders()
await self.reconcile_ghost_positions()   # YENİ (P1-4)
```

Aralık gerekçesi: `reconcile_orphan_orders` ile **aynı** 60sn aralık. Orphan-check halihazırda her 60sn'de tüm sembollerin açık emirlerini tarayıp REST yükü bindiriyor; ghost-check bununla aynı büyüklükte ek yük getirmez (yalnızca `_active_trades`'te olmayan + `open=true` adaylar için `get_positions()` tek çağrı). Daha sık aralık gereksiz REST isteği demek; daha seyrek aralık da restart'lar arası boşluğu uzatır. Tutarlılık için 60sn seçildi.

## 3. Proaktif Kontrol: Idempotency / Çakışma Riski

### 3.1 İki kez reconcile edilirse (restart + periyodik / periyodik + periyodik)
`reconcile_ghost_positions` (`recovery_manager.py:639-729`) üç durum üretir:

1. **`sym in self._active_trades`** → `continue` (satır 656-657). Aktif trade'ler asla ghost muamelesi görmez.
2. **Binance'te pozisyon AÇIK** (satır 666-714) → yalnızca SL/TP varlığını loglar/event yazar, **hiçbir durum değişikliği yapmaz**. İkinci kez çağrılırsa aynı log tekrar basılır — zararsız (idempotent). SL/TP eksikse `ghost_missing_sltp` uyarısı her 60sn'de tekrarlanır — bu bilinçli: sorun devam ettiği sürece görünür kalmalı (baş mühendisin "sessiz bırakma" prensibi).
3. **Binance'te pozisyon KAPALI** (satır 715-727) → `mark_trade_closed(sym)` (`state_manager.py:116-126`): `open=False` yazar. Sonraki çağrıda satır 654'teki `if not s.get("open"): continue` ile **elenir**. `mark_trade_closed` kendisi de idempotent (zaten `open=False` ise no-op). `_states[sym].trades_today = 0` (satır 717) yalnızca bu dalda çalışır — canlı trade'te sıfırlama yok.

→ **Sonuç: idempotent, çift-çağrı zararsız.**

### 3.2 Restart çağrısıyla çakışma
- Restart çağrısı `bot.py:1265` — `run()` içinde **senkron**, `periodic_check_loop` ise `run()` sonunda task olarak başlatılıyor (`bot.py:1299-1301`) ve ilk iterasyonu 60sn sonra çalışıyor (`asyncio.sleep(60)` döngü sonunda).
- Dolayısıyla restart-time temizlik **bitmeden** periyodik çağrı başlamaz; iki çağrı aynı anda `state_manager` FileLock'una (LOCK_FILE) girmez (senkron restart çağrısı zaten bitmiş olur).
- Aynı pozisyon iki kez reconcile olsa bile 3.1'deki idempotency geçerli.

### 3.3 REST yükü
- Periyodik çağrı, `recover_positions`'ın zaten yaptığı `get_positions()` çağrısına ek olarak her 60sn'de **en fazla 1 ek istek** bindirir (tüm ghost adayları için tek `get_positions`; `get_all_orders` yalnızca pozisyon gerçekten açıksa).
- Normal çalışmada ghost adayı yoksa `get_positions` çağrısı yine de olur (state'te `open=true` olup `_active_trades`'te olmayan hiçbir sembol yoksa `get_positions` hiç çağrılmaz — satır 663'teki sorgu döngü içindedir ve aday başına çalışır).

> Düzeltme: satır 663'teki `get_positions()` her **aday sembol için** ayrı çağrılır (döngü içinde). Ghost adayı yoksa hiç çağrılmaz. Normal (temiz) durumda ek REST yükü ≈ 0.

## 4. Regression Test Planı

`tests/test_recovery_manager.py`'ye yeni test sınıfı `TestPeriodicLoopGhostReconcile`:
- `periodic_check_loop()` içinde `reconcile_ghost_positions()`'ın çağrıldığını doğrula (mock ile, `asyncio.sleep` patch → tek iterasyon + CancelledError).
- `reconcile_ghost_positions` davranışı: `dump_state`/`mark_trade_closed` mock ile state `open=true` + Binance pozisyon kapalı → `mark_trade_closed` çağrılır, `ghost_cleaned` event'i loglanır.
- Baseline: mevcut testlerin tamamı (0 yeni fail) + `-k` ile hedefli çalıştırma.

## 5. Dokunulmayacaklar
- `reconcile_ghost_positions` mantığı (satır 639-729) — sıfır değişiklik.
- `reconcile_orphan_orders` çağrı noktaları, `_known_protection_ids`, ProtectionLifecycleService.
- `_on_1m_close` orphan sayacı (`bot.py:555-556`).

## 6. Kanıt
- Değişiklik: `src/trading/recovery_manager.py` (tek satır ekleme).
- Test: `tests/test_recovery_manager.py` yeni testler + baseline karşılaştırması.
- Commit + push (bu dosyayla birlikte).
