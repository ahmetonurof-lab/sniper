# Active Context — Sniper Bot

## Mevcut Durum (Temiz Başlangıç)

- **Bot çalışıyor mu?**: Testnet'te, canlı emir gönderimi aktif.
- **Testnet bakiyesi**: ~5,000 USDT
- **Sembol sayısı**: 28 (18 eski + 10 yeni: TIA/SEI/ONDO/PYTH/RENDER/ENA/STRK/GMX/DYDX/LDO)
- **Kaldıraç**: 5x
- **Strateji**: CBDR → Sweep → FVG Wick Rejection → Primary Entry → Trailing → Exit (V3 — retrade/LHR kaldırıldı)

## Kritik Yapılan Değişiklikler

| # | Değişiklik | Açıklama |
|---|-----------|----------|
| 1 | **Retrade/LHR tamamen silindi** | `RetradeEngine`, `_check_retrade()`, `execute_lhr_entry()`, `SYMBOL_RISK_MAP`, `is_retrade`, `save_retrade_arm`/`load_retrade_arm`/`clear_retrade_arm`, `rsms_retrade`, `retrade_engines` — tümü kaldırıldı. |
| 2 | **Sweep infinite loop fix** | `unmark_sweep_used()` silindi. `mark_sweep_consumed(level)` + `is_sweep_consumed(level)` level-based ID (ör: `bullish_1.2345`) ile eklendi. Token restart-proof JSON lock file. |
| 3 | **`_exit_trade()` rewrite** | Sıra: `cancel_all_open_orders()` → `reduceOnly=True` market → 5-attempt position verify loop → `mark_sweep_consumed()` + `rsm.reset()`. |
| 4 | **Double exit guard** | `_exit_trade()` başında `active_trades.pop(sym, None)` ile trade alınır, `None` dönerse erken return. `pop` çağrısı en üste taşındı — artık hem guard hem atomik silme. |
| 5 | **Orphan cleanup geniş** | `reconcile_orphan_orders()` tüm order türlerini temizler (LIMIT dahil). |
| 6 | **FVG trailing close teyidi** | `_fvg_close_confirmed()` — trailing sadece 15m close'u FVG içinde olan FVG'leri kullanır. |
| 7 | **Trail prev ID geçiş fix** | `update_trail_orders()` eski SL/TP id'sini `*_order_id_prev` olarak saklar, WS fill eşleşmesi hem güncel hem prev id'leri kontrol eder. CANCELED callback'te prev id'ler sessizce yok sayılır. — WS_FALLBACK sayısını azaltır. |
| 8 | **Backtest trailing → live bot port** | `analyzer_v3.py` trailing bloğu `_fvg_close_confirmed()` + ATR buffer + TRAIL_MIN_MOVE_MULT + break-even ile güncellendi. `coins_config.py`'a trailing sabitleri eklendi. |
| 9 | **Entry wick ratio guard kaldırıldı (sweep bar'da yanlıştı)** | `signal_engine.py`'daki sweep barı wick ratio guardı silindi. Doğru kontrol `fvg.py/_wick_ratio_ok()` ile FVG tespiti sırasında yapılıyor. `is_closed` close guard korundu. |
| 10 | **FVG marker fix** | `_save_fvg_state()` içinde `fvg_bar_index: max(0, current.index-3)` → `fvg.bar_index` (restart sonrası marker yanlış yere düşüyordu). |
| 11 | **BE chart bar index fix** | `TrailingManager.evaluate_break_even()`'de `"bar": current.index` → `"bar": bar_index_15m` (15m bar index'i ile skala uyumu). `bars_15m` BE öncesi çekildi, dublikat silindi. |
| 12 | **Sweep level ActiveTrade'de** | `models.ActiveTrade`'e `sweep_level: float\|None` field'ı eklendi, `_try_entry()`'de `sweep_level=ss.sweep_level` ile dolduruluyor. |
| 13 | **on_sweep_confirmed rewrite** | 3 değişiklik: (a) sweep invalidation gate — ters kırılırsa IDLE, (b) FVG yoksa reset yok — bekle, (c) unconditional reset kalktı — SWEEP_DETECTED'de kal. |
| 14 | **output/ gitignore** | `output/*` exception'lar kaldırıldı, tüm output dizini ignore. Mevcut dosyalar `git rm --cached` ile indexten çıkarıldı. |
| 15 | **Snapshot pad & fetch limit** | `_PAD_BARS=8→20`, `_FETCH_LIMIT=120→160` — daha geniş pencere. |
| 16 | **Legend konum fix** | `bottom:14px` → `top:54px` — chart altına düşmesin. |
| 17 | **Entry line canvas overlay'e taşındı** | `createPriceLine()` silindi, `rangedHLine()` ile SL/TP yanına eklendi — chart'a entegre. |
| 18 | **ActiveTrade cbdr_high/cbdr_low** | models.py'ye eklendi, `_try_entry()`'de `ss.cbdr_body_high/low` ile dolduruluyor. |
| 19 | **fvg = rsm.trigger_fvg taşındı** | `_try_entry()` sonundan en başa alındı. |
| 20 | **update_trail_orders signature değişikliği** | `new_sl`, `new_tp`, `new_trail_count` parametreleri eklendi. Paper modda da `trade["sl"]`/`trade["tp"]`/`trade["trailing_count"]` güncellenir. `apply_price_precision()` çağrısı fonksiyon içine alındı — caller'da tekrar yok. |
| 21 | **Trailing partial success fix** | `sl_ok or tp_ok` durumunda `trailing_count` güncellenir. Sadece ikisi de başarısız olursa `False` döner (eski: biri başarısız → hep `False`). Log'da artık `trade.get("sl")` kullanılıyor — key hatası yok. |
| 22 | **_exit_trade() active_trades.pop taşındı** | `pop(sym, None)` çağrısı fonksiyon sonundan (`_write_trade_jsonl` sonrası) başına alındı — çift exit'te ikinci çağrı trade bulunmadığı için hemen return eder. |
| 23 | **max_wick_ratio parametresi kaldırıldı** | `TrailingManager.evaluate_trail()` imzasından `max_wick_ratio: float = 1.0` silindi. `find_fvgs()` çağrısındaki `max_wick_ratio` kwarg da kaldırıldı — kullanılmıyordu. |
| 24 | **Wick ratio guard doğru katmana taşındı** | `signal_engine.py:100-115` sweep bar wick guardı kaldırıldı (yanlış bar). `bot.py` RSM init'e `max_wick_ratio=cfg.FVG_WICK_RATIO_MAX` (0.75) eklendi — artık `fvg.py/_wick_ratio_ok()` impulse mother barını kontrol eder, FVG tespiti sırasında. Trailing'deki `max_wick_ratio` önceki commit'te zaten silindi (23). |
| 28 | **ATR refactor (indicators.py)** | Sahte ATR (`max(range, close*0.0001)`) → gerçek Wilder's smoothing 14-periyot ATR (`_atr_state`, `_atr_prev_close`). `bot.py`: `_warmup_cbdr`, `_on_15m_close`, `_on_1m_close` 3 yerine entegre. `recovery_manager.py`'de de kullanılıyor. `__init__` sıralama bug'ı düzeltildi (`_atr_state` artık RecoveryManager'dan önce tanımlı). |
| 29 | **Dinamik FVG eşiği** | Statik `FVG_SIZE_MAP` (`$ değerleri`) → `FVG_MIN_SIZE_ATR_MULT × atr_val` (dinamik). Hem entry hem trailing aynı formül. MULT taraması (0.02-0.30, 195 run) → `FVG_MIN_SIZE_ATR_MULT = 0.06` seçildi (0.02-0.08 arası PnL farkı gürültü seviyesinde, 0.06 en sağlam/orta nokta). |
| 25 | **FVG bar index restart fix** | `snapshot.py:_resolve_fvg_bar_index()` öncelik sırası değiştirildi: fiyat bazlı arama (#1) artık bar offset formülünden (#2) ÖNCE gelir. Restart sonrası `bars_15m` indeksleri sıfırlandığında formül yanlış bar'ı işaret ediyordu (FVG ~81 seviyesi / indeks 8'de ~77-78 barı). `snapshot.py:166-195`. |
| 26 | **Chart FVG uyuşmazlık uyarısı** | `chart_template.html`'e JS tutarlılık kontrolü eklendi: FVG marker bar'ının high/low'u ile fvgTop/fvgBottom arasındaki mesafe bar range'inin 8 katını geçerse kırmızı uyarı bandı basar. |
| 27 | **console_reporter syntax fix** | `display_fvg_status()`'ta `TRIGGER_READY` bloğundaki iki `self.emit()` yanlış indentasyon seviyesindeydi (if dışında), `elif` yetim kalıp SyntaxError veriyordu. |
| 30 | **RiskManager + Erken London risk çarpanı** | `risk_manager.py` (filelock thread-safe). EL çarpanı 1.5x (02-08 UTC). Histeresizli devre kesici: DD≥%15 patla, DD≤%10 reset. Backtest: 13/13 coin EL avantajı doğrulandı. Config: `EARLY_LONDON_RISK_MULT=1.5`. |
| 31 | **Session Router (yeni modül)** | `session_router.py` — `get_cbdr_multiplier()`, `should_trade()`, `is_high_quality_fvg()`, `is_fvg_valid()`, `get_session_hours()`. Coin bazlı CBDR risk çarpanı + zehirli bölge filtresi + ATR-bazlı FVG kalite kontrolü. |
| 32 | **CBDR Risk Matrisi + 3 katmanlı risk** | `config.py`'de `CBDR_RISK_MATRIX` (13 coin × 6 bucket × 6 çarpan kademesi: 1.5x/1.2x/1.0x/0.8x/0.5x/0.0x). 3 katman: Zaman(EL) × Kurulum(CBDR bucket) × Portföy(devre kesici). |
| 33 | **Defense mode** | Devre kesici aktifken (DD > %15) EL ve Elite CBDR çarpanları iptal: `final = 1.0 × min(cbdr_mult, 1.0)`. Log'da `[DEFENSE]` etiketi. |
| 34 | **Coin bazlı SessionState + midnight crossover** | Her coin `CBDR_RISK_MATRIX['session']` üzerinden kendi optimal session saatlerini alır. Midnight crossover session_router'da handle edilir. |
| 35 | **NaN fix + BOT_SESSION sil + MIN_FVG_SIZE temizlik** | `BOT_SESSION` sabiti kaldırıldı (artık coin bazlı). `FVG_SIZE_MAP` kullanımdan kalktı (ATR-bazlı dinamik eşik). NaN koruması eklendi. |
| 36 | **Dinamik ATR bazlı FVG filtresi** | `is_high_quality_fvg()` — FVG/ATR oranı `MIN_REL_FVG_THRESHOLD=0.50` altındaki FVG'leri reddeder. Tüm checklist tamamlandı. |
| 37 | **P0-5: STRKUSDT -4005 max quantity kısır döngüsü fix** | `place_stop_order/place_tp_order`: `close_position=True` parametresi eklendi (qty'siz emir). `_parse_error_code()` ile -4005 ayrımı. `get_max_qty()` helper. `repair_protection()`: -4005'te closePosition→parçalı dene, diğer hatalarda fiyat-bazlı retry aynen kalır. `recover_positions()`: aynı yaklaşım. `place_market_order_priority()`: CB bypass'li acil kapanış. `place_force_close_order()`: CB bypass. `_emergency_post()`: CB'sız POST. Backoff: 3 başarısız denemeden sonra 5dk bekle + CRITICAL uyarı. |
| 38 | **P0-6: `place_market_order()` `{}` dönmeme sorunu** | `place_market_order()` ve `place_market_order_priority()` hard failure'da `{"_status":...}` yerine `{}` döner. Caller'daki `if not close_result:` artık çalışır, `place_force_close_order()` tetiklenir. |
| 37 | **P0-5: STRKUSDT -4005 max quantity kısır döngüsü fix** | `place_stop_order/place_tp_order`: `close_position=True` parametresi eklendi (qty'siz emir). `_parse_error_code()` ile -4005 ayrımı. `get_max_qty()` helper. `repair_protection()`: -4005'te closePosition→parçalı dene, diğer hatalarda fiyat-bazlı retry aynen kalır. `recover_positions()`: aynı yaklaşım. `place_market_order_priority()`: CB bypass'li acil kapanış. `place_force_close_order()`: CB bypass. `_emergency_post()`: CB'sız POST. Backoff: 3 başarısız denemeden sonra 5dk bekle + CRITICAL uyarı. Test: 76+37 yeni test, 620+ geçiyor. |
| 37 | **FVG expiry filter** | `GLOBAL_FVG_EXPIRY_BARS=45` — 45 bar'dan eski FVG'ler 'ölü' kabul edilir. `is_fvg_valid()` session_router'da. Entry öncesi uygulanır. |
| 38 | **Session assignment** | 13 coin 3 session: **DEFAULT** (8: ADA, AVAX, DOT, NEAR, SOL, XRP, ETH, SUI), **REAL_CBDR** (2: ATOM, BTC), **ASIA_RANGE** (3: APT, BNB, LINK). ETH/SUI DEFAULT'a atanarak geri eklendi. |
| 39 | **CBDR_RISK_MATRIX final** | 13 coin bucket eşikleri + çarpanları backtest verisiyle dolduruldu. Her bucket WR/BE+/PnL baz alındı. Zehirli bölgeler (mult=0.0) işaretlendi. |
| 40 | **bot.py _session_label ASIA fix — backtest uyumu** | `_session_label()` 22-02'yi "ASIA" olarak etiketleyip blokluyordu. Bu REAL_CBDR coin'lerde (19-01) 01:00-02:00 arası hatalı bloka sebep oluyordu. Kaldırıldı. Artık coin bazlı CBDR penceresi blokajı (`cbdr_locked`) backtest'le birebir aynı. |
| 41 | **ExitLifecycleService extraction (Patch Set 2)** | `_exit_trade()`'den `ExitLifecycleService` (557 satır) ayrı modül olarak çıkarıldı (`src/trading/exit_lifecycle.py`). `bot.py`'da `EXIT_LIFECYCLE_SERVICE_ENABLED = cfg.EXIT_LIFECYCLE_SERVICE_ENABLED` flag + DI `exit_service` ile `_exit_trade()` wrapper (flag→execute, flag→legacy). Rollback guard: flag module-level const olarak yakalandığı için `@patch("bot.cfg", autospec=True)` interference'ı yok. 24 unit test + 3 wiring test. |
| 42 | **_round_step floor division fix** | `_round_step()`'de `value // step` kayan nokta hatasıyla 1 step eksik hesaplıyordu (7275.8 // 0.1 = 72757 → 7275.7). `int(value / step)` ile düzeltildi. OPUSDT'de her market close 0.1 OP kalıntı bırakıyordu. |
| 43 | **P1: State split model tanımları (192b6b6)** | `models.py`: `TradeStatus` enum, `TradeRuntimeState`, `TradeConfirmedState`, `ProtectionRef`, `ProtectionSlot`, `ProtectionState`, `PendingExitContext`, `NormalizedOrderEvent`. Henüz `ActiveTrade`'e bağlanmadı — sadece tip tanımları. |
| 44 | **P3: Protection lifecycle extraction (3935a51)** | `protection_lifecycle.py` (+265 satır): `ProtectionLifecycleService` — policy kararları OrderManager/RecoveryManager'dan ayrıldı. `ProtectionCheckResult` (tuple yerine dataclass). `CleanupPlan`. Rollout: `PROTECTION_LIFECYCLE_SERVICE_ENABLED` (env, default False). OrderManager + RecoveryManager delegate calls. |
| 45 | **P4: WS normalization — pending writes (007983b)** | `user_data_handler.py` (+238 satır): WS FILLED/TRIGGERED event'i artık confirmed alanlara direkt yazılmaz. `pending_exit_price/qty/order_id/timestamp`'e yazılır → `_exit_trade()` veya `ExitLifecycleService` promote eder. `normalize_order_event()` pipeline. Rollout: `WS_EVENT_NORMALIZATION_ENABLED` (env, default False). |
| 46 | **P5: bot.py orchestration cleanup (29ffd98)** | `_on_1m_close` yeniden yapısı: orphan sweep artık status'ten **bağımsız** (her 5 bar'da çalışır), ATR hesaplama `if unrestricted` bloğu içine taşındı, UPNL+state writer **her bar'da** (frozen trade'lerde bile). |
| 47 | **P1-1: repair_protection stale SL fallback** | `order_manager.py:repair_protection()` — SL/TP basarisizsa (fiyat coktan gecti, immediately trigger) mevcut mark_price + risk_pts ile yeniden hesapla. `recover_positions()` ile ayni retry mantigi. Eski: `trade["sl"]` dogrudan kullanilip reddedilir, sessizce yutulurdu. |
| 48 | **P1-4: periodic orphan sweep** | `recovery_manager.py:periodic_check_loop()` — `reconcile_orphan_orders()` periyodik olarak da calistirilir. Portfolio flat iken `_on_1m_close` tetiklenmez, sayac durur, orphan sweep calismaz. Artik 60sn'de bir her sey calisiyor. |
| 49 | **P0-4: restart REPAIR_REQUIRED cleanup** | `bot.py:run()` — recover_positions sonrasi REPAIR_REQUIRED/EXIT_REQUESTED trade'leri kontrol et. SL/TP saglikliyse STATUS_ACTIVE'e dondur. Eksi: onceki session'dan kalan bozuk trade sonsuza kadar REPAIR_REQUIRED'da kilitli kaliyordu. |
| 47 | **P6: Operator visibility (6df2134)** | `state_writer.py`: Her trade için `frozen` (status not in UNRESTRICTED) + global `feature_flags` (3 rollout flag'ın JSON durumu). |
| 48 | **B1: ActiveTrade runtime bağlantısı (bd234d4)** | `models.py` (+36): `TradeRuntimeState` → `ActiveTrade.runtime` field. `__getitem__`/`__setitem__` 3 key'i runtime'a yönlendirir: `status`, `frozen`, `pending_events`. `__post_init__` flat→runtime sync. |
| 49 | **B2: ProtectionState → runtime.protection (f2f15f1)** | `models.py` (+101): 6 flat protection alanı (`sl_order_id`, `tp_order_id`, `*_prev`, `pending_*`) → `runtime.protection` object üzerinden okunur/yazılır. `_PROTECTION_MAP` yönlendirme dict'i. `ProtectionState._get_ref/_set_ref` + `known_ids()`. |
| 50 | **B3: ProtectionCheckResult tuple yerine (35ac290)** | `order_manager.py`: `verify_protection()` dönüş tipi `(bool,bool)` → `ProtectionCheckResult` (`sl_present`, `tp_present`, `sl_healthy`, `tp_healthy`, `needs_repair`, `detail`). `__iter__` backward compat. REST fallback path de aynı dataclass'ı döndürür. |
| 51 | **fix: HTFFVG bar_index (2e73ae3)** | `bot.py`: `current.index - tf.real_index` → `current.index - tf.bar_index`. FVG expiry kontrolünde yanlış index kullanılıyordu. |
| 52 | **D1: ProtectionState lifecycle status (dbdab53)** | `models.py` (+32) + `state_writer.py` (+4): `sl_status(sl_price)`, `tp_status(tp_price)` → "NOT_REQUIRED"/"ACTIVE_CONFIRMED"/"PENDING_CREATE"/"EXPECTED". `health` → "HEALTHY"/"DEGRADED"/"BROKEN". State writer'a `sl_status`, `tp_status`, `protection_health` alanları eklendi. |
| 53 | **C(53): Explicit lifecycle states (9d0e72b)** | 3 yeni status: `EXIT_REQUESTED` (trail/exit tespitinde), `EXIT_SUBMITTED` (market order öncesi), `CLOSED` (commit'te string'den enum'a). `update_trail_orders()` replace sırasında `TRAIL_REPLACING`, success'te `ACTIVE`. bot.py + exit_lifecycle.py aynı state machine. |
| 54 | **P0-1: false position closed fix (c11c785)** | `exit_lifecycle.py:_submit_and_verify_market_close()` — verify loop'da adapter belirsizken (REQUEST_SENT/ORDER_ACKNOWLEDGED) `for-else` (sembol listede yok) ilk denemede `pos_closed=True` veriyordu. Binance gecikmeli donebilir, ilk `get_positions()` bos donebilir. Artik: (1) `is_ambiguous` flag, (2) belirsiz durumda `for-else` sadece son denemede kabul, (3) `get_all_orders()` fallback ile FILLED reduceOnly/emir kontrolu. UNIUSDT restart dongusu kok nedeni. |
| 54 | **E(54): Chaos/edge-case tests (9d0e72b)** | 4 test: delayed fill (4. attempt), REST timeout → REPAIR_REQUIRED, force close fallback (market REJECTED), state transition doğrulama. |
| 55 | **fix: close 3 system review findings (594f6f3)** | Review bulguları kapatıldı — detaylar commit'te embedded. |
| 56 | **P0-3: repair_protection per-symbol asyncio.Lock** | `order_manager.py`: `import asyncio` eklendi, `__init__`'e `_repair_locks: dict[str, asyncio.Lock]` eklendi. `repair_protection()` wrapper + `_repair_protection_locked()` rename. Aynı sembol için eşzamanlı çağrılar `lock.locked()` ile tespit edilip sessizce atlanır. Farklı semboller bloklanmaz. Test: 3 concurrency test (`TestRepairProtectionConcurrency`). |
| 57 | **P2-4: self-exit race guard** | `user_data_handler.py`: unmatched-reduceOnly fill, trade EXIT_SUBMITTED/EXIT_VERIFYING durumundayken WS_FALLBACK'e çevriliyordu (market-close emri SL/TP ID setinde yer almaz). `_SELF_EXIT_IN_PROGRESS_STATUSES` guard eklendi — hem normalized hem legacy handler'da. Legacy docstring güncellendi. raise → log_event + log.critical'e çevrildi (ACTIVE senaryosunda). WSFallbackError import kaldırıldı. |
| 58 | **P2-5: update_trail_orders -4005 fallback + backoff** | `order_manager.py`: SL/TP placement -4005 hatasında closePosition → split_qty fallback eklendi (repair_protection ile aynı desen). `error_code` log_event'a eklendi. `_trail_failures` backoff mekanizması: 3 ardışık başarısızlığın ardından 5dk backoff + CRITICAL uyarı. 8 yeni test. |
| 59 | **P1-6: Entry sizing max_qty kontrolü yok (kök neden)** | `entry_manager.py:calculate_qty()` — Binance LOT_SIZE.maxQty kontrolü yok. Sadece buying power tavanı var. Risk formulü çıkış qty'si maxQty sınırını aşabilir → SL/TP -4005 döngüsü. P2-5 semptom tedavisi, bu kök neden. **DURUM: DÜZELTİLDİ** — `execute_live_entry()`'e clamp eklendi. |
| 60 | **P1-7: Harici kapanışlar (forensic)** | 2026-07-22 events logunda 13+ WS_FALLBACK çıkışı tespit edildi. Çoğu 1-10 saniyede kapandı. ADAUSDT: entry→9s后 WS_FALLBACK, SL algo ID vs normal order ID çelişkisi. Harici MARKET emri pozisyonu kapatmış. Olası neden: testnet tuhaflığı, çoklu instance, loglanmayan kod yolu. Forensic: Binance API'den `ylOu3i0T6KRNJfKMA3T18s` order detayı çekilmeli. |

## Aktif Kararlar

- **LEVERAGE=5**: 5x kaldıraç, margin = notional / 5.
- **RSM (RetraceStateMachine)**: IDLE → SWEEP_DETECTED → TRIGGER_READY. Sadece 3 state.
- **Max 1 trade/gün/sembol** (retrade kalktı).
- **CBDR penceresi içinde işlem yasak**: Backtest'le birebir aynı. CBDR body tracking penceresinde (DEFAULT 22-02, REAL_CBDR 19-01, ASIA_RANGE 01-05) trade alınmaz — sadece body tracking + bias üretimi. CBDR kilitlenince trade serbest. Eski `_session_label` "ASIA" blokajı kaldırıldı.
- **Erken London risk çarpanı (1.5x)**: 02-08 UTC'de pozisyon boyutu %50 artırılır.
- **CBDR bucket çarpanı**: 6 kademe (1.5x/1.2x/1.0x/0.8x/0.5x/0.0x). Coin bazlı, CBDR genişliğine göre.
- **3 katmanlı risk**: Zaman (EL 1.5x) × Kurulum (CBDR bucket) × Portföy (devre kesici). Defense mode'da EL ve Elite CBDR iptal.
- **Devre kesici**: DD ≥ %15 → defense mode (EL çarpanı kapanır, CBDR elite iptal). DD ≤ %10 → reset.
- **RiskManager**: `sniper/src/risk_manager.py`, filelock ile thread-safe, state `output/risk_state.json`.
- **Session Router**: `sniper/src/session_router.py` — coin bazlı CBDR çarpanı + zehirli bölge filtresi + FVG kalite/zaman aşımı kontrolü.
- **FVG expiry filter**: `GLOBAL_FVG_EXPIRY_BARS=45` — 45 bar'dan eski FVG'ler kullanılmaz.
- **Dinamik FVG filtresi**: `MIN_REL_FVG_THRESHOLD=0.50` — FVG/ATR oranı bu değerin altındaysa red.
- **Backtest doğrulaması**: 13/13 coin'de erken London WR > geç London/NY, tutarlılık %100. EL PF=4.35 vs non-EL PF=2.52. CBDR bucket matrisi backtest ile dolduruldu.
- **Explicit exit state machine**: `EXIT_REQUESTED` (trail/exit tespiti) → `EXIT_SUBMITTED` (market order gönderildi) → `EXIT_VERIFYING` (position verification) → `CLOSED` (commit). `update_trail_orders()` replace sırasında `TRAIL_REPLACING`, success'te `ACTIVE`.
- **Backtest metodu**: Parquet'ten linear PnL skalalama — exit koşulları price-based, qty skalası lineer taşınır. Gerçek portföy MaxDD günlük birleştirilmiş equity eğrisinden hesaplanır.
- **RISK_PER_TRADE=0.003**: Elle güncellendi (%0.3).
- **FVG_BUFFER_MULT=0.50**: Canlı ve backtest artık aynı.
- **MAX_SL_DIST_MULT=2.0**: FVG bazlı SL max `risk_pts × 2`.
- **CBDR gövde bazlı (open/close)**: High/low değil.
- **Backtest trailing live bot ile uyumlu**: `_fvg_close_confirmed()`, ATR buffer (`0.25×ATR`), `TRAIL_MIN_MOVE_MULT=0.2`, break-even (`1R` sonrası SL→entry).

## Sıradaki / Açık Konular

- Canlı testte `_exit_trade()` cancel_all + reduceOnly flow'un Binance ile çalışması gözlemlenecek.
- Backtest trailing port'u sonrası WR/DD değişimi canlı ile karşılaştırılacak.
- WS_FALLBACK sayısı trail prev ID fix sonrası takip edilecek.
- **FVG marker konum bug'ı** (chart'ta gördüğümüz, 3 örnek: SOLUSDT aynı gün) — kök neden araştırılıyor.
- **CBDR_RISK_MATRIX** canlı performansı gözlemlenecek — bucket çarpanlarının gerçek PnL'e uyumu kontrol edilecek.
- **Session assignment** sonrası DEFAULT/REAL_CBDR/ASIA_RANGE geçişlerinde FVG bulunamama sorunu tekrarlarsa analiz edilecek.
- **FVG_SIZE_MAP güncellemesi (2026-07-17):** Tüm 28 coin best session FVG Size değerleriyle yenilendi.
- **CBDR_RISK_MATRIX session sync (2026-07-17):** 9 coin'in session'ı best session analizine göre güncellendi. ATOM:ASIA→REAL, AVAX:REAL→DEFAULT, DOT:DEFAULT→REAL, INJ:ASIA→REAL, LINK:REAL→ASIA, NEAR:ASIA→REAL, OPUS:DEFAULT→REAL, UNI:DEFAULT→REAL, XRP:REAL→DEFAULT.
- **FVG_MIN_SIZE_ATR_MULT güncellendi (2026-07-15):** 0.08→0.06 (analyze_cbdr_thresholds.py ile aynı).
- **SYMBOLS listesi genişletildi (2026-07-15):** 10 yeni coin eklendi (toplam 28).
- **ict_cbdr_thresholds.md** — geçersiz (sahte ATR ile koşmuş), yeniden koşulacak (sırada bekliyor).
- **v3_window_comparison.md** — geçersiz çıktı, yeniden koşulacak (sırada bekliyor).
- **[FVG_SCAN] log formatı** — 16 haneli float basıyor, `.6f` ile sınırlanması istendi, teyit edilmedi.
- **Wiring test scope fix**: `test_flag_true_delegates_to_exit_service` ve `test_flag_false_calls_legacy`'de `with patch("bot.EXIT_LIFECYCLE_SERVICE_ENABLED", ...)` bloğu `_exit_trade` çağrısını kapsamıyordu — patch revert olup flag kayboluyordu. Tüm akış `with` içine alındı, 3/3 wiring testi geçiyor.
- Coin bazlı pencere kararı (real_cbdr/asia_range) — CBDR_RISK_MATRIX içinde session assignment çözüldü, artık v3_window_comparison.md'ye bağımlı değil.
- Dün gece FVG bulunamama şikayeti (23:00'a kadar hiçbir coinde FVG yok, 1-2 sweep) — MULT=0.06 + ATR-bazlı FVG filtresi sonrası düzelip düzelmediği kontrol edilecek.
- **Backtest altyapısı entegrasyonu**: 5 dosya (session.py, retrace_state.py, fvg.py, models.py, coins_config.py) silindi — artık `sniper/src`'ten import ediliyor. `SNIPER_OUTPUT_DIR` env var ile production output/ klasöründen izolasyon. Determinism doğrulandı (in-memory state sızıntısı yok). `mult_scan.py`'de checkpoint/resume mekanizması var.
- **Rollout flag takibi**: 3 rollout flag — `EXIT_LIFECYCLE_SERVICE_ENABLED` (default False), `PROTECTION_LIFECYCLE_SERVICE_ENABLED` (default False), `WS_EVENT_NORMALIZATION_ENABLED` (default False). Hepsi şu an **kapalı**. Feature_flags state_writer'da JSON'a yazılıyor (P6).
- **Backfill** — P1 modelleri henüz ActiveTrade'e bağlı değilken B1/B2 bağlanmıştı. `TradeConfirmedState` field'ları `ActiveTrade` flat alanlarına henüz bağlanmadı — hala kullanılmıyor.
- **Sprint C** `EXIT_REQUESTED` + `pending_exit_price` yazma mekanizması eklendi (bot.py:478, trail/exit tespitinde). Ancak `pending_exit_*` → confirmed promotion (`_close_trade_pending_exit()`) henüz implemente edilmedi — P4 WS normalization için gerekli.

## Hatırlatmalar

- sweep_direction mapping: yukarı sweep = bearish = SHORT, aşağı sweep = bullish = LONG.
- `mark_sweep_consumed()` level-based ID kullanır — bar_index değil.
- `rsm.reset()` artık `_exit_trade()` sonunda çağrılır, `_try_entry()` içinde değil.
- Trailing güncellemede eski order id `*_order_id_prev` olarak saklanır, geçiş penceresinde WS fill'leri prev id ile de eşleşebilir.
