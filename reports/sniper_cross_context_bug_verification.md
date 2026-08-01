# Sniper Cross-Context Bug Verification

**İncelenen kaynak:** `ahmetonurof-lab/sniper/main` üzerinden çekilen güncel dosyalar ve ekli `sniper_cross_context_bug_report.md`.

## Önemli kapsam uyarısı

Ekli rapor ile güncel `main` arasında belirgin sürüm/branch farkı var. Raporda geçen bazı metodlar güncel dosyada hiç yok: `_emergency_close`, `normalize_order_event`, `parse_market_fill`, `validate_protection_with_actual_fill` ve `order_manager._repair_locks`. Bu maddeler güncel `main` için bug olarak onaylanamaz; rapor başka bir commit, branch veya yerel çalışma ağacından üretilmiş görünüyor.

## Sonuç özeti

| ID | Sonuç | Değerlendirme |
|---|---|---|
| BUG-1 | Güncel main'de yok | `entry_manager.py` içinde `_emergency_close` görünmüyor. Stale rapor veya farklı branch. |
| BUG-2 | Güncel main'de yok | `user_data_handler.py` doğrudan raw `L` fiyatını kullanıyor; `NormalizedOrderEvent.fill_price` akışı mevcut dosyada yok. |
| BUG-3 | Koşullu gerçek bug | `orchestrate_trail()` `stop_loss`/`take_profit` yazıyor; `check_exit()` bu alanları fallback olarak okusa da `order_manager` ve botun ana akışı `sl`/`tp` kullanıyor. Bu method çağrılıyorsa state divergence gerçek, mevcut botun static `evaluate_trail()` akışında ise latent. |
| BUG-4 | Yanlış | `FVG` gerçekten `@dataclass(frozen=True)`, fakat `object.__setattr__` frozen dataclass üzerinde Python'da çalışır. Bu kullanım kötü tasarım olabilir, rapordaki `TypeError` iddiası doğru değil. |
| BUG-5 | Kısmen gerçek | `session.py` CBDR gün anahtarı 22:00-00:00 için takvim gününü, `state_manager._today()` ise ertesi günü kullanıyor. Anahtarlar tutarsız; ancak doğrudan “günde 2 trade” sonucu bu dosyalarla tek başına kanıtlanmıyor. |
| BUG-6 | Yanlış | `ActiveTrade` frozen değil, normal `@dataclass`; recovery mutasyonu TypeError üretmez. |
| BUG-7 | Güncel main'de yok | `_emergency_close` bulunmadığı için side belirsizliği bu branch'te doğrulanamaz. |
| BUG-8 | Gerçek risk | User-data handler exit timestamp için `time.time()` kullanıyor, Binance event timestamp'i kullanılmıyor. Bu clock skew ve event ordering riskidir; mevcut handler'da `E` alanı hiç okunmuyor. |
| BUG-9 | Güncel main'de yok | `order_manager.py` içinde `_repair_locks` veya `repair_protection` lock kullanımı görünmüyor. Rapor başka sürümle eşleşiyor. |
| BUG-10 | Gerçek, düşük olasılıklı | `_bump_to_min_notional()` float `ceil` kullanıyor. Sınır değerlerde step/notional rounding hatası mümkün; Decimal veya exchange precision helper daha güvenli. |
| BUG-11 | Gerçek ama zararsız | `exit_lifecycle.execute()` pending exit alanlarını iki kez normalize etmeye çalışıyor. İlk blok alanları `None` yaptıktan sonra ikinci blok pratikte çalışmıyor; redundant migration artığı. |
| BUG-12 | Gerçek | Exit idempotency key yalnızca `entry_bar_index + entry_price` içeriyor. Aynı bar/fiyatta yeniden giriş olursa farklı trade aynı key'e çarpabilir. Benzersiz `trade_id` veya entry timestamp eklenmeli. |
| BUG-13 | Güncel main'de yok | `entry_manager.py` içinde `parse_market_fill()` görünmüyor; rapor başka sürümle eşleşiyor. |
| BUG-14 | Güncel main'de yok | Güncel `user_data_handler.py` normalize edilmiş ve legacy iki handler olarak ayrılmıyor; rapordaki `Normalized`/`Legacy` ayrımı bu dosyada yok. |
| BUG-15 | Bug değil, parity/design kontrolü | Canlı `risk_pts` ile gerçek `risk_dist` farklı kavramlar. Fallback SL'nin `risk_pts * 2` olması bilinçli olabilir; fakat qty ve SL sonrası risk aynı canonical değer üzerinden hesaplanmalı. Şu an parity riski var, kesin runtime bug değil. |
| BUG-16 | Gerçek ama zararsız | `detect_phase()` içindeki `isinstance(dt, int)` dalı mevcut çağrı akışında kullanılmıyor. Temizlenebilir, para kaybettiren bug değil. |
| BUG-17 | Gerçek, düşük risk | `CircuitBreaker.is_open` lock almadan state okuyor; async tek event loop'ta risk düşük, fakat concurrent task/thread erişimi için snapshot/lock disiplini daha doğru. |
| BUG-18 | Güncel main'de yok | `validate_protection_with_actual_fill` metodu mevcut `entry_manager.py` içeriğinde görünmüyor. |
| BUG-19 | Doğrulanmadı | Paylaşılan güncel `recovery_manager.py` içinde rapordaki nested helper'lar görünmüyor. Stale veya farklı dosya sürümü. |
| BUG-20 | Güncel main'de yok | Güncel `order_manager.py` içinde `tp_unchanged` bulunmuyor. |
| BUG-21 | Güncel main'de yok | `actual_qty`/`order_qty` akışı güncel `entry_manager.py` içinde yok. |
| BUG-22 | Yanlış | `_commit_confirmed_exit()` pop sonrası `if not trade: return False` kontrolünü içeriyor. Rapor bunu riskli diye işaretlese de mevcut kod korumalı. |
| BUG-23 | Gerçek davranış, niyet belirsiz | `should_trade()` `cbdr_width_pct=None` olduğunda profile tanımlıysa `True` döner. CBDR ölçülemiyorsa trade'e izin vermek istenmiyorsa bu güvenlik açığıdır; mevcut davranış bilinçli ise bug değildir. Ben güvenli varsayılan olarak `False` öneririm. |
| BUG-24 | Bug değil, politika | Filled FVG'ler `max_age`, unfilled FVG'ler `2 * max_age` ile temizleniyor. Asimetrik ama açıkça kodlanmış politika; strateji sahibi onaylamadıkça bug denemez. |
| BUG-25 | Gerçek güvenlik bug'ı | Bozuk risk state JSON'unda `_load_state()` `peak_equity=0.0` döndürüyor. Böylece `get_current_dd()` her zaman 0 döner ve devre kesici tetiklenemez. Güvenli fallback `initial_equity` olmalı, ayrıca bot trade açmadan önce state bütünlüğünü doğrulamalı. |
| BUG-26 | Bug değil | Rate limiter'ın ilk çağrıyı hemen geçirmesi beklenen davranış; `_last=0` ile negatif wait oluşması normal. |
| BUG-27 | Doğrulanmadı | Güncel `paper_trade_logger.py` içeriği çekilmeden global `_RUN_ID` yarış koşulu kesinlenemez. Async tek loop'ta bile interleaving mümkün olabilir ama kanıt yok. |
| BUG-28 | Yanlış/false positive | `cleanup_old_event_logs()` dizin yokluğunu güvenli şekilde ele alıyorsa `FileNotFoundError` üretmez. Raporun kendi notu da bunun güvenli olduğunu söylüyor. |

## Gerçek ve öncelikli maddeler

### P0/P1 olarak düzeltilecekler

1. **BUG-25:** Bozuk risk state dosyası devre kesiciyi fiilen devre dışı bırakıyor. `peak_equity` için `initial_equity` fallback'i, state schema validation ve bozuk dosyada fail-closed davranış eklenmeli.
2. **BUG-12:** Exit idempotency için benzersiz trade kimliği kullanılmalı. `entry_bar_index + entry_price` tek başına güvenli değil.
3. **BUG-3:** `orchestrate_trail()` gerçekten çağrılıyorsa `sl`/`tp` canonical alanlarına yazmalı. Ayrıca `stop_loss`/`take_profit` kullanımını ya tamamen kaldırın ya da tüm execution katmanında tek standarda çevirin.
4. **BUG-8:** WS timestamp/event ordering için Binance'in `E` alanı kullanılmalı; local receive time ayrıca `received_at` olarak tutulmalı.
5. **BUG-10:** Min-notional quantity hesaplaması Decimal veya exchange'in precision/amount helper'ı ile yapılmalı.

### P2 / karar gerektirenler

- **BUG-5:** `session_day` ile quota date aynı kavram olarak kullanılacaksa ortak helper'a taşınmalı. Önce “CBDR günü 22:00'de başlayan gün mü, UTC takvim günü mü?” kesinleştirilmeli.
- **BUG-23:** `cbdr_width_pct=None` için güvenli varsayılan `False` olmalı. Veri yokken poison filtresini bypass etmek tehlikeli.
- **BUG-15:** SL hesaplama, precision, minimum mesafe ve qty sizing tek canonical pipeline'a alınmalı.

## Raporun en büyük problemi

Ekli rapor 28 maddenin hepsini aynı güncel kod tabanına aitmiş gibi sunuyor. Bu doğru değil. En az 9 madde güncel `main` dosyalarında bulunmayan sembol/metodlara dayanıyor; önce commit/branch sabitlenmeden bu maddeleri “gerçek bug” diye düzeltmek yanlış patch üretir.

## Önerilen doğrulama sırası

```text
1. sniper commit SHA'sını sabitle.
2. Bug raporunu üreten ajanla aynı SHA'da tekrar tarama yap.
3. Her iddia için dosya + metod + gerçek satır + çağrı zinciri ekle.
4. Runtime path üzerinde çağrılıyor mu kontrol et.
5. Unit test ile reproduce et.
6. Sadece reproduce edilen gerçek bug'ı patchle.
7. Entry parity ve order lifecycle regression testlerini çalıştır.
```

## Son karar

Raporun tamamı güvenilir değil. **BUG-8, BUG-10, BUG-11, BUG-12, BUG-16, BUG-17, BUG-23 ve BUG-25 gerçek veya ciddi doğrulama adayı; BUG-3 çağrı yoluna bağlı; BUG-4 ve BUG-6 yanlış; BUG-1/2/7/9/13/14/18/19/20/21 güncel main için sürüm uyuşmazlığı; BUG-22/26/28 false positive.**
