# GÖREV 1 — Restart/Recovery Persistence Bulgusu: `_inverted_candidates`

**Tarih:** 2026-08-18 · **Kapsam:** IFVG paper-deploy direktifi Görev 1 (restart/recovery)

---

## Bulgu: BUG YOK — bilinçli tasarım kararı (veri kaybı olarak belgelendi)

`_inverted_candidates`, bot restart'ında **sessizce boşalır**. Bu bir bug değil,
bilinçli bir tasarım kararıdır; ancak **yazılı belgelenmesi gereken** bir davranıştır
(direktifin istediği gibi — ileride "neden IFVG adayı restart sonrası kayboldu"
araştırması başlamasın diye).

### Kanıt

1. **RecoveryManager RSM'e hiç dokunmaz.** `recovery_manager.py` yalnızca Binance'teki
   açık pozisyonları → `ActiveTrade` envanterine yeniden kurar (restore). `RetraceStateMachine`
   veya `_inverted_candidates` ile ilgili **sıfır referans** var (grep: 0 eşleşme).

2. **RSM her restart'ta sıfırdan kurulur.** `bot.py::PaperTrader.__init__` her sembol
   için `RetraceStateMachine(max_wick_ratio=cfg.FVG_WICK_RATIO_MAX)` yaratır →
   `_inverted_candidates = []` (yeni nesne, bellekteki liste yok olur).

3. **Persist edilen tek RSM state'i günlük BIAS latch'idir.** `state_manager.py`
   (`trade_state.json`): `mark_bias_locked`/`load_bias_lock` → yalnızca `daily_bias`,
   `sweep_direction`, `sweep_level`, `bias_lock_day`, `bias_lock_bar_index`. Restart'ta
   `_restore_bias_latch` bunu `restore_bias_lock()` ile yükler — o da `_inverted_candidates`
   alanına **dokunmaz** (yeni RSM zaten boş başladığı için fark edilmez).

4. **`active_fvg.json`** (`_FVG_STATE_FILE`) yalnızca **exit-lifecycle** FVG verisi için
   kullanılır — IFVG adayları oraya yazılmaz.

5. **tick_size bug'ı ile karşılaştırma:** `tick_size` olayı "kısmi restore" hatasıydı
   (alan sessizce atlanıyor, yanlış state ile devam). Burada **kısmi restore YOK** —
   RSM tamamen sıfırdan kuruluyor, `_inverted_candidates` asla yarım yüklenmiyor.
   Dolayısıyla **sessiz state bozulması riski yok**; sadece **beklenen veri kaybı** var.

### Davranış (belgelenen)

| Olay | Davranış |
|---|---|
| Restart (tam süreç yeniden başlama) | `_inverted_candidates` → boş (bellek state'i kaybolur). Günlük BIAS latch'i korunur. |
| Trade kapanışı / `lock_bias` | `_inverted_candidates` KORUNUR (suressiz gecerli — test: `test_lock_bias_does_not_clear_inverted_candidates`) |
| `reset()` (bias conflict, full reset) | `_inverted_candidates` temizlenir (tasarım: yeni gün taze başlar) |

### Etki değerlendirmesi

- **Kritik değil:** IFVG adayları kısa ömürlüdür (birkaç 15m bar içinde retest ya da
  ölüm ile sonlanır). Restart anında izlenen adaylar kaybolsa da yeni gün zaten genelde
  bias latch'i ile BIAS_LOCKED'de başlar ve yeni FVG'lerle adaylar yeniden oluşur.
- **Paper açılışını engellemez.** Görev 3 için beklenen davranış: restart sonrası
  IFVG adayı yok → yeni FVG kırılımlarıyla yeniden birikir.

### Eklenen regresyon testi

`tests/test_retrace_state.py::TestIFVGLifecycle::test_restart_simulation_loses_inverted_candidates`
— restart simülasyonu: aday kaydet → yeni RSM kur + `restore_bias_lock` → aday listesinin
boş, bias lock'un korunduğunu assert eder. (Suite: 83 passed.)

---

### Görev 3 (paper açma) için beklenti

Paper'da bir restart yaşanırsa: `_inverted_candidates` boş gelecek (beklenen),
BIAS latch korunacak (beklenen). Anomali sayılmaz — sadece log'da `[RST] BIAS_LOCKED
RESTORE` satırı ve sonrasında IFVG adaylarının yeniden birikmeye başlaması doğrulanmalı.
