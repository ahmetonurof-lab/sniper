# Patch Set 1 — Veri Modeli: Yerel Ajan Görev Tanımı

Kaynak: `new_refactoring_plan1.md`, checklist madde 1 ("models.py için state split patch'ini çıkar").
Kontrol edilen repo: `ahmetonurof-lab/sniper`, commit `b1aa4d2a6f5a...` — `src/models.py`, `src/trading/user_data_handler.py`, `tests/test_models.py` okunarak hazırlandı.

## 🔴 Kritik bulgu — plan bir noktada güncellendi

`ActiveTrade` şu an **tamamen flat**: 40+ alan (`status`, `sl_order_id`, `exit_price`, `pending_exit_reason`, `exit_actual_qty`, ...) tek dataclass üzerinde duruyor, `status` bile plain `str`. `TradeConfirmedState`/`TradeRuntimeState` ayrımını bu patch'te doğrudan `ActiveTrade`'e bağlarsak (yani `trade.confirmed.x` / `trade.runtime.x` şekline geçersek), `bot.py` / `order_manager.py` / `user_data_handler.py`'a **hiç dokunmadan** prod'u kırarız — bu dosyalardaki onlarca çağrı noktası hâlâ `trade.status`, `trade.sl_order_id` gibi flat erişim bekliyor.

Bu yüzden **Patch 1'in tek güvenli hali**: yeni sınıfları `models.py`'de bağımsız / kablosuz olarak tanımlamak, `ActiveTrade`'e hiç dokunmamak. Bağlama (nesting) işi Patch 2/3'te gerçek servisler yazılırken, alan alan taşınarak olacak. Bu, planın kendi "yapılmayacaklar" listesiyle (`_exit_trade` taşınmayacak, `order_manager` değişmeyecek, `user_data_handler` davranışı değişmeyecek) zaten tutarlı — ben sadece bunu somutlaştırdım ve `ActiveTrade`'i de bu listeye ekledim.

Bonus bulgu: `INCIDENT_*` sabitleri (`INCIDENT_WS_UNMATCHED_REDUCE_ONLY` dahil) ve bir `Result[T]` generic sınıfı zaten mevcut — Patch 6 ve olası servis dönüş tipleri için hazır altyapı var, şimdilik dokunmuyoruz.

## Kapsam

**Değişecek dosyalar (sadece bunlar):**
- `src/models.py` — yeni sınıflar eklenir
- `tests/test_models.py` — yeni testler eklenir

**Dokunulmayacaklar:** `ActiveTrade` (tek satırı bile), `bot.py`, `order_manager.py`, `recovery_manager.py`, `user_data_handler.py`, `bot_binance.py`, `config.py`, `state_writer.py`.

## src/models.py — eklenecek kod

Eklenecek tek yeni import: `from enum import Enum` (`Literal`, `field`, `dataclass` zaten dosyada var). Bloğu, mevcut `STATUS_*` / `INCIDENT_*` sabitlerinden sonra, `ActiveTrade` sınıfından **önce** eklemeni öneririm — mantıksal komşuluk için.

```python
from enum import Enum

# ── Patch Set 1: yeni state taşıyıcıları (ActiveTrade'e HENÜZ bağlı değil) ──
# Bu sınıflar şimdilik bağımsızdır ve hiçbir yerde import edilip kullanılmaz.
# ActiveTrade'e "confirmed"/"runtime" olarak bağlanması Patch 2/3'te
# exit_lifecycle.py ve protection_lifecycle.py gerçek mantığı taşırken
# yapılacak. Bu patch'te ActiveTrade'e DOKUNULMAZ.


class TradeStatus(str, Enum):
    """Mevcut STATUS_* sabitleriyle birebir eşleşir + yeni CLOSED değeri."""

    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    TRAIL_REPLACING = "TRAIL_REPLACING"
    EXIT_VERIFYING = "EXIT_VERIFYING"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    BROKEN_MANUAL_INTERVENTION_REQUIRED = "BROKEN_MANUAL_INTERVENTION_REQUIRED"
    CLOSED = "CLOSED"  # yeni — bugün string tarafında karşılığı yok


class ProtectionSlot(str, Enum):
    CURRENT = "CURRENT"
    PENDING = "PENDING"
    PREVIOUS = "PREVIOUS"


@dataclass
class ProtectionRef:
    order_id: str
    kind: Literal["SL", "TP"]
    slot: ProtectionSlot
    created_ms: int | None = None


@dataclass
class ProtectionState:
    """Bugünkü sl_order_id / tp_order_id / *_prev / *_history alanlarının
    hedef (henüz bağlanmamış) karşılığı."""

    sl_current: ProtectionRef | None = None
    sl_pending: ProtectionRef | None = None
    sl_previous: ProtectionRef | None = None
    tp_current: ProtectionRef | None = None
    tp_pending: ProtectionRef | None = None
    tp_previous: ProtectionRef | None = None
    history: list[ProtectionRef] = field(default_factory=list)

    @property
    def sl_present(self) -> bool:
        return self.sl_current is not None

    @property
    def tp_present(self) -> bool:
        return self.tp_current is not None

    def known_ids(self) -> set[str]:
        """user_data_handler.py'daki mevcut oid-eşleştirme kontrolüyle
        (current + prev + history, SL+TP) birebir aynı küme."""
        refs = (
            self.sl_current, self.sl_pending, self.sl_previous,
            self.tp_current, self.tp_pending, self.tp_previous,
        )
        ids = {r.order_id for r in refs if r is not None}
        ids.update(r.order_id for r in self.history)
        return ids


@dataclass
class PendingExitContext:
    """Bugünkü pending_exit_reason / price / qty / order_id / timestamp
    alanlarının hedef karşılığı. Patch 2'de exit_lifecycle.py'nin
    REQUEST_SENT/ACKNOWLEDGED/VERIFYING adımlarını ayrıca takip etmesi
    gerekirse buraya alan eklenecek — o karar bu patch'in kapsamı dışında."""

    reason: str | None = None
    price: float | None = None
    qty: float | None = None
    order_id: str | None = None
    timestamp_ms: int | None = None


@dataclass
class TradeConfirmedState:
    """Entry/exit'in exchange tarafından doğrulanmış hali.
    sl/tp/trailing seviyelerinin confirmed'e mi runtime'a mı ait olduğu
    henüz karara bağlanmadı (bkz. aşağıdaki 'Açık kalan noktalar')."""

    symbol: str
    side: Literal["long", "short"]
    entry_price: float
    entry_qty: float
    entry_order_id: str = ""
    exit_price: float | None = None
    exit_qty: float | None = None
    exit_quote_qty: float | None = None
    exit_order_id: str = ""
    exit_timestamp_ms: int | None = None
    result: str | None = None  # "SL" | "TP" | "WS_FALLBACK" | ...


@dataclass
class TradeRuntimeState:
    status: TradeStatus = TradeStatus.ACTIVE
    frozen: bool = False
    pending_exit: PendingExitContext | None = None
    protection: ProtectionState = field(default_factory=ProtectionState)
    pending_events: list["NormalizedOrderEvent"] = field(default_factory=list)


@dataclass
class NormalizedOrderEvent:
    """on_order_update()'in bugün doğrudan okuduğu ham Binance alanlarının
    (s/X/c/i/R/ap/L/z/Z) normalize edilmiş hedef karşılığı. Bu patch sadece
    şekli tanımlar; gerçek normalize_order_event() fonksiyonu Patch 4'te
    user_data_handler.py değişirken yazılacak."""

    symbol: str            # ham alan: s
    order_id: str           # ham alan: i
    client_order_id: str    # ham alan: c
    status: str              # ham alan: X (FILLED/TRIGGERED/CANCELED/EXPIRED/...)
    reduce_only: bool = False        # ham alan: R veya reduceOnly
    avg_price: float | None = None   # ham alan: ap
    last_price: float | None = None  # ham alan: L
    cum_qty: float | None = None       # ham alan: z
    cum_quote_qty: float | None = None  # ham alan: Z
    ts_ms: int | None = None
    raw: dict | None = None

    @property
    def fill_price(self) -> float:
        """Bugünkü `ap if ap > 0 else L` kuralıyla birebir aynı."""
        if self.avg_price and self.avg_price > 0:
            return self.avg_price
        return self.last_price or 0.0
```

## tests/test_models.py — eklenecek testler

Mevcut dosya class-based (`TestBar`, `TestFVG`, `TestCHoCH`, ...) + düz `assert` stilini kullanıyor; aşağıdakiler aynı stilde. `models` import satırına eklenecekler: `TradeStatus, ProtectionSlot, ProtectionRef, ProtectionState, PendingExitContext, TradeConfirmedState, TradeRuntimeState, NormalizedOrderEvent, STATUS_ACTIVE, STATUS_PENDING, STATUS_TRAIL_REPLACING, STATUS_EXIT_VERIFYING, STATUS_REPAIR_REQUIRED, STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED`.

```python
class TestTradeStatus:
    def test_matches_legacy_string_constants(self):
        assert TradeStatus.ACTIVE == STATUS_ACTIVE
        assert TradeStatus.PENDING == STATUS_PENDING
        assert TradeStatus.TRAIL_REPLACING == STATUS_TRAIL_REPLACING
        assert TradeStatus.EXIT_VERIFYING == STATUS_EXIT_VERIFYING
        assert TradeStatus.REPAIR_REQUIRED == STATUS_REPAIR_REQUIRED
        assert (
            TradeStatus.BROKEN_MANUAL_INTERVENTION_REQUIRED
            == STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED
        )

    def test_closed_is_new_value(self):
        assert TradeStatus.CLOSED.value == "CLOSED"


class TestProtectionState:
    def test_defaults_are_empty(self):
        state = ProtectionState()
        assert state.sl_present is False
        assert state.tp_present is False
        assert state.known_ids() == set()

    def test_sl_present_when_current_set(self):
        ref = ProtectionRef(order_id="1", kind="SL", slot=ProtectionSlot.CURRENT)
        state = ProtectionState(sl_current=ref)
        assert state.sl_present is True
        assert state.tp_present is False

    def test_known_ids_covers_current_pending_previous_and_history(self):
        state = ProtectionState(
            sl_current=ProtectionRef(order_id="1", kind="SL", slot=ProtectionSlot.CURRENT),
            sl_previous=ProtectionRef(order_id="2", kind="SL", slot=ProtectionSlot.PREVIOUS),
            tp_pending=ProtectionRef(order_id="3", kind="TP", slot=ProtectionSlot.PENDING),
            history=[ProtectionRef(order_id="0", kind="SL", slot=ProtectionSlot.PREVIOUS)],
        )
        assert state.known_ids() == {"0", "1", "2", "3"}


class TestPendingExitContext:
    def test_defaults_are_none(self):
        ctx = PendingExitContext()
        assert ctx.reason is None
        assert ctx.price is None
        assert ctx.order_id is None


class TestTradeConfirmedState:
    def test_required_fields(self):
        c = TradeConfirmedState(
            symbol="BTCUSDT", side="long", entry_price=100.0, entry_qty=0.01
        )
        assert c.exit_price is None
        assert c.result is None


class TestTradeRuntimeState:
    def test_defaults(self):
        rt = TradeRuntimeState()
        assert rt.status == TradeStatus.ACTIVE
        assert rt.frozen is False
        assert rt.pending_exit is None
        assert rt.pending_events == []
        assert rt.protection.sl_present is False


class TestNormalizedOrderEvent:
    def test_optional_fields_default_safely(self):
        evt = NormalizedOrderEvent(
            symbol="BTCUSDT", order_id="1", client_order_id="c1", status="FILLED"
        )
        assert evt.reduce_only is False
        assert evt.fill_price == 0.0

    def test_fill_price_prefers_avg_price(self):
        evt = NormalizedOrderEvent(
            symbol="BTCUSDT", order_id="1", client_order_id="c1", status="FILLED",
            avg_price=100.0, last_price=99.0,
        )
        assert evt.fill_price == 100.0

    def test_fill_price_falls_back_to_last_price(self):
        evt = NormalizedOrderEvent(
            symbol="BTCUSDT", order_id="1", client_order_id="c1", status="FILLED",
            avg_price=0.0, last_price=99.0,
        )
        assert evt.fill_price == 99.0
```

## Kabul kriterleri

- [ ] Yukarıdaki tüm yeni testler yeşil (`pytest tests/test_models.py -v`)
- [ ] `pytest tests/` (tüm suite) — hiçbir mevcut test kırılmadı
- [ ] Diff'te sadece `src/models.py` ve `tests/test_models.py` var
- [ ] `ActiveTrade` sınıfının satırlarında 0 fark
- [ ] `src/models.py`'a eklenen tek yeni import: `from enum import Enum`
- [ ] Yeni sınıflardan hiçbiri şu an başka hiçbir dosyada import/kullanılmıyor (bilerek — bağlama işi sonraki patch'lerde)

## Açık kalan noktalar (bu patch'te karar verilmedi)

1. **sl/tp fiyat seviyeleri confirmed'de mi runtime'da mı?** Bugün `ActiveTrade.sl` / `.tp` trailing ile değişen canlı değerler. `TradeConfirmedState`'e mi (exchange'e gönderilen son onaylı seviye) yoksa `ProtectionState`'e mi ait olmalı — Patch 3'te netleşecek.
2. **PendingExitContext'in ince taneli adımları.** Bugün böyle bir REQUEST_SENT/ACKNOWLEDGED/VERIFYING ayrımı yok, sadece `pending_exit_*` alanları var. Patch 2'de `exit_lifecycle.py` ihtiyaç duyarsa alan eklenecek.
3. **order_id vs client_order_id çakışması.** `on_order_update()` bugün `oid = c or i` diyerek ikisini tek değişkende topluyor. `NormalizedOrderEvent` ikisini ayrı tutuyor (daha temiz) — Patch 4'te gerçek eşleştirme mantığı yazılırken bu ayrımın etkisi düşünülmeli.
4. **`status=""` (boş string) varsayılanı.** `UNRESTRICTED_STATUSES` bugün `{STATUS_ACTIVE, ""}`'i eşit sayıyor. `TradeRuntimeState.status` varsayılanını doğrudan `ACTIVE` yaptım — "" ayrı bir anlamlı durum değil, tarihi bir gevşeklik gibi görünüyor ama teyit gerekir.

## Yerel ajana verilecek prompt

> `src/models.py` dosyasına şu yeni sınıfları ekle: `TradeStatus` (Enum), `ProtectionSlot` (Enum), `ProtectionRef`, `ProtectionState`, `PendingExitContext`, `TradeConfirmedState`, `TradeRuntimeState`, `NormalizedOrderEvent` (tam kod ekte, `STATUS_*` sabitlerinden sonra `ActiveTrade`'den önce eklenecek). Tek yeni import: `from enum import Enum`.
>
> KURAL: `ActiveTrade` sınıfına DOKUNMA — hiçbir alanını silme, yeniden adlandırma, taşıma. Yeni sınıflar şimdilik bağımsız/kullanılmayan sınıflar olarak eklenir. `bot.py`, `order_manager.py`, `recovery_manager.py`, `user_data_handler.py`, `bot_binance.py`, `config.py` dosyalarının HİÇBİRİNE dokunma.
>
> `tests/test_models.py` dosyasına ekteki testleri, dosyadaki mevcut `TestBar`/`TestFVG` stiliyle birebir tutarlı class-based pytest formatında ekle.
>
> Bitirdiğinde: `pytest tests/test_models.py -v` çalıştır, hepsi yeşil olmalı. Sonra `pytest tests/` (tüm suite) çalıştır, hiçbir mevcut testin kırılmadığını doğrula. Diff'te sadece `src/models.py` ve `tests/test_models.py` görünmeli.
