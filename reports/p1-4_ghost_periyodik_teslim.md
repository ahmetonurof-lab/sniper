# P1-4 — reconcile_ghost_positions Periyodik Hale Getirme: Teslim Raporu

Tarih: 09.08.2026
Baş mühendis direktifi: `reconcile_ghost_positions` mantığını değiştirme, yalnızca çağrı zamanlamasını değiştir (restart-only → periyodik + restart).

## 1. Değişiklik (tek nokta)

**`src/trading/recovery_manager.py:838-842`** — `periodic_check_loop()` içine `reconcile_orphan_orders()` çağrısının hemen ardına:

```python
# FIX (P1-4): Periyodik ghost pozisyon temizligi —
# restart'lar arasinda olusan ghost pozisyonlar da
# fark edilsin (idempotent: temizlenen state open=false
# olur, sonraki turda elenir).
await self.reconcile_ghost_positions()
```

- **Aralık gerekçesi:** `reconcile_orphan_orders` ile aynı 60sn aralık. Orphan sweep zaten her 60sn'de tüm sembollerin açık emirlerini tarıyor; ghost-check ekstra REST yükü getirmez (yalnızca `_active_trades`'te olmayan + `open=true` adaylar için, döngü içinde `get_positions` — normal/temiz durumda aday yoksa hiç çağrılmaz). Daha sık aralık gereksiz istek demek, daha seyrek aralık restart'lar arası boşluğu uzatır → tutarlılık için 60sn.
- **Dokunulmayan:** `reconcile_ghost_positions` mantığı (satır 639-729), `reconcile_orphan_orders`, `_known_protection_ids`, ProtectionLifecycleService, `_on_1m_close` orphan sayacı, restart çağrısı (`bot.py:1265`).

## 2. Proaktif Kontrol: Idempotency / Çakışma (kod öncesi doğrulandı)

| Senaryo | Analiz | Sonuç |
|---|---|---|
| Aynı ghost iki kez reconcile | Temizleme dalı `mark_trade_closed` (`state_manager.py:116-126`) `open=False` yazar → sonraki turda `if not s.get("open"): continue` ile elenir. `mark_trade_closed` kendisi de idempotent. | Zararsız ✅ |
| Pozisyon açık + ghost adayı | SL/TP kontrolü yalnızca `log_event`/`_pl` üretir, durum değiştirmez → tekrar tekrar çağrılabilir. | Zararsız ✅ |
| Aktif trade'ler | `if sym in self._active_trades: continue` (satır 656-657) → asla ghost muamelesi görmez. | Korunuyor ✅ |
| Restart vs periyodik çakışması | Restart çağrısı `bot.py:1265` senkron; `periodic_check_loop` `run()` sonunda task olarak başlar (`bot.py:1299-1301`), ilk iterasyonu 60sn sonra → bindirme yok. | Çakışma yok ✅ |
| `trades_today` sıfırlama | `_states[sym].trades_today = 0` yalnızca "pozisyon kapalı" dalında (satır 717) → canlı trade'te sıfırlama yok. | Güvenli ✅ |
| SL/TP eksik uyarısı | `ghost_missing_sltp` her 60sn'de tekrarlanır — bilinçli (sorun görünür kalır, "sessiz bırakma" prensibi). | Bilinçli ✅ |

## 3. Regression Test

**`tests/test_recovery_manager.py`** +2 test (`TestPeriodicLoopGhostReconcile`):

1. `test_periodic_loop_runs_ghost_reconcile` — `periodic_check_loop` içinde `reconcile_ghost_positions`'ın çağrıldığını doğrular (`asyncio.sleep` patch → tek iterasyon + CancelledError; `recover_positions`/`reconcile_orphan_orders`/`reconcile_ghost_positions` AsyncMock ile).
2. `test_ghost_reconcile_cleans_closed_position` — kapsam kilidi: `state_manager.dump_state`/`mark_trade_closed`/`log_event` patch; state `open=true` + Binance pozisyon kapalı (`get_positions`→`[]`) → `mark_trade_closed("BTCUSDT")` + `log_event("ghost_cleaned", ...)` + `trades_today == 0`. Mantığın DEĞİŞMEDİĞİNI garantiler.

## 4. Kanıt (test + baseline karşılaştırması)

| Suite | Önce (baseline) | Sonra | Δ |
|---|---|---|---|
| test_recovery_manager | 6 passed | **8 passed / 0 fail** | +2 yeni ✅ |
| test_entry_manager | 95 passed | **95 passed / 0 fail** | 0 yeni fail ✅ |
| test_integration_lifecycle | 12 passed | **12 passed / 0 fail** | 0 yeni fail ✅ |
| test_models | 51 passed | **51 passed / 0 fail** | 0 yeni fail ✅ |
| test_bot | 32 passed / 13 fail | **32 passed / 13 fail** | 13 pre-existing (mark_trade_closed/_stage/MIN_FVG_SIZE), **0 yeni** ✅ |

Hedefli koşu: `pytest tests/test_recovery_manager.py -k "PeriodicLoopGhostReconcile"` → **2 passed**.
Lint: `ruff check src/trading/recovery_manager.py tests/test_recovery_manager.py` → **All checks passed**.

## 5. Sonuç
- Restart'lar arasında oluşan ghost pozisyon artık en geç 60sn içinde tespit edilip temizlenir (restart-only bekleyiş bitti).
- Mantık değişmedi; yalnızca `periodic_check_loop`'a 1 satır ekleme + restart çağrısı korundu.
- Commit: (aşağıda) + push yapıldı.
