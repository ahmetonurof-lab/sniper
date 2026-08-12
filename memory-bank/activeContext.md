# Active Context
## Son Durum: 2026-08-12 22:37

### Mevcut Görev: FVG display kozmetik bug düzeltmesi
- **Sorun:** `BIAS_LOCKED` durumunda FVG bilgisi İKİ KEZ basılıyordu:
  1. SWEEP satırına inline: `SWEEP: TAMAMLANDI | ... | FVG bekleniyor | ...`
  2. Ayrı FVG satırı: `FVG_SCAN | ... | FVG ARANIYOR...`
- **Etki:** Kullanıcı "FVG bulma işi birinde yeni formatta yani aynı satırda belirtiyor, diğerinde eskisi gibi 3.satırda fvg aranıyor" şeklinde duplike görüyordu.
- **Fix:** `console_reporter.py`'de BIAS_LOCKED ve IDLE+bias SWEEP satırlarından inline "FVG bekleniyor" kaldırıldı. Artık FVG durumu sadece `display_fvg_status()` üzerinden ayrı satırda gösteriliyor (SWEEP_DETECTED, BIAS_LOCKED → "FVG ARANIYOR", TRIGGER_READY → "HAZIR").
- **Dosya:** `src/trading/console_reporter.py` (lines 185, 230)
- **Commit:** `fix: remove duplicate inline FVG status from SWEEP display`
