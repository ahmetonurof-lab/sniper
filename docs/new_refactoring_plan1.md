arar: İlk hedef kodu güzelleştirmek değil, canlıda Binance ile kavga eden state geçişlerini kontrol altına almak. Bu yüzden ilk patch seti exit/protection/websocket üçlüsünü ayıracak.



1. Genel Özet



Ben olsam bu refactor’ı tek büyük commit olarak yapmam. En doğru yol, birbirini kilitlemeyen 6 patch seti halinde ilerlemek. İlk iki patch seti tamamen hayati: state split ve exit lifecycle extraction. Üçüncü patch seti de senin dediğin gibi kritik: hangi fonksiyon nereye taşınacak konusu net olmazsa ekip yine bot.py içine geri kayar.



2. Dosya Bazlı Patch Listesi



Aşağıdaki sıra, uygulanabilir ve rollback-friendly sıradır.

Patch Set 1 — Veri modelini hazırla
Amaç: Yeni servisler gelmeden önce state taşıyıcılarını tanımla.



Değişecek dosyalar:

src/models.py
tests/test_models.py


Eklenmesi gerekenler:

TradeStatus
TradeConfirmedState
TradeRuntimeState
PendingExitContext
ProtectionRef
ProtectionState
NormalizedOrderEvent (istersen bunu user_data_handler.py tarafında da tutabilirsin ama ben modelde tutmayı tercih ederim)


Bu patchte yapılmayacaklar:

_exit_trade logic taşıma yok
order_manager logic değişimi yok
user_data_handler davranış değişimi yok


Amaç sadece şu: altyapıyı hazırlamak.



Patch Set 2 — Exit lifecycle extraction
Amaç: _exit_trade içindeki canlı riskin kalbini dışarı almak.



Yeni dosya:

src/trading/exit_lifecycle.py


Değişecek dosyalar:

src/bot.py
tests/test_bot.py
tests/test_integration.py
yeni: tests/test_exit_lifecycle.py


Bu patchte olacaklar:

_exit_trade içindeki şu mantıklar servisleşecek:

exit request hazırlama
market close submit
response ambiguity handling
verify-before-commit
confirmed close commit
fail → EXIT_VERIFYING / REPAIR_REQUIRED


bot.py’de ne kalacak?

sadece exit niyeti üretme
self.exit_service.execute(...) çağrısı
CLOSED ise finalize/persist


Patch Set 3 — Protection lifecycle extraction
Amaç: verify_protection, trailing replace semantiği, cleanup ve repair’i tek dilde toplamak.



Yeni dosya:

src/trading/protection_lifecycle.py


Değişecek dosyalar:

src/trading/order_manager.py
src/trading/recovery_manager.py
src/bot.py
tests/test_order_manager.py
tests/test_recovery_manager.py
yeni: tests/test_protection_lifecycle.py


Bu patchte olacaklar:

verify_protection() bool olmaktan çıkacak
current/pending/previous/history mantığı tek yapıya bağlanacak
cleanup_on_exit() yalnızca confirmed exit sonrası doğru davranacak
reconcile_orphan_orders() transition-aware olacak


Patch Set 4 — WS normalization
Amaç: websocket handler artık confirmed trade mutator olmasın.



Değişecek dosyalar:

src/trading/user_data_handler.py
src/models.py veya src/trading/events.py
tests/test_websocket.py
yeni: tests/test_user_data_handler.py


Bu patchte olacaklar:

on_order_update() normalize eder
trade bulursa runtime buffer’a event yazar
confirmed exit alanlarını direkt mutasyona uğratmaz
unmatched reduceOnly fill → incident/event olarak kaydedilir


Patch Set 5 — bot.py küçültme ve orchestration netleştirme
Amaç: bot.py karar verici kalsın, state sahibi olmasın.



Değişecek dosyalar:

src/bot.py
tests/test_bot.py


Bu patchte olacaklar:

_on_1m_close içindeki sıra netleşecek
symbol freeze kuralları sadeleşecek
trailing / exit / recovery servis çağrıları düzenlenecek


Patch Set 6 — Operatör görünürlüğü ve rollout guard
Değişecek dosyalar:

src/state_writer.py
src/config.py
src/event_log.py
tests/test_state_writer.py


Bu patchte olacaklar:

status
exit_unconfirmed
repair_required
frozen
sl_present
tp_present
incident standardization
feature flags


3. Hangi fonksiyon nereye taşınacak?



Burası kritik. Aşağıyı neredeyse taşıma haritası gibi kullanabilirsin.

src/bot.py
Kalacak fonksiyonlar:

__init__
_on_15m_close
_on_1m_close  → ama kısalmış hali
_try_entry → büyük ihtimalle şimdilik kalabilir
run
on_15m
on_1m
warmup / history / session yardımcıları


İnceltilmesi gereken fonksiyonlar:

_exit_trade → içi boşaltılacak; ya tamamen kaldırılacak ya da self.exit_service.execute(...) wrapper’ına dönecek


Yeni hali yaklaşık şöyle olmalı:



def _exit_trade(self, sym: str, reason: str, price: float | None = None):
    trade = self.active_trades.get(sym)
    if not trade:
        return False
    return self.exit_service.execute(trade=trade, reason=reason, now_ms=self._now_ms())


src/trading/exit_lifecycle.py
Buraya taşınacak mantıklar:

market close request hazırlama
adapter response yorumlama
REQUEST_SENT / ACKNOWLEDGED / VERIFYING / COMMITTED semantiği
invalid fill / empty response / delayed confirmation handling
confirmed close sonrası accounting tetikleme
confirmed close sonrası cleanup tetikleme


Önerilen fonksiyon seti:



class ExitLifecycleService:
    def execute(self, trade: ActiveTrade, reason: str, now_ms: int) -> bool: ...
    def _submit_market_close(self, trade: ActiveTrade) -> object | None: ...
    def _handle_ambiguous_submit(self, trade: ActiveTrade) -> None: ...
    def _verify_position_closed(self, trade: ActiveTrade) -> bool: ...
    def _extract_fill(self, response: object, trade: ActiveTrade) -> tuple[float, float]: ...
    def _commit_exit(self, trade: ActiveTrade, fill_price: float, fill_qty: float, reason: str, now_ms: int) -> None: ...
    def _mark_repair_required(self, trade: ActiveTrade, why: str) -> None: ...


Özellikle _exit_trade’den buraya taşınması gereken bloklar:

early active_trades.pop civarı mantık
early pnl/balance commit mantığı
failed close → trade’i active gibi geri koyan mantık
adapter ambiguity handling
verify-before-commit mantığı


src/trading/protection_lifecycle.py
Buraya taşınacak mantıklar:

verify_protection() iç mantığı
repair_protection() kararları
cleanup_on_exit() kararı
pending/current promotion
protection known ID aggregation


Önerilen fonksiyon seti:



class ProtectionLifecycleService:
    def verify(self, trade: ActiveTrade, open_order_ids: set[str]) -> ProtectionCheckResult: ...
    def known_ids(self, trade: ActiveTrade) -> set[str]: ...
    def begin_replace_sl(self, trade: ActiveTrade, new_id: str) -> None: ...
    def begin_replace_tp(self, trade: ActiveTrade, new_id: str) -> None: ...
    def promote_sl(self, trade: ActiveTrade) -> None: ...
    def promote_tp(self, trade: ActiveTrade) -> None: ...
    def cleanup_after_confirmed_exit(self, trade: ActiveTrade, result: str) -> None: ...
    def maybe_repair(self, trade: ActiveTrade) -> bool: ...
    def should_skip_reconcile(self, trade: ActiveTrade) -> bool: ...


Nereden taşınacak?

src/trading/order_manager.py
kısmen src/trading/recovery_manager.py
kısmen src/bot.py


src/trading/order_manager.py
Burada kalması gerekenler:

low-level order place/cancel çağrıları
exchange’e giden ince yardımcılar
pure order utility mantığı


Buradan çıkarılması gereken karar mantıkları:

“bu protection healthy mi?”
“şimdi cleanup nasıl davranmalı?”
“şu durumda repair tetiklemeli miyim?”
“pending/current promotion nasıl olmalı?”


Yani order_manager.py bir mechanics dosyası olsun; policy dosyası olmasın.



src/trading/recovery_manager.py
Burada kalması gerekenler:

orphan order scan akışı
position recovery akışı


Ama değişmesi gereken şey:

orphan kararı verirken ProtectionLifecycleService.known_ids(trade) kullansın
transition state’lerde skip etsin


Önerilen kullanım:



def reconcile_orphan_orders(...):
    for sym, trade in active_trades.items():
        if protection_service.should_skip_reconcile(trade):
            continue

        known_ids = protection_service.known_ids(trade)
        ...


src/trading/user_data_handler.py
Burada kalacak:

raw WS payload alma
normalize etme
trade bulma
event queue/buffer’a yazma


Buradan çıkarılacak:

exit_price
exit_actual_price
exit_actual_qty
exit_quote_qty
result = "WS_FALLBACK"
doğrudan confirmed exit alanlarına yazılan her şey


Yeni mantık:



def on_order_update(raw_event, trade_store, event_bus):
    evt = normalize_order_event(raw_event)
    trade = trade_store.find_by_order_reference(evt.symbol, evt.order_id, evt.client_order_id)

    if trade is None:
        event_bus.record_unmatched(evt)
        return

    trade.runtime.pending_events.append(evt)


src/state_writer.py
Buraya eklenecek alanlar:

status
exit_unconfirmed
repair_required
frozen
sl_present
tp_present


Burada yapılmayacaklar:

business logic yok
repair kararı yok
exit commit yok


src/bot_binance.py
Burada ne değişmeli?

Bu dosyayı büyük çapta bölmeye gerek olmayabilir. Ama response semantics netleşmeli.



Öneri: place_market_order() veya close helper’ı şu semantiklerden birini dönsün:



@dataclass
class OrderSubmitResult:
    request_sent: bool
    acknowledged: bool
    order_id: str = ""
    client_order_id: str = ""
    avg_price: float | None = None
    executed_qty: float | None = None
    raw: dict | None = None


Bu çok önemli çünkü None, {}, {"status": ...} gibi gevşek response yapıları üst katmanda yanlış yorumlanıyor olabilir.



4. İlk commit seti nasıl atılmalı?



Bunu da çok net vereyim:



Commit 1
models: add confirmed/runtime trade state and protection refs
Commit 2
execution: introduce exit lifecycle service without enabling it
Commit 3
bot: delegate exit flow to exit lifecycle service behind feature flag
Commit 4
execution: introduce protection lifecycle service and transition-aware protection checks
Commit 5
recovery: use protection known ids and skip reconcile during transitions
Commit 6
websocket: normalize order updates and buffer runtime events instead of mutating confirmed state
Commit 7
observability: expose exit/protection runtime state in state writer and incident logs


5. Riske göre öncelik sırası



Senin “2 ve 3 hayati” dediğin yere tamamen katılıyorum. Ben önceliği şöyle koyarım:



Exit lifecycle extraction
Fonksiyon taşıma haritasını net uygulama
Protection lifecycle extraction
WS normalization
Orphan transition guard
State writer visibility


Yani ilk üçü tamamlanmadan “refactor oldu” demem.



6. Bir mini örnek: bot.py sonrası hedef görünüm



class PaperTrader:
    def _on_1m_close(self, bar):
        for symbol, trade in list(self.active_trades.items()):
            if trade.runtime.frozen:
                continue

            self.trailing_service.maybe_update(symbol, trade, bar)

            exit_reason = self.signal_engine.get_exit_reason(symbol, trade, bar)
            if exit_reason:
                self.exit_service.execute(trade, exit_reason, now_ms=bar.ts)

            self.recovery_service.reconcile_if_stable(symbol, trade)

            if trade.runtime.status == TradeStatus.CLOSED:
                self._finalize_closed_trade(symbol, trade)


İşte ulaşmak istediğimiz sade görüntü bu.



7. Önerilen Adımlar (Checklist)

models.py için state split patch’ini çıkar.
_exit_trade içinden taşınacak blokları tek tek işaretleyip exit_lifecycle.py iskeletini kur.
order_manager.py içindeki policy kararlarını ayıklayıp protection_lifecycle.py içine taşı.
user_data_handler.py confirmed-state write noktalarını runtime event buffer’a çevir.
recovery_manager.py için transition-aware orphan guard ekle.


İstersen bir sonraki mesajda bunu daha da ileri taşıyıp sana literal patch manifest hazırlayayım:

models.py içine eklenecek sınıflar
bot.py içinde hangi bloklar silinecek / hangi çağrılar kalacak
order_manager.py içinde hangi fonksiyonlar küçülecek
yeni dosyaların tam skeleton’ı


Yani artık “plan” değil, neredeyse uygulanacak patch blueprint’i olur.
