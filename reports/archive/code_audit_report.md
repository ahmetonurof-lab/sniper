# 🔴 Sniper Bot — Kapsamlı Kod Denetimi Raporu

> **Tarih:** 2026-08-01
> **Kapsam:** `sniper/src/` — tüm modüller, tüm public fonksiyonlar, tüm hata sınıfları
> **Yöntem:** Statik analiz, grep taraması, çapraz bağlam kontrolü, test kalitesi incelemesi

---

## BULGU-01 — `ActiveTrade`'de `pending_exit_*` Alanları Tanımsız (Dinamik Attribute Injection)

**[CRITICAL]**

**BULGU:** `ActiveTrade` dataclass'ında `pending_exit_price`, `pending_exit_qty`, `pending_exit_order_id`, `pending_exit_timestamp`, `pending_exit_reason` alanları **TANIMLI DEĞİL**. Ancak `user_data_handler.py` ve `exit_lifecycle.py`'de `trade["pending_exit_price"] = price` şeklinde yazılıyor. `ActiveTrade.__setitem__` → `setattr()` sayesinde runtime'da çalışıyor, AMA:

1. `__dataclass_fields__` bu alanları İÇERMEZ → `keys()`, `__iter__` gibi yöntemlerle bu alanlar görünmez.
2. [exit_lifecycle.py:718-724](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L718-L724) `record = {**trade, ...}` — `ActiveTrade.__iter__` yalnızca `__dataclass_fields__`'i döndürür → **pending_exit_\* alanları trades_history.jsonl'e YAZILMAZ** → post-mortem debug kaybı.
3. Type checker (mypy/pyright) bu alanları görmez → tip-bağımlı IDE refactoring bu alanları kaçırır.

**KANIT:**
- [models.py:548-552](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py#L548-L552) — `keys()` ve `__iter__` yalnızca `__dataclass_fields__` döndürür
- [user_data_handler.py:246-249](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/user_data_handler.py#L246-L249) — `trade["pending_exit_price"] = price` (tanımsız alan yazımı)
- [exit_lifecycle.py:718-724](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L718-L724) — `{**trade, ...}` spread — `pending_exit_*` alanları kaybolur

**ETKİ:** Post-mortem analiz verisi eksik. `trades_history.jsonl`'de pending_exit verileri yok. Type checker hiçbir uyarı vermez.

**ÖNERİ:** `ActiveTrade`'e `pending_exit_price: float | None = None`, `pending_exit_qty: float | None = None`, `pending_exit_order_id: str | None = None`, `pending_exit_timestamp: int | None = None`, `pending_exit_reason: str | None = None` alanlarını ekle. Veya `exit_unconfirmed_reason` gibi zaten dinamik olarak eklenen diğer alanları da aynı şekilde formalize et.

---

## BULGU-02 — `_save_fvg_state` ve `_load_fvg_state` Sessiz Exception Yutma: Veri Kaybı

**[CRITICAL]**

**BULGU:** FVG state dosyası yazma/okuma işlemlerinde `except Exception: pass` var. Bu kritik çünkü bu dosya recovery path'inde kullanılıyor. Disk dolu, izin hatası veya bozuk JSON durumunda:
- Trade açılır ama FVG state diske yazılamaz
- Restart sonrası FVG verisi kayıp → recovery eksik

**KANIT:**
- [bot.py:100-111](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L100-L111) — `_save_fvg_state`: `except Exception: pass`
- [bot.py:114-122](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L114-L122) — `_load_fvg_state`: `except Exception: pass`
- [exit_lifecycle.py:698-709](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L698-L709) — FVG state cleanup: `except Exception: pass`

**ETKİ:** Restart sonrası FVG verisi kayıp → bot yanlış FVG ile trailing yapabilir veya korumayı eksik kurabilir. Disk I/O hatası tamamen görünmez.

**ÖNERİ:** `except Exception: pass` yerine `except Exception as e: log.warning("[FVG_STATE] yazma/okuma hatası: %s", e)` ve FVG state'i critical veri olarak kabul ediliyorsa retry veya in-memory fallback ekle.

---

## BULGU-03 — `exit_lifecycle.py:337` Stale Trade Referansı: Data Race

**[CRITICAL]**

**BULGU:** `execute()` fonksiyonunda pending_exit → exit promote bloğundan SONRA, satır 337'de `trade = self._active_trades.get(sym)` ile trade TEKRAR okunuyor. Bu arada WS callback'i aynı trade'i pop'lamış olabilir → trade=None → return False → exit accounting KAYBOLUR ama pozisyon borsada KAPANMIŞ durumda.

**KANIT:**
- [exit_lifecycle.py:321-341](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L321-L341):
```python
# Satır 322-335: pending → exit promote (trade["exit_price"] = ...)
# Satır 337: trade = self._active_trades.get(sym)  ← TEKRAR OKU
# Satır 338-340: if not trade: return False ← EXIT KAYBOLUR
```

**ETKİ:** Eğer WS callback ve 1m bar check aynı anda exit tetiklerse, biri promote yapar ama diğeri trade'i pop'lar → PnL muhasebesi yapılmaz, balance güncellenmez, ama pozisyon borsada kapalı. Net sonuç: bakiye verisi yanlış.

**ÖNERİ:** `trade` değişkenini lock içinde tek referans olarak kullan, `self._active_trades.get(sym)` tekrar çağırma — zaten `execute()` fonksiyonunun başında alınan `lock` ile korunuyor ama farklı `_trade_id_key` gelirse farklı lock oluşur.

---

## BULGU-04 — `exit_lifecycle.py:613` Double-Pop Race: PnL Kaybolabilir

**[HIGH]**

**BULGU:** `_commit_confirmed_exit` fonksiyonunun 613. satırında `trade = self._active_trades.pop(sym, None)` yapılıyor. Ancak 606. satırda `trade["status"] = STATUS_CLOSED` zaten set edilmiş. İki farklı exit path (WS + 1m bar check) farklı trade_key ile lock alabilir → ikisi de commit'e ulaşır → ilki pop yapar, ikincisi `trade=None` → return False.

**KANIT:**
- [exit_lifecycle.py:606-619](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L606-L619):
```python
trade["status"] = STATUS_CLOSED  # L606
trade = self._active_trades.pop(sym, None)  # L613
if not trade:  # L614 — ikinci çağrı burada yakalanır
    return False  # PnL commit yapılmaz!
```

**ETKİ:** İkinci exit path'i trade'i göremez → PnL kaydedilmez, balance güncellenmez. Hâlâ SL/TP cleanup yapılmamış olabilir.

**ÖNERİ:** Lock'u `sym` bazlı yaparak race'i kapat. `_trade_identity_key` bazlı lock farklı key üretebilir.

---

## BULGU-05 — `state_writer.py:83-85` — `trade.runtime.protection` Her Zaman Boş ProtectionState

**[HIGH]**

**BULGU:** `write_state()` fonksiyonunda `trade.runtime.protection.sl_status()` ve `.tp_status()` ve `.health` çağrılıyor. Ancak `ActiveTrade.runtime` default `TradeRuntimeState()` — bu da default `ProtectionState()` ile oluşuyor. **Hiçbir yerde `trade.runtime.protection`'a SL/TP bilgisi YAZILMIYOR.** Tüm SL/TP state'i `trade["sl_order_id"]`, `trade["tp_order_id"]` flat field'larında.

**KANIT:**
- [state_writer.py:83-85](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/state_writer.py#L83-L85):
```python
"sl_status": trade.runtime.protection.sl_status(trade.get("sl", 0)),
"tp_status": trade.runtime.protection.tp_status(trade.get("tp", 0)),
"protection_health": trade.runtime.protection.health,
```
- [models.py:355-396](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py#L355-L396) — `ProtectionState`: sl_current default None → `sl_status()` HER ZAMAN "MISSING" döner
- [models.py:519](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py#L519) — `runtime: TradeRuntimeState = field(default_factory=TradeRuntimeState)` — hiç güncellenmez

**ETKİ:** `live_state.json`'da `sl_status` HER ZAMAN "MISSING", `protection_health` HER ZAMAN "BROKEN" görünür → operatör yanlış alarm alır, gerçek broken durumu ayırt edemez. Dashboard güvenilmez.

**ÖNERİ:** Ya `ProtectionLifecycleService`'i `trade.runtime.protection`'ı dolduracak şekilde entegre et, ya da `state_writer`'da flat field'lardan (`sl_order_id` var mı yok mu) status türet.

---

## BULGU-06 — `_save_fvg_state` / `_load_fvg_state` TOCTOU Race: Eşzamanlı Write Corruption

**[HIGH]**

**BULGU:** `_save_fvg_state` (bot.py:100-111) dosyayı oku → dict güncelle → geri yaz yapıyor. Birden fazla sembol aynı anda trade açarsa:
1. Thread A: oku → `{"BTCUSDT": {...}}`
2. Thread B: oku → `{"BTCUSDT": {...}}`
3. Thread A: yaz → `{"BTCUSDT": {...}, "ETHUSDT": {...}}`
4. Thread B: yaz → `{"BTCUSDT": {...}, "SOLUSDT": {...}}` — ETHUSDT kaybedildi!

asyncio single-threaded olsa bile `await` noktaları arasında bu fonksiyon sync olduğu için pratikte sorun yaşanmayabilir — **AMA** recovery_manager'dan çağrılırsa farklı task'lar yarışabilir.

**KANIT:**
- [bot.py:100-111](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L100-L111) — sync I/O, lock yok, oku-güncelle-yaz pattern

**ETKİ:** FVG state veri kaybı → restart sonrası eksik FVG bilgisi.

**ÖNERİ:** Atomik yazma: önce temp dosyaya yaz, sonra rename. Veya tüm FVG state'ini memory'de tut, tek noktadan diske flush et.

---

## BULGU-07 — 36+ Bare `except Exception: pass` — Sessiz Hata Yutma Pandemisi

**[HIGH]**

**BULGU:** Repo genelinde 36+ yerde `except Exception:` (bare, log bile yok) ile hata tamamen yutulmuş. Bunların bir kısmı zararsız (log rotate) ama kritik olanlar:

| Dosya | Satır | Bağlam | Risk |
|---|---|---|---|
| [bot.py:110](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L110) | FVG state save | Veri kaybı | HIGH |
| [bot.py:120](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L120) | FVG state load | Recovery eksik | HIGH |
| [exit_lifecycle.py:521](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L521) | Position verify | Exit doğrulaması atlanır | CRITICAL |
| [exit_lifecycle.py:549](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L549) | FILLED order check | Yanlış REPAIR_REQUIRED | HIGH |
| [recovery_manager.py:486](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/recovery_manager.py#L486) | SL/TP placement | Korumasız pozisyon | CRITICAL |
| [order_manager.py:646](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/order_manager.py#L646) | SL placement (closePosition) | Korumasız pozisyon | HIGH |
| [order_manager.py:966](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/order_manager.py#L966) | Repair cancel | Eski emir aktif kalır | HIGH |

**ETKİ:** Operatör hiçbir uyarı almadan pozisyon korumasız kalabilir, exit doğrulaması atlanabilir.

**ÖNERİ:** Her `except Exception: pass` yerine en az `log.warning()` ekle. Kritik yollarsa (SL/TP placement, position verify) `pass` YASAK — en az bir incident kaydı at.

---

## BULGU-08 — `exit_lifecycle.py:504` — `abs(amt) < 0.0001` Sabit Eşik: Micro-Cap Token'larda Yanlış Kapanış

**[HIGH]**

**BULGU:** Pozisyon kapanış doğrulamasında `abs(amt) < 0.0001` sabit eşiği kullanılıyor. DOGEUSDT gibi düşük fiyatlı token'larda 0.0001 qty ciddi bir değerdir (özellikle leverage ile). Ayrıca bazı Binance token'larında `positionAmt` 5-6 ondalık basamak precision'a sahip olabilir.

**KANIT:**
- [exit_lifecycle.py:503-506](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L503-L506):
```python
amt = float(p.get("positionAmt", 0))
if abs(amt) < 0.0001:
    pos_closed = True
```

**ETKİ:** Micro-cap token'larda kalan küçük pozisyon "kapalı" kabul edilir → orphan pozisyon kalır, korumasız.

**ÖNERİ:** `step_size` veya `minQty` bazlı dinamik eşik kullan. Veya `amt == 0` kontrolü (Binance zaten 0 döner kapanınca).

---

## BULGU-09 — `entry_manager.py:765` — TP Başarısız Olsa Bile `success=True` Dönüyor

**[CRITICAL]**

**BULGU:** `execute_live_entry` fonksiyonunda SL başarılı olduktan sonra TP emri verilir. TP başarısız olursa (`tp_id` boş) sadece `log.warning()` yapar ve **`success=True` döner** (satır 765). Çağıran `bot.py` bu success=True'yu alır ve trade'i `active_trades`'e kaydeder — AMA TP koruma emri YOK. Pozisyon TP olmadan çalışır.

**KANIT:**
- [entry_manager.py:748-774](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/entry_manager.py#L748-L774):
```python
# L748-763: TP BASARISIZ — sadece log.warning
else:
    log.warning("[ORDER] %s TP BASARISIZ! resp=%s", sym, tp_resp)

# L765: Yine de success=True dönüyor!
return EntryExecutionResult(
    success=True,
    ...
    tp_order_id=tp_id,  # BOŞ STRING!
)
```
- [bot.py:796-802](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L796-L802) — `if not exec_result.success` kontrolü → TP başarısız olsa bile trade kaydedilir

**ETKİ:** Pozisyon sadece SL ile korunur, TP emri yok. Kazançlı trade'ler TP'ye ulaşamaz, sadece SL veya trailing ile kapanır. Unlimited risk exposure (trailing yoksa).

**ÖNERİ:** TP başarısız olduğunda ya `success=False` döndür ve pozisyonu kapat, ya da hemen repair_protection tetikle. "TP olmadan trade" açık bırakmak tehlikeli.

---

## BULGU-10 — `ActiveTrade.__contains__` Tehlikesi: Dinamik Alanlar `hasattr` ile Yanıltıcı

**[MEDIUM]**

**BULGU:** `ActiveTrade.__contains__` → `hasattr(self, key)` kullanıyor. Dataclass property'leri ve method'ları da `hasattr` ile True döner. Örnek: `"stop_loss" in trade` → True (property), `"keys" in trade` → True (method). Bu, dict uyumluluğu beklentisini kırar.

**KANIT:**
- [models.py:545-546](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py#L545-L546):
```python
def __contains__(self, key: str) -> bool:
    return hasattr(self, key)
```

**ETKİ:** `"stop_loss" in trade` → True, ama `stop_loss` bir property (alias), gerçek alan `sl`. Code'da `if "stop_loss" in trade:` şeklinde kontrol yapılırsa her zaman True döner → yanlış branching.

**ÖNERİ:** `__contains__` yalnızca `__dataclass_fields__` anahtarlarını kontrol etsin: `return key in self.__dataclass_fields__`

---

## BULGU-11 — `normalize_order_event` order_id Çakışma Riski

**[MEDIUM]**

**BULGU:** `normalize_order_event` fonksiyonunda `order_id` önce `raw_order.get("c", "")` (client_order_id), yoksa `raw_order.get("i", "")` (server orderId) olarak alınıyor. Ancak Binance algo order'larında `c` ve `i` farklı ID formatları döner. `_oid_matches_trade` fonksiyonu her iki format ile karşılaştırma yapıyor ama `_resolve_fill_result` fonksiyonunda `oid` hangi format olduğuna bakılmadan SL/TP ayrımı yapılıyor → yanlış result ataması mümkün.

**KANIT:**
- [user_data_handler.py:85-86](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/user_data_handler.py#L85-L86):
```python
order_id=str(raw_order.get("c", "") or raw_order.get("i", "")),
client_order_id=str(raw_order.get("c", "")),
```
- Algo order'larda `c` prefix ("sl_BTCUSDT_...") `i` ise sayısal ID → format uyumsuzluğu

**ETKİ:** SL/TP fill'i yanlış kategorize edilebilir → yanlış result kaydı → istatistik çarpıklığı. Ciddi durumlarda trade yanlış yönde kapanabilir.

**ÖNERİ:** `order_id` ve `client_order_id`'yi ayrı ayrı match'le, birleştirme yapma.

---

## BULGU-12 — `exit_lifecycle.py:700-707` — File Handle Leak

**[MEDIUM]**

**BULGU:** FVG state dosyası okuma/yazma işleminde `open()` çağrısı `with` statement olmadan kullanılıyor. Exception durumunda dosya handle açık kalır.

**KANIT:**
- [exit_lifecycle.py:700-707](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L700-L707):
```python
data = json.loads(
    open(self._fvg_state_file, "r", encoding="utf-8").read()
)
data.pop(sym, None)
open(self._fvg_state_file, "w", encoding="utf-8").write(
    json.dumps(data, ensure_ascii=False)
)
```

**ETKİ:** File descriptor leak → uzun süreli çalışmada "too many open files" hatası.

**ÖNERİ:** `with open(...) as f:` kullan.

---

## BULGU-13 — `TradeStatus(str)` Sınıfı Amacına Hizmet Etmiyor

**[MEDIUM]**

**BULGU:** `TradeStatus` sınıfı `str`'den türetilmiş ama enum DEĞİL. Class attribute'ları kullanılmıyor — kodda hep string literal'ler (`"ACTIVE"`, `"PENDING"`) veya `STATUS_ACTIVE` modül-level sabitleri kullanılıyor. `TradeStatus` sınıfı dead code.

**KANIT:**
- [models.py:328-337](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py#L328-L337) — `TradeStatus(str)` tanımı
- grep `TradeStatus.ACTIVE` → 0 sonuç (src/ içinde hiç kullanılmıyor, yalnızca `models.py:425` default value'da)
- Kodda hep `STATUS_ACTIVE`, `STATUS_CLOSED` gibi modül-level sabitler kullanılıyor

**ETKİ:** Gereksiz karmaşıklık, yanlış güvenlik hissi. Type checker'a fayda sağlamıyor.

**ÖNERİ:** Ya `str` Enum'a dönüştür ve tüm kullanımları migrate et, ya da kaldır.

---

## BULGU-14 — `ProtectionSlot(str)` Aynı Durum — Dead Code

**[LOW]**

**BULGU:** `ProtectionSlot` ve `ProtectionRef`, `ProtectionState` sınıfları tanımlı ama `state_writer.py:83-85` dışında hiçbir yerde gerçek veriyle doldurulmuyor. Patch 3/4'te entegre edileceği belirtilmiş ama yapılmamış.

**KANIT:**
- [models.py:340-396](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py#L340-L396) — Tanımlar
- `state_writer.py:83-85` — Kullanılıyor ama HER ZAMAN default (boş) veriyle

**ETKİ:** Dashboard'da yanıltıcı veri.

---

## BULGU-15 — Broad Exception Yakalama: 56+ `except Exception as e:` + 36+ Bare `except Exception:`

**[HIGH]**

**BULGU:** Toplam 92+ yerde `except Exception` pattern'i var. Bunlar:
- `KeyboardInterrupt` ve `SystemExit` dahil tüm exception'ları yakalar
- Bazıları (özellikle recovery_manager ve order_manager'da) **gerçek API hataları ile yapısal hataları** (TypeError, AttributeError) karıştırır
- Bug'lar sessizce yutulur, production'da fark edilmez

**ÖNERİ:** En az REST API çağrıları için spesifik exception tipi kullan (`aiohttp.ClientError`, `ConnectionError`, vb.). `TypeError`, `AttributeError` gibi programcı hatalarını yutma.

---

## BULGU-16 — Test Kalitesi: MagicMock Yoğunluğu → Tip Bağımlı Bug'lar Kaçar

**[HIGH]**

**BULGU:** Test dosyalarında 500+ `MagicMock/AsyncMock` kullanımı var. Özellikle:
- `test_recovery_manager.py`: REST client tamamen MagicMock → dict interface test ediliyor, ActiveTrade davranışı test edilmiyor
- `test_user_data_handler.py`: trade nesnesi ActiveTrade değil, düz dict mock → setattr/getattr farkı test edilmez
- `test_order_manager.py`: trade dict olarak geçiliyor → BULGU-01'deki dinamik alan injection hiç test edilmiyor

**KANIT:**
- [test_recovery_manager.py:31-48](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/tests/test_recovery_manager.py#L31-L48) — `rest = MagicMock()` — 10+ method AsyncMock ile stub'lanmış
- [test_user_data_handler.py:266-269](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/tests/test_user_data_handler.py#L266-L269) — 3 adet bare `MagicMock()` parametre olarak geçilmiş

**ETKİ:** ActiveTrade'in `__setitem__` → `setattr` davranışı, property alias'ları (`stop_loss`/`sl`), `__contains__` → `hasattr` sapması hiçbir testte kontrol edilmiyor. Gerçek ActiveTrade ile başarısız olacak senaryolar mock'larla maskeleniyor.

**ÖNERİ:** Kritik path testlerinde (exit_lifecycle, user_data_handler, order_manager) gerçek `ActiveTrade` nesnesi kullan. En az bir "entegrasyon stili" test serisi ekle.

---

## BULGU-17 — `bot.py:962` — `fvg.bar_index` Attribute: FVG'de `bar_index` Tanımlı Değil

**[CRITICAL]**

**BULGU:** `bot.py:962` ve `bot.py:649`'da `fvg.bar_index` kullanılıyor. Ancak `FVG` dataclass'ında alan adı `real_index`, `bar_index` DEĞİL. `FVG` frozen dataclass olduğu için `fvg.bar_index` çağrısı `AttributeError` fırlatır → trade KAYIT EDİLEMEZ → PendingLock context manager cleanup yapar ama asıl ENTRY BAŞARISIZ OLUR.

**KANIT:**
- [models.py:150-159](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py#L150-L159) — `FVG` dataclass: `real_index: int` — `bar_index` YOK
- [bot.py:962](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L962) — `fvg_bar_index=fvg.bar_index if fvg else -1`
- [bot.py:649](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L649) — `"bar_index": fvg.bar_index`
- [bot.py:986](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L986) — `"fvg_bar_index": fvg.bar_index if fvg else -1`

**ETKİ:** Her FVG'li entry girişimi `AttributeError` ile başarısız olur → **HİÇBİR TRADE AÇILAMAZ**. Bu ya production'da farklı bir FVG sınıfı kullanılıyor (o zaman test parity bozuk), ya da bu bir aktif bug.

> **AÇIK SORU:** Bu bug gerçekten production'da mı var? Eğer `trigger_fvg` başka bir türde geliyorsa (non-dataclass FVG, bar_index attribute'u olan), bu sorun yoktur ama tip uyumsuzluğu devam eder. `retrace_state.py`'daki FVG tipini kontrol etmek gerekir.

**ÖNERİ:** `fvg.bar_index` → `fvg.real_index` olarak düzelt. Veya FVG dataclass'ına `bar_index` property alias'ı ekle (ama bu shadow'lamayı artırır — tavsiye edilmez).

---

## BULGU-18 — `entry_manager.py:585-595` — `trigger_fvg.bar_index` Aynı Bug

**[CRITICAL]**

**BULGU:** `execute_live_entry` fonksiyonunda da `trigger_fvg.bar_index` kullanılıyor (satır 591). Aynı BULGU-17 — `FVG` dataclass'ında `bar_index` yok.

**KANIT:**
- [entry_manager.py:591](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/entry_manager.py#L591) — `"bar_index": trigger_fvg.bar_index`

---

## BULGU-19 — `state_writer.py:37` — Hardcoded Feature Flag: `ws_event_normalization: False`

**[MEDIUM]**

**BULGU:** `write_state` fonksiyonunda `ws_event_normalization` feature flag'i hardcoded `False` olarak yazılıyor. Ancak `user_data_handler.py:41` `WS_EVENT_NORMALIZATION_ENABLED = cfg.WS_EVENT_NORMALIZATION_ENABLED` kullanıyor. Bu iki kaynak tutarsız → operatör `live_state.json`'a bakarak normalization'ın kapalı olduğunu sanır ama aslında config'den açık olabilir.

**KANIT:**
- [state_writer.py:37](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/state_writer.py#L37) — `"ws_event_normalization": False` — HARDCODED
- [user_data_handler.py:41](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/user_data_handler.py#L41) — `WS_EVENT_NORMALIZATION_ENABLED = cfg.WS_EVENT_NORMALIZATION_ENABLED`

**ETKİ:** Dashboard'da yanlış feature flag bilgisi.

**ÖNERİ:** `cfg.WS_EVENT_NORMALIZATION_ENABLED` değerini oku ve yaz.

---

## BULGU-20 — `exit_lifecycle.py` `_exit_log.setdefault(sym, {})` — ActiveTrade'de Değil Ama Dict'te Sorunsuz

**[LOW]**

**BULGU:** `_exit_log` düz dict olduğu için `setdefault` sorunsuz çalışır. ANCAK `console_reporter.py:48`'deki `self._log_state.setdefault(sym, {})[key] = msg` de düz dict. `setdefault` çağrılarının hiçbiri ActiveTrade üzerinde yapılmıyor → **bu bug sınıfı şu an temiz.**

**KANIT:**
- [exit_lifecycle.py:745](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L745) — `self._exit_log.setdefault(...)` — düz dict, OK
- [order_manager.py:683](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/order_manager.py#L683) — `self._repair_locks.setdefault(...)` — düz dict, OK
- [exit_lifecycle.py:158](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L158) — `self._exit_locks.setdefault(...)` — düz dict, OK

**Sonuç:** `.setdefault()` bug sınıfı şu an repoda **MEVCUT DEĞİL** (ActiveTrade üzerinde çağrılmıyor).

---

## BULGU-21 — `bot.py:779` — `_live` Flag Güvensiz Kontrol: `getattr(self, "_live", False)`

**[MEDIUM]**

**BULGU:** Entry path'inde `cfg.BINANCE_API_KEY and getattr(self, "_live", False)` kontrolü var. `_live` flag'i `__init__` içinde `self._live = False` olarak set ediliyor ama `True` yapıldığı yer... `run()` fonksiyonundaki `self._live = True` satırı. Eğer bir sebepten `run()` çağrılmadan trade denenirse (testlerde?) `_live` hep False → API key olsa bile paper mode çalışır. Bu bir güvenlik mekanizması mı yoksa bug mı belirsiz.

**KANIT:**
- [bot.py:222](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L222) — `self._live = False`
- [bot.py:779](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L779) — `cfg.BINANCE_API_KEY and getattr(self, "_live", False)`
- [bot.py:248](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py#L248) — `EntryManager(is_live=bool(cfg.BINANCE_API_KEY))` — BURADA `_live` flag KULLANILMIYOR!

**ETKİ:** `EntryManager.is_live` ve `PaperTrader._live` farklı kaynaktan okunur. `EntryManager` API key varsa hep live, `PaperTrader` sadece `run()` sonrası live → geçiş sırasında tutarsızlık mümkün.

**ÖNERİ:** Tek kaynak (`cfg.BINANCE_API_KEY`) kullan veya `_live` flag'ini `EntryManager`'a da geçir.

---

## BULGU-22 — `time.time()` Yaygın Kullanım: Test Edilemezlik

**[MEDIUM]**

**BULGU:** 50+ yerde `time.time()` veya `int(time.time() * 1000)` doğrudan çağrılıyor. Hiçbir yerde injectable clock pattern yok. Testlerde `time.time()` mock'lanmıyor → zamana bağlı backoff, cooldown, stale detection mantıkları test edilemiyor.

**KANIT:**
- [order_manager.py:115,172,179,243,498,514](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/order_manager.py#L115) — Backoff timestamp
- [exit_lifecycle.py:255](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L255) — Stale cooldown
- [user_data_handler.py:71,73](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/user_data_handler.py#L71-L73) — WS event timestamp

**ETKİ:** Zamana bağlı bug'lar sadece production'da ortaya çıkar. Backoff / cooldown / stale detection hiç test edilemez.

**ÖNERİ:** `time.time` → `clock: Callable[[], float]` injection pattern'i uygula. Testlerde `lambda: fixed_time` geçir.

---

## BULGU-23 — Paper Mode / Live Mode Farkları: Tehlikeli Sapmalar

**[HIGH]**

**BULGU:**

| Kontrol | Paper Mode | Live Mode | Risk |
|---|---|---|---|
| `EntryManager._is_live` | `False` → hemen success=True | `True` → API call | None |
| `PaperTrader._live` | `False` | `True` (run sonrası) | BULGU-21 |
| `OrderManager._is_live` | `False` → trade dict güncelle | `True` → API call | None |
| `exit_lifecycle._submit_and_verify` | `cfg.BINANCE_API_KEY` kontrolü | Aynı | **Sorunlu** |

`exit_lifecycle.py:349`'da `cfg.BINANCE_API_KEY` kontrolü yapılıyor — bu `_is_live` ile aynı DEĞİL. API key env'de set edilmişse ama bot paper mode'daysa, exit sırasında Binance'e gerçek API çağrısı yapılır → **paper mode'da gerçek market close emri gönderilebilir**.

**KANIT:**
- [exit_lifecycle.py:349](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L349) — `if cfg.BINANCE_API_KEY and not _exit_already_closed:`
- [exit_lifecycle.py:177](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py#L177) — `if _exit_result in ("SL", "TP", "WS_FALLBACK") and cfg.BINANCE_API_KEY:`

**ETKİ:** Paper mode'da API key set edilmişse gerçek emir gönderilir → istenmeyen trade operasyonları.

**ÖNERİ:** Tüm API çağrılarını `_is_live` veya `_live` flag'i ile koruma altına al. `cfg.BINANCE_API_KEY` yalnızca "API key mevcut mu?" kontrolü için kullan, "canlı mı?" kontrolü için DEĞİL.

---

## ÇAPRAZ BAĞLAM ÖZETİ

| Bug Sınıfı | Etkilenen Modüller | Çapraz Etki |
|---|---|---|
| Dinamik attribute injection | models, user_data_handler, exit_lifecycle, bot | Serialization kaybı, type checker bypass |
| Sessiz exception yutma | bot, exit_lifecycle, recovery_manager, order_manager | Korumasız pozisyon, kayıp veri |
| Paper/Live mode tutarsızlık | bot, exit_lifecycle, entry_manager, order_manager | Paper'da gerçek API call |
| `fvg.bar_index` AttributeError | bot, entry_manager | Hiçbir trade açılamaz (eğer gerçekten bug ise) |
| TP başarısız → success=True | entry_manager, bot | TP'siz pozisyon |
| State writer yanıltıcı veri | state_writer, models | Dashboard güvenilmezliği |

---

## ÖNCELİK SIRASI

1. 🔴 **BULGU-17/18** (`fvg.bar_index`) — eğer production'da tetikleniyorsa HİÇBİR TRADE AÇILAMAZ
2. 🔴 **BULGU-09** (TP fail → success=True) — korumasız pozisyon
3. 🔴 **BULGU-03/04** (exit race condition) — PnL kaybı
4. 🟠 **BULGU-23** (paper/live mode) — paper'da gerçek emir
5. 🟠 **BULGU-05** (state_writer yanıltıcı) — operatör yanlış alarm
6. 🟠 **BULGU-01** (dinamik alanlar) — serialization kaybı
7. 🟠 **BULGU-07** (sessiz exception) — görünmez hatalar
8. 🟡 **BULGU-22** (time.time) — test edilemezlik
9. 🟡 **BULGU-16** (mock yoğunluğu) — bug maskeleme
