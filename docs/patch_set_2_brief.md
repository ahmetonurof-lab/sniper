# Patch Set 2 — Exit Lifecycle Extraction: Tamamlandı (kaynak kodu), test yazımı bekliyor

Kaynak: `new_refactoring_plan1.md`, "Patch Set 2 — Exit lifecycle extraction" ve checklist madde 2.
Kontrol edilen repo: `ahmetonurof-lab/sniper` — `src/bot.py` (`_exit_trade`, satır 729-1132),
`src/trading/user_data_handler.py` (WS callback çağrısı), `src/trading/order_manager.py`,
`src/trading/entry_manager.py`, `src/config.py`, `src/models.py` (Patch 1 zaten uygulanmış hali)
okunarak hazırlandı. **Bu kez kaynak kodu (exit_lifecycle.py + bot.py + config.py + trading/__init__.py)
BEN yazdım** — Patch 1'in aksine bu bir spec değil, uygulanmış patch. Yerel ajana kalan tek iş:
test dosyaları (`tests/test_exit_lifecycle.py` yeni, `tests/test_bot.py` güncelleme).

## 🔴 Kritik bulgular — plan iki noktada güncellendi

1. **`execute()` imzası planın taslağından farklı.** Plan `execute(trade, reason, now_ms) -> bool`
   öneriyordu. Gerçek çağrı yeri iki farklı yerden (`bot.py::_on_1m_close` ve
   `user_data_handler.py`'deki WS callback) `_exit_trade(self, sym, trade, exit_timestamp)`
   şeklinde çağrılıyor — `reason` ayrı bir parametre değil, çağıran taraf zaten
   `trade["result"]`'ı set ediyor. `ExitLifecycleService.execute()` gerçek imzayı
   (`execute(sym, trade, exit_timestamp) -> bool`) kullanıyor.

2. **Market close + doğrulama + repair, planın sandığından farklı olarak tek `if` bloğunun içinde.**
   `if cfg.BINANCE_API_KEY and not _exit_already_closed:` bloğu hem emir gönderimini hem
   pozisyon doğrulama loop'unu hem de REPAIR_REQUIRED dalını kapsıyor (satır 817-999,
   12-space indent — iç içe). Yani SL/TP ile zaten kapanmış ya da API key'siz (paper mode)
   durumlarda doğrulama adımı da tamamen atlanıp direkt commit'e geçiliyor. Bu, extraction'da
   `_submit_and_verify_market_close()` tek metoduna aynen taşındı.

3. **(Bulundu ve düzeltildi, plan'da hiç yoktu) Feature-flag / `@patch("bot.cfg", autospec=True)` çakışması.**
   Mevcut testlerin çoğu (`test_bot.py`) `@patch("bot.cfg", autospec=True)` ile TÜM config modülünü
   mock'luyor. Ayarlanmamış her attribute varsayılan olarak truthy bir `MagicMock` döner. Flag'i
   doğrudan `cfg.EXIT_LIFECYCLE_SERVICE_ENABLED` olarak okusaydık, flag'i hiç bilmeyen 3 eski test
   yanlışlıkla "servis path" dalına düşüp gerçek (mock'lanmamış) `config` modülünü kullanan servise
   girip regression'a yol açıyordu — bunu tam test suite'i koşarak yakaladım (30 baseline failure →
   33). Çözüm: `INITIAL_CAPITAL`/`RISK_PER_TRADE` ile aynı desen — flag `bot.py` modül seviyesinde
   kendi ismiyle (`EXIT_LIFECYCLE_SERVICE_ENABLED = cfg.EXIT_LIFECYCLE_SERVICE_ENABLED`) donduruluyor,
   böylece `@patch("bot.cfg", ...)` onu etkilemiyor. Düzeltmeden sonra tam suite tekrar **30/457** —
   sıfır regresyon.

## Kapsam (uygulanan)

**Yeni dosya:**
- `src/trading/exit_lifecycle.py` — `ExitLifecycleService` sınıfı

**Değişen dosyalar:**
- `src/bot.py` — import, `self.exit_service` DI kurulumu (`OrderManager`'dan hemen sonra),
  `EXIT_LIFECYCLE_SERVICE_ENABLED` modül sabiti, `_exit_trade` artık ince bir wrapper,
  eski gövde birebir korunarak `_exit_trade_legacy` adına taşındı (silinmedi — rollback yolu)
- `src/config.py` — `EXIT_LIFECYCLE_SERVICE_ENABLED` env flag (varsayılan `False`)
- `src/trading/__init__.py` — `ExitLifecycleService` export

**Dokunulmayan:** `order_manager.py`, `recovery_manager.py`, `user_data_handler.py`,
`bot_binance.py`, `models.py`, mevcut tüm test dosyaları.

Diff küçük ve tamamen ekleme şeklinde (`git diff --stat`): `bot.py +41`, `config.py +11`,
`trading/__init__.py +2`, hiçbir satır silinmedi/değiştirilmedi — mevcut davranış (flag=False
iken) `_exit_trade_legacy` üzerinden birebir korunuyor.

## `ExitLifecycleService` — gerçek metot sözleşmesi

Plan'ın önerdiği isim setine yakın ama gerçek kontrol akışına sadık, uyarlanmış hali:

```python
class ExitLifecycleService:
    async def execute(self, sym: str, trade, exit_timestamp: int) -> bool: ...
    async def _submit_and_verify_market_close(self, sym: str, trade) -> bool: ...
    async def _mark_repair_required(self, sym: str, trade) -> None: ...
    async def _commit_confirmed_exit(self, sym: str, trade, exit_timestamp: int) -> bool: ...
```

`execute()` dönüş sözleşmesi (orijinal `_exit_trade` hiçbir zaman bir şey döndürmüyordu ve
hiçbir çağıran yeri kullanmıyordu — bu servis katmanında YENİ, ama geriye dönük kırıcı değil):
- `True` → exit doğrulandı ve muhasebe (pnl/balance/peak) commit edildi
- `False` → bu turda commit edilmedi: WS-fallback stale event, ikinci-exit engeli,
  REPAIR_REQUIRED, ya da geçersiz fill verisi (`BROKEN_MANUAL_INTERVENTION_REQUIRED`).
  Akış bir sonraki 1m bar'da ya da WS event'inde tekrar denenebilir hale geliyor.

Constructor DI (bkz. `bot.py::__init__`, `OrderManager`'dan hemen sonra): `rest_client`,
`order_manager`, `active_trades`, `states`, `rsms`, `trades` (hepsi PaperTrader ile **paylaşılan
referans**, kopya değil), `pl_callback`, `risk_mgr`, `balance_getter`/`balance_setter`
(closure — `self._available_balance` plain float attribute olduğu için), `wallet_balance_getter`,
`output_dir`, `fvg_state_file`.

## Doğrulama (yerel ajanın testleri yazarken referans alabileceği manuel sonuçlar)

Test dosyalarına dokunmadım ama davranışı 3 şekilde manuel doğruladım (repo'da script olarak
bırakılmadı, sadece bu brief'te sonuçları var):

1. **Tam test suite, değişiklik öncesi/sonrası:** `30 failed, 457 passed` → `30 failed, 457 passed`,
   **aynı 30 test aynı sebeplerle** (hepsi patch 2 ile ilgisiz, önceden bozuk: stale imzalar,
   `test_entry_manager.py`/`test_session.py`/`test_snapshot.py` gibi tamamen alakasız modüller).
2. **Parity check (flag=True vs flag=False, aynı input):** short/SL exit senaryosunda
   `pnl`, `available_balance`, `trades` kaydı **birebir aynı** (`362.69` / `10362.69`).
   Tek fark `execute()`'un `True` dönmesi — `_exit_trade_legacy` zaten hep `None` dönüyordu
   (kullanılmıyordu), davranış değişikliği değil.
3. **Live-mode REPAIR_REQUIRED senaryosu (mock REST, flag=True):** boş adapter response →
   force-close denendi → pozisyon 5 denemede kapanmadı → `status=REPAIR_REQUIRED`, trade
   `active_trades`'te kaldı, `EXIT_UNCONFIRMED` critical log basıldı — mevcut
   `TestExitTradeAdapterAmbiguity::test_empty_response_no_commit`'in (legacy path'te zaten
   yeşil) beklediği davranışın birebir aynısı.

## Kabul kriterleri (kaynak kodu için — tamamlandı)

- [x] `pytest tests/` — flag=False (varsayılan) iken sıfır yeni regresyon (30/457, baseline ile aynı)
- [x] `ExitLifecycleService.execute()` paper-mode'da uçtan uca çalışıyor (manuel doğrulandı)
- [x] `ExitLifecycleService` live-mode REPAIR_REQUIRED dalı mock REST ile doğrulandı
- [x] Diff sadece ilan edilen 4 dosyada, `_exit_trade_legacy` orijinal koddan **0 fark**
- [x] Rollback tek satır: `EXIT_LIFECYCLE_SERVICE_ENABLED=False` (zaten varsayılan)

## Açık kalan noktalar (bu patch'te karar verilmedi)

1. **`COMMISSION_RATE` iki yerde tanımlı** (`bot.py` — legacy path için, `exit_lifecycle.py` —
   servis için), aynı değer (`0.0005`) ama tek kaynağa indirgenmedi. Legacy path kaldırıldığında
   (flag kalıcı `True` olup `_exit_trade_legacy` silindiğinde) `bot.py`'deki tanım da kaldırılıp
   `exit_lifecycle.py`'den import edilebilir.
2. **`ActiveTrade` hâlâ flat** — Patch 1 brief'in bıraktığı gibi. `TradeRuntimeState`/
   `TradeConfirmedState` bu patch'te de bağlanmadı; `ExitLifecycleService` mevcut `["alan"]`
   dict-erişimiyle çalışıyor. Bağlama kararı hâlâ Patch 3'e (protection lifecycle) ait.
3. **Feature flag ne zaman `True`'ya çevrilecek?** Test dosyaları yazılıp
   `tests/test_exit_lifecycle.py` yeşil olduktan ve `test_bot.py`'deki exit testleri
   `EXIT_LIFECYCLE_SERVICE_ENABLED=True` ile de koşulup doğrulandıktan sonra — bu brief'in
   kapsamı dışında, sıradaki adım.

## Yerel ajana verilecek prompt (SADECE testler)

> Kaynak kodu tamamlandı, dokunma: `src/trading/exit_lifecycle.py`, `src/bot.py`,
> `src/config.py`, `src/trading/__init__.py`.
>
> `tests/test_exit_lifecycle.py` (yeni dosya) yaz: `ExitLifecycleService`'i doğrudan (bot
> üzerinden değil) DI ile kurup test et — `execute()`'un WS-fallback guard, market-close
> ambiguity (`REQUEST_SENT`/`ORDER_ACKNOWLEDGED`/`REJECTED`/boş response), 5x doğrulama loop'u,
> `_mark_repair_required`, `_commit_confirmed_exit` (geçerli/geçersiz fill, invalid fill →
> `BROKEN_MANUAL_INTERVENTION_REQUIRED`) yollarını kapsadığından emin ol. `tests/test_bot.py`'deki
> mevcut `TestExitTrade*` sınıflarını (hepsi `_exit_trade_legacy`'yi hâlâ dolaylı test ediyor,
> isim değişmedi çünkü `_exit_trade` wrapper flag=False iken ona delege ediyor) referans stil
> olarak kullan.
>
> Ayrıca `test_bot.py`'ye `EXIT_LIFECYCLE_SERVICE_ENABLED=True` iken `_exit_trade`'in
> `self.exit_service.execute(...)`'a delege ettiğini doğrulayan birkaç ince "wiring" testi ekle
> (örn. `exit_service.execute`'u mock'layıp çağrıldığını doğrulamak yeterli — mantığı tekrar
> test etmeye gerek yok, o `test_exit_lifecycle.py`'nin işi).
>
> Bitirdiğinde: `pytest tests/test_exit_lifecycle.py -v` ve `pytest tests/ -q` çalıştır,
> yeni testler yeşil, mevcut 30 pre-existing failure dışında hiçbir şey kırılmamış olmalı.
