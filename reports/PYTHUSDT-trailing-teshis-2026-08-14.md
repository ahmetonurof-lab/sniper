# PYTHUSDT Trailing Teşhis Raporu — 2026-08-14

## Özet
PYTHUSDT short trade (PYTHUSDT-0) 10:44:37 UTC'de açıldı, **15:17:31 UTC'de TP ile kapandı**.
**pnl=+32.92 $, trail_count=0, trailing_count=0, trail_steps=[]**. Trailing trade hayatı boyunca HİÇ uygulanmadı.

## 1) [TRAIL] Logları (baş mühendisin istediği veri #1)
- 259 adet `trail_skipped` event, tamamı `reason="no_better_trail_candidate"`.
- İlk: 10:46:14 UTC, Son (yerel kopya): 15:04:14 UTC. Median gap ~60.053s → her 1m kapanışta trailing çalıştı.
- **Canlı log'da 18:16 local (15:16 UTC) hâlâ trail_skipped var** → trailing kapanışa kadar hiç durmadı; tek sebep `no_better_trail_candidate`.
- trail event'leri `events_2026-08-14.jsonl`'da YOK, yalnız `paper_trade.log`'da.

Örnek satır:
```
{"event_type": "trail_skipped", "trade_id": "PYTHUSDT-0", "symbol": "PYTHUSDT", "side": "short", "reason": "no_better_trail_candidate"}
```

## 2) trail_steps (veri #2)
`trades_history.jsonl` (kapanış kaydı):
```json
"trailing_count": 0, "trail_steps": [], "trail_count": 0
```
→ **BOŞ**. Trailing adımı hiçbir zaman üretilmedi.

## 3) protection_state.last_invalid_fingerprint (veri #3)
```json
"protection_state": {}
```
→ **BOŞ**. `last_invalid_fingerprint` YOK. Baş mühendisin hipotez #4 (last_invalid_fingerprint tıkanması) **ELEKTEN GEÇMİYOR**.

## 4) Kök Neden Analizi (simülasyonla kanıt)
Sunucuda bot'un kendi kodu (`_fvg_multihop` + `detect_fvgs` + `_fvg_close_confirmed`) gerçek `PYTHUSDT_15m.csv` verisiyle çalıştırıldı.

Parametreler: entry=0.03934, SL=0.03966, TP=0.03878, tick=1e-05, risk_pts=0.00031983, TRAIL_MIN_MOVE_MULT=0.2 → iyileşme eşiği **0.00006397**. TRAIL_MODE=retrace, ATR_TRAIL_MULT=0.1.

### Hesaplama
Short için aday koşulu: `new_sl = fvg.top + atr_buffer` ve `cmp_sl < current_sl (0.03966)` VE `(0.03966 - cmp_sl) > 0.00006397`.
→ **fvg.top < 0.039577** olan ONAYLANMIŞ (close_confirmed) bearish FVG gerekli.

### Son 50 15m bardaki bearish FVG'ler:
| FVG | top | confirmed | new_sl | move | sonuç |
|-----|-----|-----------|--------|------|-------|
| #1767 | 0.04040 | ✅ | 0.040419 | -0.000759 | RED (SL üstü) |
| #1768 | 0.04041 | ✅ | 0.040429 | -0.000769 | RED (SL üstü) |
| #1769 | 0.04030 | ✅ | 0.040319 | -0.000659 | RED (SL üstü) |
| #1773 | 0.04012 | ❌ | - | - | onay yok |
| #1784 | 0.04007 | ✅ | 0.040089 | -0.000429 | RED (SL üstü) |
| #1785 | 0.04005 | ✅ | 0.040069 | -0.000409 | RED (SL üstü) |
| **#1800** | **0.03904** | ❌ | - | - | **onay yok (tek uygun konum, invalidation)** |

### Karar zinciri
1. **Onaylanan** FVG'lerin top'u hep 0.040+ → new_sl > current_sl → SL'yi iyileştirmiyor (red).
2. **SL altında kalan tek FVG (#1800, top=0.03904)**: `_fvg_close_confirmed` = False.
   - Bearish FVG #1800 (bottom=0.03902, top=0.03904): sonraki bar close'u (0.0391) > fvg.top → **invalidation** (close > top).
3. Sonuç: **hiçbir "gap içi kapanış" onayı yok** → `no_better_trail_candidate`.

## 5) Baş Mühendisin Hipotez Değerlendirmesi
1. **"Uygun FVG oluşmamış"** → ✅ **DOĞRU (genişletilmiş)**: FVG'ler oluştu ama onaylananların top'u hep SL üstünde; SL altındaki tek FVG invalidation oldu.
2. **is_placeable reddi** → ❌ YANLIŞ bu trade için: `retrace_only=True` iken `placeable = not retrace_only = False` → `(not placeable or ...)` otomatik True. is_placeable bu modda DEVRE DIŞI.
3. **İyileşme yetersiz** → kısmen; eşik 0.00006397, adaylar zaten 0.0004+ uzakta ama yanlış yönde.
4. **last_invalid_fingerprint** → ❌ ELEK: `protection_state={}` boş.
5. **extractor kurulmamış** → ❌ YANLIŞ: extractor her dakika çalıştı (trail_skipped üretimi bunu kanıtlar).

## 6) Asıl Kök Neden (öz)
Fiyat short lehine düştü (TP'ye gitti) ama **retrace onayı (`_fvg_close_confirmed`) hiç oluşmadı**:
düşüşte oluşan bearish FVG'lerin gap'i yüksekte kaldı (SL iyileşmedi) veya fiyat gap'in üstünde kapandı (invalidation).
`TRAIL_MODE=retrace` tasarımı gereği fiyat gap içinde KAPANMADIKÇA SL/TP hareket etmiyor — PYTHUSDT'de o retrace hiç gerçekleşmedi.

## Notlar
- Aktif pozisyon kârdayken TP ile kapandı (+32.92 $) → kayıp yok, potansiyel kar kilitleme kullanılamadı.
- trade_state.json bugünkü PYTHUSDT: `open=false`.
- 1m/15m CSV yazımı 15:00 UTC civarı durmuş görünüyor (ayrıca incelenebilir).
