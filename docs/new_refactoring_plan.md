Hazırladım. Aşağıdaki planı anahtar teslim refactor planı olarak düşünebilirsin. Bunu özellikle şu bağlamda kurdum: backtest tarafı deterministik ve temiz çalışıyor ama canlıda Binance akışı ack / fill / websocket gecikmesi / stale snapshot / cancel-replace geçişleri yüzünden botun state modelini bozuyor. Yani strateji kötü değil; execution state modeli canlı borsa gerçekliğini temsil etmekte zayıf.



Referans aldığım bağlamlar:

sniper repo
Sprint A / B planı
sniper şema
Ana karar: src/bot.py daha fazla büyümemeli. Doğru çözüm, canlı execution lifecycle’ını 2 yeni küçük servis ve 1 event normalizer katmanına ayırmak.



1. Genel Özet



Sorunun özü, botun live execution tarafında confirmed state ile runtime/pending state’i aynı obje üzerinde karıştırması. Backtest’te bu görünmez çünkü olaylar sıralı, temiz ve deterministik geliyor; Binance canlıda ise aynı trade için market close denemesi, trailing replacement, websocket fill, cancel event ve orphan scan birbirini kesebiliyor. Bu yüzden senin Sprint A fixleri yerinde, ama kalıcı çözüm için veri modelinin ve lifecycle akışının ayrılması şart. Benim önerim: mevcut mimariyi komple yıkmadan, 3 katmanlı minimum güçlendirme yapman: state split + exit lifecycle service + protection lifecycle service.



2. Botun gerçek problemi ne?

Backtest dünyası ile live dünya aynı state modeliyle temsil ediliyor
Backtest’te “order gönderildi → fill oldu → trade kapandı” tek akış gibi akar. Binance’te ise bunlar ayrı olaylardır. Kod bunları tek transaction gibi varsaydığı için canlıda state bozuluyor.
Trade objesi confirmed kayıt defteri gibi değil, aynı anda çalışma masası gibi kullanılıyor
Yani aynı obje hem muhasebe kaydı, hem pending exit alanı, hem temporary websocket fallback alanı, hem protection geçiş alanı olmuş. Bu da yanlış anda yanlış commit üretir.
Protection emirleri object lifecycle olarak modellenmemiş
SL/TP için current / pending / previous / history semantiği olmadan orphan cleanup ve repair güvenilir çalışmaz.


3. Onayladığım hedef mimari



Ben Sprint B’yi şu hale getirerek onaylıyorum:



Katman

Amaç

Karar

State modeli

Confirmed ve speculative state’i ayırmak

Şart

Exit lifecycle

Prepare → Execute → Verify → Commit

Şart

Protection lifecycle

SL/TP replace/repair/orphan güvenliği

Şart

WS normalization

WS handler state mutator değil event-source olsun

Şart

bot.py içine state machine gömme

Hızlı ama kötü çözüm

Önermiyorum



4. Anahtar teslim refactor planı



Aşağıdaki plan, uygulanabilir sıradadır.

Faz 0 — Mimari sınırı koy
Amaç: bot.py’yi daha fazla şişirmemek.



Kural:

src/bot.py sadece orchestration yapacak.
Trade state mutasyonu servislerde yapılacak.
user_data_handler.py doğrudan confirmed trade state yazmayacak.


Bu fazda yapılacak:

bot.py içine yeni iş mantığı eklemeyi durdur.
Yeni servis dosyalarının isimlerini sabitle.
models.py içinde yeni veri yapılarının yerini belirle.
Faz 1 — ActiveTrade’ı iki katmana ayır
Değişecek dosya: src/models.py



Yeni hedef yapı:

TradeConfirmedState
TradeRuntimeState
PendingExitContext
ProtectionRef
ProtectionState
ActiveTrade


Neden?

Çünkü entry_price, qty, confirmed exit price, realized pnl gibi alanlar ile pending ws fallback, pending replacement, ambiguous close gibi alanlar aynı güven seviyesinde tutulamaz.



Önerilen model:



from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class TradeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TRAIL_REPLACING = "TRAIL_REPLACING"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    EXIT_SUBMITTED = "EXIT_SUBMITTED"
    EXIT_VERIFYING = "EXIT_VERIFYING"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    CLOSED = "CLOSED"
    BROKEN_MANUAL_INTERVENTION_REQUIRED = "BROKEN_MANUAL_INTERVENTION_REQUIRED"

@dataclass
class TradeConfirmedState:
    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_timestamp: int
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    exit_price: Optional[float] = None
    exit_qty: Optional[float] = None
    exit_timestamp: Optional[int] = None
    result: Optional[str] = None
    realized_pnl: Optional[float] = None

@dataclass
class PendingExitContext:
    reason: str
    requested_qty: float
    requested_at: int
    client_order_id: str = ""
    exchange_order_id: str = ""
    ack_received: bool = False
    fill_price: Optional[float] = None
    fill_qty: Optional[float] = None
    fallback_source: Optional[str] = None

@dataclass
class ProtectionRef:
    current_id: str = ""
    pending_id: str = ""
    previous_id: str = ""
    history_ids: list[str] = field(default_factory=list)

    def all_ids(self) -> set[str]:
        return {
            oid for oid in [self.current_id, self.pending_id, self.previous_id, *self.history_ids] if oid
        }

@dataclass
class ProtectionState:
    sl: ProtectionRef = field(default_factory=ProtectionRef)
    tp: ProtectionRef = field(default_factory=ProtectionRef)

@dataclass
class TradeRuntimeState:
    status: TradeStatus = TradeStatus.ACTIVE
    exit_ctx: Optional[PendingExitContext] = None
    protection: ProtectionState = field(default_factory=ProtectionState)
    pending_events: list[dict] = field(default_factory=list)
    repair_required: bool = False
    exit_unconfirmed: bool = False
    frozen: bool = False

@dataclass
class ActiveTrade:
    confirmed: TradeConfirmedState
    runtime: TradeRuntimeState = field(default_factory=TradeRuntimeState)


Kritik not:

Eski kodda ActiveTrade bazen dict gibi, bazen dataclass gibi davranıyorsa bunu tamamen bitir. Bu hibrit kullanım canlıda bug üretir.

Faz 2 — Exit lifecycle’ı bot.py dışına çıkar
Yeni dosya: src/trading/exit_lifecycle.py



Sorumluluk:

exit niyeti almak
market close emri göndermek
ambiguity durumunu işaretlemek
borsa tarafını verify etmek
only-on-confirmed commit yapmak


bot.py içinden taşınacak mantık:

_exit_trade içindeki execution ve verification kısmı
erken active_trades.pop
erken muhasebe commit
invalid fill sonrası trade’i kaybetme davranışı


Servis API önerisi:



class ExitLifecycleService:
    def request_exit(self, trade: ActiveTrade, reason: str, now_ms: int) -> None: ...
    def submit_exit(self, trade: ActiveTrade) -> None: ...
    def verify_exit(self, trade: ActiveTrade) -> bool: ...
    def commit_exit(self, trade: ActiveTrade) -> None: ...
    def fail_to_repair_state(self, trade: ActiveTrade, why: str) -> None: ...


Çekirdek akış:



class ExitLifecycleService:
    def __init__(self, exchange, position_reader, order_manager, accounting, logger):
        self.exchange = exchange
        self.position_reader = position_reader
        self.order_manager = order_manager
        self.accounting = accounting
        self.logger = logger

    def execute(self, trade: ActiveTrade, reason: str, now_ms: int) -> bool:
        if trade.runtime.status in {
            TradeStatus.EXIT_REQUESTED,
            TradeStatus.EXIT_SUBMITTED,
            TradeStatus.EXIT_VERIFYING,
            TradeStatus.BROKEN_MANUAL_INTERVENTION_REQUIRED,
        }:
            return False

        trade.runtime.status = TradeStatus.EXIT_REQUESTED
        trade.runtime.exit_ctx = PendingExitContext(
            reason=reason,
            requested_qty=trade.confirmed.qty,
            requested_at=now_ms,
        )

        response = self.exchange.place_market_close(
            symbol=trade.confirmed.symbol,
            side="SELL" if trade.confirmed.side == "LONG" else "BUY",
            qty=trade.confirmed.qty,
        )

        if response and response.order_id:
            trade.runtime.exit_ctx.exchange_order_id = response.order_id
            trade.runtime.exit_ctx.ack_received = True
            trade.runtime.status = TradeStatus.EXIT_SUBMITTED
        else:
            trade.runtime.exit_unconfirmed = True
            trade.runtime.status = TradeStatus.EXIT_VERIFYING
            return False

        if self.position_reader.is_position_open(trade.confirmed.symbol):
            trade.runtime.exit_unconfirmed = True
            trade.runtime.status = TradeStatus.EXIT_VERIFYING
            return False

        fill_price = response.avg_price or response.price
        fill_qty = response.executed_qty or trade.confirmed.qty
        self._commit(trade, fill_price, fill_qty, reason, now_ms)
        return True

    def _commit(self, trade: ActiveTrade, fill_price: float, fill_qty: float, reason: str, now_ms: int) -> None:
        pnl = self.accounting.calculate_realized_pnl(
            side=trade.confirmed.side,
            entry_price=trade.confirmed.entry_price,
            exit_price=fill_price,
            qty=fill_qty,
        )

        trade.confirmed.exit_price = fill_price
        trade.confirmed.exit_qty = fill_qty
        trade.confirmed.exit_timestamp = now_ms
        trade.confirmed.result = reason
        trade.confirmed.realized_pnl = pnl

        self.order_manager.cleanup_after_confirmed_exit(trade, reason)
        trade.runtime.status = TradeStatus.CLOSED
        trade.runtime.exit_unconfirmed = False


Burada ana kural:

active_trades.pop() yalnızca CLOSED olduğunda.
PnL yalnızca _commit() içinde.
cleanup_on_exit() yalnızca confirmed close sonrası.
Faz 3 — Protection lifecycle’ı ayır
Yeni dosya: src/trading/protection_lifecycle.py



Bu servis neyi çözecek?

verify_protection() bool cehenneminden çıkacak
pending/current/previous/history yönetilecek
trailing replacement güvenli olacak
cleanup/orphan aynı protection semantiğini paylaşacak


Yeni dönüş tipi önerisi:



from dataclasses import dataclass
from enum import Enum

class ProtectionHealth(str, Enum):
    HEALTHY = "HEALTHY"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    NOT_REQUIRED = "NOT_REQUIRED"

@dataclass
class ProtectionCheckResult:
    sl: ProtectionHealth
    tp: ProtectionHealth

    @property
    def healthy(self) -> bool:
        return self.sl in {ProtectionHealth.HEALTHY, ProtectionHealth.NOT_REQUIRED} and \
               self.tp in {ProtectionHealth.HEALTHY, ProtectionHealth.NOT_REQUIRED}


Verify örneği:



class ProtectionLifecycleService:
    def verify(self, trade: ActiveTrade, open_order_ids: set[str]) -> ProtectionCheckResult:
        sl_expected = trade.confirmed.sl_price is not None
        tp_expected = trade.confirmed.tp_price is not None

        sl_ids = trade.runtime.protection.sl.all_ids()
        tp_ids = trade.runtime.protection.tp.all_ids()

        sl = ProtectionHealth.NOT_REQUIRED
        if sl_expected:
            if not sl_ids:
                sl = ProtectionHealth.MISSING
            elif sl_ids & open_order_ids:
                sl = ProtectionHealth.HEALTHY
            else:
                sl = ProtectionHealth.UNKNOWN

        tp = ProtectionHealth.NOT_REQUIRED
        if tp_expected:
            if not tp_ids:
                tp = ProtectionHealth.MISSING
            elif tp_ids & open_order_ids:
                tp = ProtectionHealth.HEALTHY
            else:
                tp = ProtectionHealth.UNKNOWN

        return ProtectionCheckResult(sl=sl, tp=tp)


Trailing replacement örneği:



def replace_stop_loss(self, trade: ActiveTrade, new_order_id: str) -> None:
    trade.runtime.status = TradeStatus.TRAIL_REPLACING
    trade.runtime.protection.sl.pending_id = new_order_id

def promote_stop_loss(self, trade: ActiveTrade) -> None:
    ref = trade.runtime.protection.sl
    if ref.current_id:
        ref.previous_id = ref.current_id
        if ref.current_id not in ref.history_ids:
            ref.history_ids.append(ref.current_id)
    ref.current_id = ref.pending_id
    ref.pending_id = ""
    trade.runtime.status = TradeStatus.ACTIVE
Faz 4 — WebSocket handler’ı mutatör olmaktan çıkar
Değişecek dosya: src/trading/user_data_handler.py



Bugünkü sorun:

WS event geldiği anda confirmed state’e exit_price, result, exit_qty gibi alanlar yazılıyorsa bu çok riskli. Çünkü bu event:

stale olabilir
geç gelebilir
unmatched reduceOnly olabilir
close attempt ile yarışıyor olabilir


Yeni görev tanımı:

on_order_update() yalnızca event’i normalize etsin ve ilgili trade’in runtime buffer’ına yazsın.



Normalize event modeli:



from dataclasses import dataclass
from typing import Optional

@dataclass
class NormalizedOrderEvent:
    symbol: str
    order_id: str
    client_order_id: str
    event_type: str
    status: str
    side: str
    reduce_only: bool
    filled_qty: float
    avg_price: float
    event_time: int


Handler mantığı:



def on_order_update(raw: dict, trade_store, event_router) -> None:
    evt = event_router.normalize(raw)
    trade = trade_store.find_by_order_reference(evt.symbol, evt.order_id, evt.client_order_id)

    if trade is None:
        event_router.record_unmatched(evt)
        return

    trade.runtime.pending_events.append({
        "type": evt.event_type,
        "status": evt.status,
        "order_id": evt.order_id,
        "filled_qty": evt.filled_qty,
        "avg_price": evt.avg_price,
        "event_time": evt.event_time,
    })


Yorum:

confirmed mutation burada değil, exit_lifecycle veya protection_lifecycle içinde yapılmalı.

Faz 5 — Orphan recovery transition-aware hale gelsin
Değişecek dosya: src/trading/recovery_manager.py



Kural:

reconcile_orphan_orders() yalnızca stable state’te agresif çalışsın.



Agresif çalışmaması gereken statüler:

TRAIL_REPLACING
EXIT_REQUESTED
EXIT_SUBMITTED
EXIT_VERIFYING
REPAIR_REQUIRED
BROKEN_MANUAL_INTERVENTION_REQUIRED


Önerilen guard:



TRANSITIONAL_STATUSES = {
    TradeStatus.TRAIL_REPLACING,
    TradeStatus.EXIT_REQUESTED,
    TradeStatus.EXIT_SUBMITTED,
    TradeStatus.EXIT_VERIFYING,
    TradeStatus.REPAIR_REQUIRED,
    TradeStatus.BROKEN_MANUAL_INTERVENTION_REQUIRED,
}

def should_skip_orphan_reconcile(trade: ActiveTrade) -> bool:
    return trade.runtime.status in TRANSITIONAL_STATUSES


Known IDs kaynağı tek yerden gelsin:



def known_protection_ids(trade: ActiveTrade) -> set[str]:
    return trade.runtime.protection.sl.all_ids() | trade.runtime.protection.tp.all_ids()
Faz 6 — bot.py sadece orchestration yapsın
Değişecek dosya: src/bot.py



Yeni rolü:

bar akışı al
signal engine’i çağır
lifecycle servislerini sırayla çalıştır
trade store üstünden active trade listesini yönet


Yani bot.py şuna yaklaşmalı:



class PaperTrader:
    def _on_1m_close(self, bar):
        for symbol, trade in list(self.active_trades.items()):
            if trade.runtime.frozen:
                continue

            if self.protection_service.should_manage_trailing(trade):
                self.trailing_service.maybe_update(symbol, trade, bar)

            exit_reason = self.signal_engine.get_exit_reason(symbol, trade, bar)
            if exit_reason:
                self.exit_service.execute(trade, reason=exit_reason, now_ms=bar.ts)

            self.recovery_service.reconcile_if_stable(symbol, trade)

            if trade.runtime.status == TradeStatus.CLOSED:
                self.finalize_closed_trade(symbol, trade)


Buradaki kritik kazanım:

bot.py kararları verir, ama “state nasıl değişir?” sorusunun sahibi olmaz.

Faz 7 — Accounting ve persistence ayrımı
Değişecek dosyalar:

src/state_writer.py
mümkünse yeni küçük dosya: src/accounting.py


Neden?

Live state output ile muhasebe commit aynı şey değil.



Kural:

state_writer.py sadece görünürlük sağlar.
realized pnl / balance / peak equity commit yalnızca confirmed close sonrası yapılır.


State writer’a eklenecek minimum alanlar:

status
exit_unconfirmed
repair_required
sl_present
tp_present
frozen
Faz 8 — Rollout planı
Önerilen sırayla çık:

Sadece yeni model alanlarını ekle, eski davranışı bozma.
state_writer görünürlüğünü artır.
protection_lifecycle.py’ı devreye al.
exit_lifecycle.py’ı feature flag ile devreye al.
user_data_handler.py’ı event normalizer moduna geçir.
Eski _exit_trade logic’ini küçült.
Dual logging ile 1-2 gün paper/live dry-run izle.
Sonra tam geçiş.


Feature flag örneği:



USE_NEW_EXIT_LIFECYCLE = True
USE_NEW_PROTECTION_LIFECYCLE = True
USE_WS_EVENT_BUFFER = True


5. Dosya dosya net görev listesi



Dosya

Ne yapılacak?

src/models.py

Confirmed/runtime ayrımı, status enum, protection refs, pending exit context

src/bot.py

İç logic azaltılacak, servis delegasyonu kalacak

src/trading/exit_lifecycle.py

Yeni dosya; exit prepare/execute/verify/commit

src/trading/protection_lifecycle.py

Yeni dosya; verify/repair/replace/promote/cleanup

src/trading/user_data_handler.py

Confirmed state mutator değil, normalized event producer

src/trading/recovery_manager.py

Transition-aware orphan reconcile

src/trading/order_manager.py

Low-level order placement/cancel yardımcıları; lifecycle kararı burada değil

src/state_writer.py

Operator visibility alanları

tests/

Yeni lifecycle testleri, ambiguous response testleri, stale WS testleri



6. Test planı — minimum geçmeden canlıya çıkma

Unit testler
{} adapter response → EXIT_VERIFYING, no commit
invalid fill → trade state kaybolmaz
unmatched reduceOnly fill → confirmed alanlar kirlenmez
trailing replacement sırasında pending_id korunur
previous/history/current id semantiği doğru tutulur
verify_protection() missing vs unknown ayrımını yapar
cleanup_after_confirmed_exit() yalnızca confirmed close sonrası çalışır
Integration testler
market close ack var, fill gecikmeli geliyor
close başarısız, position açık, protection repair gerekiyor
trailing replace sırasında eski SL fill geliyor
orphan sweep transition sırasında skip oluyor
cancel event exit verifying sırasında repair tetiklemiyor
Chaos / simulation testler
REST timeout + geç WS fill
WS delay + stale open orders snapshot
place success ama order id geç görünmesi
cancel success ama open orders snapshot eski dönmesi


7. Canlı/backtest uyumu için özel not



Senin istediğin şey çok doğru: “backtest ile Binance artık kavga etmesin.” Bunun pratik yolu şu:

Strateji katmanı hem backtest’te hem live’da aynı kalsın.
Execution adapter farklı olabilir.
Lifecycle engine iki modda da aynı interface’i kullansın.


Yani:

Backtest: fill anında gelir.
Live: fill gecikmeli/ambiguous gelir.
Ama üst katman aynı API ile konuşur.


Örnek interface:



class ExecutionGateway(Protocol):
    def place_market_close(self, symbol: str, side: str, qty: float): ...
    def cancel_order(self, symbol: str, order_id: str): ...
    def get_open_orders(self, symbol: str) -> list[dict]: ...
    def get_position(self, symbol: str) -> dict: ...


Backtest gateway de live gateway de bu interface’i uygularsa strateji/backtest ve canlı daha uyumlu olur.



8. En kritik tasarım kuralları

Commit öncesi muhasebe yok
WS event confirmed state’e doğrudan yazamaz
bot.py lifecycle sahibi olamaz; sadece orkestra şefi olur
Protection IDs tek yerde ve stateful tutulur
Transition state’lerde orphan cleanup agresif çalışamaz
Ambiguous close = closed değildir


9. Tek cümlelik yönetici kararı



Bu botun kalıcı çözümü, daha fazla if/else eklemek değil; bot.py’yi büyütmeden confirmed/runtime ayrımı olan, exit ve protection lifecycle’ı servisleştirilmiş, websocket’i normalize eden bir mimariye geçmektir.



İstersen bir sonraki mesajda bunu daha da ileri götürüp sana direkt şu formatta verebilirim:



gün gün uygulama planı (3-5 günlük sprint breakdown)
dosya bazlı patch listesi
hangi fonksiyon nereye taşınacak
ilk commit seti nasıl atılmalı


Bunu isterse neredeyse “uygulatılabilir teknik spec” formatında çıkarırım
