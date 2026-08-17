# IFVG Modülü — Devir Eki (orijinal ifvg-direktif.md ile BİRLİKTE okunacak)

Bu dosya orijinal direktifin yerine geçmez, üzerine eklenir. Önce
`ifvg-direktif.md`'yi oku (mimari, feature-flag zorunluluğu, süreç sırası
hâlâ geçerli), sonra bunu oku (mevcut durum + değişen tek karar).

## Mevcut durum (önceki ajanın bitirdiği kısım — tekrar yapma)

**Sniper (canlı), tamamlanmış + doğrulanmış:**
- `config.py`: `IFVG_ENABLED` flag (default `False`, env-bazlı)
- `retrace_state.py`: `_inverted_candidates`, `_register_inverted()`,
  `check_ifvg_retest()`, `reset()` temizler / `lock_bias()` dokunmaz
- `signal_engine.py`: `progress_rsm` içinde IFVG çağrı noktası + bar-başı
  `rsm._last_trigger_source = 'NORMAL'` (madde 7 sızıntı önlemi)
- Testler: `test_retrace_state.py` 80/88 (flag-off regresyon bit-bit aynı +
  14 yeni IFVG testi), `test_signal_engine.py` 9/9

**Backtest-sniper, tamamlanmış + compile + smoke:**
- `sweep_sync.py`: canlı driver'ın bit-bit mirror'u
- `analyzer_v5.py`: her trade'e `entry_source` damgası, `compute_session_stats`
  → `ifvg_signals/normal_signals/ifvg_pnl/normal_pnl`, rapor coin tablosuna
  `IFVG#`/`IFVG$` sütunları + toplam blok, `--ifvg` CLI flag'i (worker'lara
  env-var ile geçiyor)
- Doğrulama: compile OK, 8-coin parallel-6 smoke temiz

**Commit durumu: HİÇBİR ŞEY COMMIT/PUSH EDİLMEDİ.** Uncommitted dosyalar:
sniper tarafında `config.py`, `retrace_state.py`, `signal_engine.py`,
`test_retrace_state.py` (+ untracked `reports/ifvg-direktif.md`);
backtest-sniper tarafında `src/analyzer_v5.py`, `src/sweep_sync.py`
(+ ⚠️ `reports/analyzer_v5_summary.md` bir önceki ajanın 8-coin smoke
run'ıyla kirlenmiş, commit'ten önce temizlenmeli — o smoke bölümünü sil,
gerçek 28-coin koşusunun raporunu bekle).

`docs/fibo_zone_holdout_validation.md` ve `reports/fvg_zone_fibo_analysis.md`
bu göreve ait DEĞİL (önceki B_SWING seansından kalma) — dokunma.

## DEĞİŞEN KARAR: IFVG artık daily-bias filtresinden MUAF

Önceki ajanın bulduğu blokaj: IFVG yapısal olarak counter-trend
(inversion = reversal), ama entry pipeline'ının daily-bias uyum filtresi
(`analyzer_v5.py:961-969` ve canlı `signal_engine.py:155-165`, aynı mantık)
tüm counter-trend sinyalleri reddediyor. Kanıt: LINK'te 528 retestten 307
tetik, 307/307 ters-yön, `would_pass=0` — yani IFVG olduğu gibi bırakılırsa
HER ZAMAN 0 trade üretir.

**Karar (baş mühendis onayı ile): IFVG girişleri bu filtreden muaf
tutulacak.** Yani IFVG kaynaklı bir trigger, `daily_bias` ile aynı yönde
olma şartını kontrol etmeden entry pipeline'ına geçebilmeli — NORMAL
(sweep+FVG) trigger'lar için filtre aynen kalıyor, yalnızca IFVG kaynaklı
olanlar için bypass ediliyor.

### Nerede uygulanacak (aynı "iki sürücü" deseni — feature flag altında)

Hem `analyzer_v5.py:961-969` hem canlı `signal_engine.py:155-165`'te bias
kontrolünün hemen öncesine, IFVG kaynaklı olup olmadığına bakan bir erken-çık
eklenmeli:

```python
# daily_bias uyum kontrolünden ÖNCE:
if getattr(rsm, "_last_trigger_source", None) != "IFVG":
    # mevcut bias-uyum kontrolü AYNEN kalır, sadece NORMAL trigger'lara uygulanır
    if <mevcut bias check koşulu reddediyorsa>:
        <mevcut reddet/continue>
# IFVG ise bias kontrolü hiç çalışmadan devam eder
```

Mevcut bias-kontrol satırlarının kendisi **değiştirilmeyecek** — sadece bir
guard (`if trigger_source != "IFVG":`) ile sarmalanacak. Bu, feature-flag
prensibiyle tutarlı: `IFVG_ENABLED=False` iken `_last_trigger_source` hep
`'NORMAL'` olduğundan guard hep True döner, davranış bugünkünden farksız
kalır — regresyon riski yok.

### Test gereksinimi (ek)

Yeni bir regresyon testi: NORMAL trigger + ters-bias senaryosu hâlâ
reddedilmeli (mevcut davranış korunmalı); IFVG trigger + ters-bias senaryosu
artık kabul edilmeli (yeni davranış). İkisi ayrı test case olarak eklensin.

## Sıradaki adımlar (bu ajanın yapacağı)

1. Yukarıdaki bias-muafiyeti değişikliğini iki sürücüye de uygula, testleri
   geçir (flag-off regresyon dahil).
2. `reports/analyzer_v5_summary.md`'deki smoke kirliliğini temizle.
3. Orijinal direktifin süreç adım 2'sini çalıştır: `--ifvg` flag'i ile
   **tam 28-coin** koşu, mevcut 1.6M temiz baseline'a karşı. Rapor: toplam
   Δ + IFVG entry sayısı/oranı + coin bazlı IFVG dağılımı (tek-coin
   kırılganlığı var mı, B_SWING onayındaki gibi kontrol et).
4. Sonucu baş mühendise getir — canlıya (`IFVG_ENABLED=True`) deploy kararı
   orada verilecek, bu ajan kendi başına flag'i açık bırakıp deploy etmeyecek.
5. Memory-bank güncellemesi + commit + push, AGENTS.md kapanış protokolüne
   göre — bu oturumda hiç yapılmadı, göz ardı edilmemeli.

## Not: memory-bank tutarsızlığı

Önceki ajan `sniper/memory-bank`'te "IFVG 56 trade" gibi mevcut koda
uymayan (stale, önceki bir varyanttan kalma muhtemelen) bir kayıt buldu.
Bu ajana: memory-bank güncellemesi yaparken bu eski kaydı düzelt/temizle,
karıştırma.
