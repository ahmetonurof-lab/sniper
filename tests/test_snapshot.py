from snapshot.snapshot import (
    _find_bar,
    _resolve_fvg_bar_index,
    _reverse_sort_key,
    normalize_trade,
)


def _candle(time, open_, high, low, close):
    return {"time": time, "open": open_, "high": high, "low": low, "close": close}


def _candles_16():
    """16 adet monoton artan fiyatlı mum. index 0-15."""
    return [
        _candle(1000000 + i * 900, 100 + i, 105 + i, 95 + i, 102 + i) for i in range(16)
    ]


def _candles_low_16():
    """16 adet FVG zonunun (106-108) ALTINDA kalan mum. index 0-15.

    Fiyat bazlı arama (Yöntem 1) bu dizide hiçbir mum bulamaz — böylece
    offset matematiği (Yöntem 2/3) ve sanity guard izole test edilir.
    """
    return [
        _candle(1000000 + i * 900, 60 + i, 65 + i, 55 + i, 62 + i) for i in range(16)
    ]


def _candles_pyth():
    """PYTHUSDT senaryosu: 30 mum, FVG zonu [0.04234, 0.0424], entry_bar=22.

    Zonla çakışan TEK mum index 25 — fiyatın zona ilk gerçek dönüşü entry'den
    SONRA olur (eski geriye-doğru arama bunu asla bulamıyordu).
    """
    candles = []
    for i in range(30):
        ts = 20000000 + i * 900
        if i == 25:
            candles.append(_candle(ts, 0.0445, 0.0450, 0.0422, 0.0430))
        elif i < 25:
            base = 0.0480 - i * 0.0001
            candles.append(
                _candle(ts, base, base + 0.0004, base - 0.0004, base + 0.0001)
            )
        else:
            base = 0.0440 + (i - 26) * 0.0002
            candles.append(
                _candle(ts, base, base + 0.0004, base - 0.0004, base + 0.0001)
            )
    return candles


class TestResolveFvgBarIndex:
    """_resolve_fvg_bar_index birim testleri."""

    def test_trigger_fvg_object_with_valid_entry_bar_index(self):
        """trigger_fvg objesi bar_index taşır, entry_bar_index geçerli >0.

        Mumlar zonla (106-108) çakışmadığından Yöntem 1 boş döner; offset
        matematiği devreye girer ve sanity guard sonucu makul bulup kabul eder.
        """
        candles = _candles_low_16()

        # entry_bar=10 (rel), fvg objesi bar_index=8, entry_bar_index=12 (abs)
        class FakeFVG:
            bar_index = 8

        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=10,
            fvg_obj=FakeFVG(),
            trade={"entry_bar_index": 12},
            fvg_top=108,
            fvg_bottom=106,
        )
        assert result == 6  # 10 + (8 - 12) = 6

    def test_trade_fvg_bar_index_with_valid_entry_bar_index(self):
        """trigger_fvg yok, trade dict fvg_bar_index taşır."""
        candles = _candles_low_16()
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=10,
            fvg_obj=None,
            trade={"fvg_bar_index": 130, "entry_bar_index": 135},
            fvg_top=108,
            fvg_bottom=106,
        )
        assert result == 5  # 10 + (130 - 135) = 5

    def test_restart_entry_bar_index_zero_uses_price_lookup(self):
        """entry_bar_index=0 (restart), fiyat bazlı bulma devreye girer."""
        candles = [
            _candle(1000000, 100, 102, 98, 101),  # high=102 < 106 → no match
            _candle(1000900, 101, 103, 99, 102),  # high=103 < 106 → no match
            _candle(1001800, 104, 105, 100, 103),  # high=105 < 106 → no match
            _candle(
                1002700, 107, 109, 102, 108
            ),  # high=109 >= 106, low=102 <= 108 → MATCH
        ]
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=3,
            fvg_obj=None,
            trade={"fvg_bar_index": 999, "entry_bar_index": 0},
            fvg_top=108,
            fvg_bottom=106,
        )
        assert result == 3

    def test_restart_no_overlap_falls_to_heuristic(self):
        """Fiyat bazlı bulma hiçbir mum bulamazsa heuristic'e düşer."""
        candles = [{"time": 1, "open": 50, "high": 55, "low": 45, "close": 52}]
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=1,
            fvg_obj=None,
            trade={},
            fvg_top=200,
            fvg_bottom=199,
        )
        assert result == 0  # max(0, 1 - 2) = 0

    def test_no_fvg_data_returns_none(self):
        """FVG verisi hiç yoksa marker atanmaz (None)."""
        candles = _candles_16()
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=10,
            fvg_obj=None,
            trade={},
            fvg_top=None,
            fvg_bottom=None,
        )
        assert result is None

    def test_entry_bar_less_than_2_returns_0(self):
        """entry_bar < 2 iken heuristic 0 döndürür."""
        candles = [{"time": 1, "open": 50, "high": 55, "low": 45, "close": 52}]
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=1,
            fvg_obj=None,
            trade={},
            fvg_top=108,
            fvg_bottom=106,
        )
        assert result == 0

    def test_all_none_returns_none(self):
        """Tümü None/hedefsiz → None (marker yok)."""
        candles = []
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=0,
            fvg_obj=None,
            trade={},
            fvg_top=None,
            fvg_bottom=None,
        )
        assert result is None

    def test_conversion_out_of_bounds_falls_to_price_lookup(self):
        """Absolute conversion bounds dışı kalırsa fiyat bazlı bulmaya düş."""
        candles = [
            _candle(1000000, 100, 102, 98, 101),  # high=102 < 106 → no match
            _candle(
                1000900, 130, 135, 107, 132
            ),  # high=135 >= 106, low=107 <= 108 → MATCH
            _candle(1001800, 104, 105, 100, 103),
        ]
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=1,
            fvg_obj=None,
            trade={"fvg_bar_index": 200, "entry_bar_index": 3},
            fvg_top=108,
            fvg_bottom=106,
        )
        # rel = 1 + (200 - 3) = 198 → out of bounds (len=3)
        # price lookup: candle[1] high=135 >= 106, low=107 <= 108 → return 1
        assert result == 1

    def test_pythusdt_price_returns_to_zone_after_entry(self):
        """PYTHUSDT: zona ilk gerçek dokunuş entry'den sonra (bar 25).

        Restart sonrası abs indeksler geçersiz — yeni arama TÜM candles'ta
        zonla çakışan barı bulur ve entry'ye en yakınını (25) döndürür.
        """
        candles = _candles_pyth()
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=22,
            fvg_obj=None,
            trade={"fvg_bar_index": 999, "entry_bar_index": 0},
            fvg_top=0.0424,
            fvg_bottom=0.04234,
        )
        assert result == 25
        # Dönen index GERÇEKTEN top/bottom ile çakışan bir mum olmalı
        c = candles[result]
        assert c["low"] <= 0.0424 and c["high"] >= 0.04234

    def test_price_match_prefers_nearest_to_entry(self):
        """Hem entry öncesi hem sonrası çakışma varsa entry'ye EN YAKIN seçilir."""
        candles = _candles_pyth()
        # bar 20'yi de zona çek — entry(22)'ye bar 25'ten daha yakın
        candles[20] = {**candles[20], "low": 0.0423}
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=22,
            fvg_obj=None,
            trade={"fvg_bar_index": 999, "entry_bar_index": 0},
            fvg_top=0.0424,
            fvg_bottom=0.04234,
        )
        assert result == 20

    def test_offset_math_sane_result_accepted_regression(self):
        """Restart yok, offset doğru ve sonuç makul — eski davranış korunur."""
        candles = _candles_low_16()
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=10,
            fvg_obj=None,
            trade={"fvg_bar_index": 132, "entry_bar_index": 135},
            fvg_top=108,
            fvg_bottom=106,
        )
        assert result == 7  # 10 + (132 - 135) = 7

    def test_offset_math_unreasonable_falls_to_heuristic(self):
        """Offset sonucu bounds içinde ama zonla alakasız → sanity guard reddeder.

        rel mumu FVG zonunun ortasından bar_range*8'den fazla uzaksa kabul
        edilmez ve heuristic'e (entry-2) düşülür.
        """
        candles = _candles_16()  # fiyatlar 95-120
        result = _resolve_fvg_bar_index(
            candles,
            entry_bar=10,
            fvg_obj=None,
            trade={"fvg_bar_index": 8, "entry_bar_index": 12},
            fvg_top=300,
            fvg_bottom=298,
        )
        # rel = 10 + (8 - 12) = 6; candle[6] high=111 low=101, zon ortası 299
        # dist = 188 > 10*8=80 → reddedilir → heuristic: 10 - 2 = 8
        assert result == 8


class TestReverseSortKey:
    """_reverse_sort_key (9-tümleyen) birim testleri."""

    def test_digit_complement(self):
        assert _reverse_sort_key("2026-08-09_172021") == "7973-91-90_827978"

    def test_non_digit_chars_preserved(self):
        assert _reverse_sort_key("a1-2_3") == "a8-7_6"

    def test_later_time_sorts_first(self):
        early = _reverse_sort_key("2026-07-21_003943")
        late = _reverse_sort_key("2026-08-09_172021")
        assert late < early

    def test_same_day_later_hour_sorts_first(self):
        early = _reverse_sort_key("2026-08-09_003943")
        late = _reverse_sort_key("2026-08-09_172021")
        assert late < early


class TestFindBar:
    """_find_bar: timestamp öncelikli, fiyat fallback."""

    def test_timestamp_picks_correct_bar_when_price_has_duplicates(self):
        """ONDOUSDT senaryosu: aynı fiyat (0.352) iki ayrı barda var.

        entry_timestamp YOKKEN (0) fiyat fallback'i İLK eşleşen barı seçer —
        grafikte 36 saat erken yanlış entry bar'ı (bug). entry_timestamp
        doluysa zaman eşleşmesi doğru barı seçer.
        """
        candles = [
            # 08-07 11:00 — eski bar, fiyat da burada var (yanlış aday)
            _candle(10000000, 0.352, 0.360, 0.348, 0.355),
            _candle(10000900, 0.356, 0.361, 0.349, 0.358),
            # 08-08 22:15 — gerçek entry bar'ı (ts 10_089_900 s, 900'a hizalı)
            _candle(10089900, 0.352, 0.359, 0.344, 0.348),
            _candle(10009800, 0.345, 0.350, 0.340, 0.347),
        ]
        entry_ts_ms = 10_089_900_000  # 10_089_900s barına düşer

        assert _find_bar(candles, 0.352) == 0  # fiyat fallback: eski bar (bug)
        assert _find_bar(candles, 0.352, entry_ts_ms) == 2  # zaman: gerçek bar

    def test_no_timestamp_falls_back_to_price(self):
        candles = [
            _candle(10000000, 100, 102, 98, 101),
            _candle(10000900, 101, 103, 99, 102),
        ]
        assert _find_bar(candles, 102.0) == 0
        assert _find_bar(candles, 102.5) == 1

    def test_timestamp_with_no_match_falls_back_to_price(self):
        """ts_ms liste dışı bir bara düşerse fiyat fallback devreye girer."""
        candles = [
            _candle(10000000, 100, 102, 98, 101),
            _candle(10000900, 101, 103, 99, 102),
        ]
        # ts 10_009_999_000 → bar_ts 10_009_800, listede yok
        assert _find_bar(candles, 102.0, 10_009_999_000) == 0

    def test_no_match_returns_last_bar(self):
        candles = [
            _candle(10000000, 100, 102, 98, 101),
            _candle(10000900, 101, 103, 99, 102),
        ]
        assert _find_bar(candles, 500.0) == 1


class TestNormalizeTrade:
    def test_entry_timestamp_passes_through(self):
        """normalize_trade dict(trade) yapıp geçer — yeni alan otomatik korunur."""
        trade = {
            "entry_price": 0.352,
            "sl": 0.3546,
            "tp": 0.3473,
            "entry_timestamp": 1786000000000,
        }
        n = normalize_trade(trade)
        assert n["entry_timestamp"] == 1786000000000

    def test_entry_timestamp_absent_stays_zero(self):
        trade = {"entry_price": 0.352, "sl": 0.3546, "tp": 0.3473}
        n = normalize_trade(trade)
        assert n.get("entry_timestamp", 0) == 0
