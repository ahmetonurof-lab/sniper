# Parity CI Regression Test

## Amaç

Canlı ve backtest giriş state-machine akışının aynı 15m input barında aynı kararı üretmesini CI'da zorunlu kılmak.

Bu test execution riskini ölçmez. Emir gecikmesi, slippage, likidite ve exchange reddi ayrı bir execution-simulation katmanıdır.

## Sabit benchmark sözleşmesi

Kaynak benchmark: aynı 87.600 bar, aynı input ve aynı ortak `session.py`, `retrace_state.py`, `fvg.py`.

Beklenen sonuç:

| Sembol | Session | Core diff | Trigger BT/Live | Sweep-lock BT/Live |
|---|---:|---:|---:|---:|
| SOLUSDT | 19-1 | 0 | 10751 / 10751 | 32697 / 32697 |
| BNBUSDT | 19-1 | 0 | 11198 / 11198 | 32974 / 32974 |
| AVAXUSDT | 22-2 | 0 | 13748 / 13748 | 41615 / 41615 |
| LINKUSDT | 1-5 | 0 | 13761 / 13761 | 43040 / 43040 |
| XRPUSDT | 22-2 | 0 | 14122 / 14122 | 41130 / 41130 |
| ATOMUSDT | 19-1 | 0 | 12317 / 12317 | 34446 / 34446 |
| ADAUSDT | 22-2 | 0 | 14839 / 14839 | 43083 / 43083 |
| APTUSDT | 19-1 | 0 | 12080 / 12080 | 33399 / 33399 |
| DOTUSDT | 19-1 | 0 | 11976 / 11976 | 34100 / 34100 |

### Fixture checksum'ları (input veri sabitleme)

Test, her sembolün `*_1m_raw.feather` SHA256 checksum'ını bu değerlerle doğrular.
Input veri değişirse test bilinçli olarak güncellenmeli, sessizce kabul edilmemeli.

| Sembol | SHA256 |
|---|---|
| SOLUSDT | `19bac676cce10e9985c3db03d4b0f40c921b5faff455a24b87e95f3793cd7238` |
| BNBUSDT | `7ecc021ab335f3bdf07e24812066897e0a217e72053357b014d98470a6ecd2ec` |
| AVAXUSDT | `ca6bb170fc09033d625b40e71807502b60efc2a8a268278ac7339ef4dd1b69c0` |
| LINKUSDT | `266800e8198ad59bd343a2cdc05573fa4b6decb8b5d7cd8b633f72f405605571` |
| XRPUSDT | `0a6029cf1e79777b9b5a2dfeea9c77610996eba93df42e945ca58fa2ff5e9ec8` |
| ATOMUSDT | `323187d0d1dbf56ef487c09b873e72cb69bdbab1853aba32eabfddc659d68217` |
| ADAUSDT | `bef33eb05aacbff22b31dc68c5f1a3de5eb96326e44ec75f0b40ac517405cf90` |
| APTUSDT | `5a374480f985ea2ed9790f64e767e761f8f0c2a1837376571ee28038554427ce` |
| DOTUSDT | `4108eb14f5572b4cd617eabbb80fd4fbeada9141e4f03fed31559515d2b63c80` |

## CI assertions

Her sembol için CI şu koşulları assert etmeli:

```python
assert result.core_diff == 0
assert result.trigger_count_backtest == result.trigger_count_live
assert result.sweep_lock_count_backtest == result.sweep_lock_count_live
assert result.state_trace_backtest == result.state_trace_live
```

Trace karşılaştırması sadece toplam sayıyı değil, her bar için şunları karşılaştırmalı:

```text
bar_index
cbdr_locked
sweep_confirmed
sweep_direction
rsm_state
rsm_direction
trigger_fvg.bar_index   (HTFFVG; spec'te real_index olarak geçen alan)
trigger_decision
entry_gate_decision
```

## State transition contract

Sıra değiştirilemez:

```text
CBDR update
  -> CBDR lock
  -> sweep_confirmed
  -> IDLE + sweep_confirmed ise on_sweep
  -> SWEEP_DETECTED ise on_sweep_confirmed
  -> TRIGGER_READY
  -> yeni CBDR günü ve kilitsiz state ise bias_reject/reset
  -> cbdr_locked değilse entry yok
  -> session/filter kontrolleri
  -> entry
```

Özellikle şu davranışlar regression test olmalı:

1. Sweep yokken `on_sweep()` çağrılmaz.
2. `SWEEP_DETECTED` state'i her 15m barında invalidation kontrolünden geçer.
3. `TRIGGER_READY`, yeni CBDR gününe taşınmaz; `daily_bias == NEUTRAL` ise resetlenir.
4. CBDR kilitli değilken entry açılmaz.
5. Coin bazlı session penceresi, backtest ve canlıda aynı değerlendirilir.
6. `fvg_close_confirmed()` entry parity testine dahil edilmez; mevcut strateji sözleşmesinde devre dışıdır.

## Test isolation

Bu CI testi sadece giriş/state parity içindir. Aşağıdakileri bu teste eklemeyin:

- gerçek Binance API
- canlı bakiye
- slippage
- order retry
- 1m exit
- trailing execution
- network timing

Bunlar ayrı `execution_regression` veya `execution_simulation` test grubunda çalışmalı.

## Çalıştırma

```bash
python -m pytest tests/parity -q --maxfail=1
```

`PARITY_SKIP_CHECKSUM=1` ortam değişkeni checksum doğrulamasını atlar (ör. CI dışı hızlı koşular için).

## CI önerisi

```yaml
name: parity-regression

on:
  push:
  pull_request:

jobs:
  parity:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: pytest -q tests/parity --maxfail=1
```

Benchmark fixture'ı checksum ile sabitleyin. Input data, ortak modül commit SHA'ları veya session/FVG kuralları değişirse test bilinçli olarak güncellenmeli, sessizce kabul edilmemeli.

## Fail output

CI failure şu formatta ilk ayrışan barı göstermeli:

```text
symbol=SOLUSDT
bar=12345
timestamp=...
backtest=SWEEP_DETECTED, bullish, fvg=11800
live=IDLE, None, fvg=None
first_divergence=on_sweep_confirmed
```

Toplam sayaç tek başına yetmez. İlk ayrışan bar kök neden analizinin ana girdisidir.

## Kayıt bilgisi

- Tarih: 2026-07-31
- Kaynak: Canlı/backtest parity çalışması — 9 aktif sembolde core-diff=0, TRIGGER ve sweep-lock birebir eşitlik
- İmplementasyon: `sniper/tests/parity/test_parity_regression.py` (9 test, 379s)
