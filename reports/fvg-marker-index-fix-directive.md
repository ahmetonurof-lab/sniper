DİREKTİF — FVG etiketi (marker) yanlış mumu işaret ediyor
================================================================================
Repo: sniper
Öncelik: DÜŞÜK-ORTA — sadece görsel/analiz doğruluğu, canlı trade kararlarını
(entry/SL/TP) ETKİLEMİYOR. detect_fvgs()'te top/bottom komşu mumlardan,
fvg_is_alive/is_retesting_fvg gerçek fiyattan çalışıyor — bu yapısal olarak
sağlam, doğrulandı. Sorun sadece snapshot/chart etiketleme katmanında.

ÖNCEKİ FIX (chart_template.html, rangedBand) — DOKUNMA, doğru çalışıyor.
Ekran kanıtı: kutu artık doğru fiyat seviyesinde çiziliyor. Sorun kutunun
kendisinde değil, "FVG ▼" etiketinin (lightweight-charts native marker,
chart_template.html satır ~214-222) hangi mumun ÜSTÜNE konduğunda.

KÖK NEDEN
---------
src/snapshot/snapshot.py: _resolve_fvg_bar_index() (satır ~166-223)

Yöntem 1 (fiyat bazlı arama, "en güvenilir" diye işaretli):
  for i in range(min(search_start, len(candles) - 1), -1, -1):
      c = candles[i]
      if c["high"] >= lo and c["low"] <= hi:
          return i
- Sadece entry_bar'dan GERİYE doğru tarıyor.
- Mumun KENDİ high/low'unun fvgTop/fvgBottom ile çakışmasını arıyor.
Ama fvg.py:detect_fvgs()'e göre top/bottom KOMŞU mumlardan (b_prev.low/high,
b_next.low/high) geliyor — ortadaki displacement mumunun (b_curr, yani
real_index) kendi aralığı bu zonla ÇAKIŞMAYABİLİR (çoğu zaman çakışmaz,
çünkü displacement mumu tanım geregi bu bölgeyi güçlü şekilde aşıp gider).
Ayrıca fiyatın FVG bölgesine gerçek dönüşü çoğu zaman entry'den SONRA
olabiliyor (PYTHUSDT örneğinde: entry_bar=22, zona ilk gerçek dokunuş
bar 25'te) — ama arama sadece geriye baktığı için bunu hiç görmüyor.

Sonuç: Yöntem 1 boş dönüyor, docstring'in kendisinin de "restart sonrası
anlamsızlaşır" dediği Yöntem 2/3'e (abs_fvg_bar + entry_bar offset) düşülüyor,
o da (muhtemelen bir restart nedeniyle) yanlış bir index üretiyor — bu
index'in gerçek top/bottom zonuyla hiçbir ilişkisi kalmıyor.

İSTENEN FIX (2 parça)
----------------------
A) Yöntem 1'i genişlet: sadece geriye değil, TÜM candles dizisinde ara
   (veya en azından entry_bar'ın biraz ilerisine kadar), ve bulunan en
   yakın eşleşmeyi entry_bar'a göre seç (entry'den önceki bir eşleşme
   varsa onu tercih et, yoksa sonrakini kullan — FVG mantıken entry'den
   önce oluşmuş olmalı ama fiyatın zona dönüşü sonra olabilir):

   def _find_price_match(candles, lo, hi, entry_bar):
       search_range = range(len(candles))
       best = None
       for i in search_range:
           c = candles[i]
           if c["high"] >= lo and c["low"] <= hi:
               dist = abs(i - (entry_bar if entry_bar is not None else i))
               if best is None or dist < best[1]:
                   best = (i, dist)
       return best[0] if best else None

   Bu, mevcut backward-only döngünün yerine geçsin.

B) Yöntem 2/3'ün ürettiği sonuca bir makul-olma kontrolü (sanity guard) ekle
   — offset matematiği restart sonrası anlamsızlaştığında sessizce yanlış
   index dönmesin:

   rel = entry_bar + (abs_fvg_bar - entry_bar_idx_abs)
   if 0 <= rel < len(candles):
       # YENİ: sonucu kabul etmeden önce makul mü diye kontrol et
       c = candles[rel]
       # chart_template.html'deki mevcut consistency-check'le AYNI mantık:
       bar_range = abs(c["high"] - c["low"])
       fvg_mid = (fvg_top + fvg_bottom) / 2
       dist = min(abs(c["high"] - fvg_mid), abs(c["low"] - fvg_mid))
       if bar_range > 0 and dist <= bar_range * 8:
           return rel
       # makul değilse kabul etme, Yöntem 4'e (heuristic) düş

TEST
----
PYTHUSDT_7973-91-89_946647_2026-08-10_053352.html'deki trade verisiyle
(fvgTop=0.0424, fvgBottom=0.04234, entryBar=22) yeni _resolve_fvg_bar_index
çağrıldığında artık candles listesinde GERÇEKTEN top/bottom'la çakışan bir
index (25 civarı) döndüğünü doğrulayan birim test ekle. Ayrıca normal
(restart olmamış, offset matematiği doğru çalışan) bir senaryoda eski
davranışın bozulmadığını doğrulayan bir regresyon testi ekle.

DEPLOY
------
Risk yok — sadece snapshot/analiz doğruluğunu iyileştiriyor, canlı trade
mantığına dokunmuyor. Test edip push etmen yeterli, ayrı onay gerekmez.
