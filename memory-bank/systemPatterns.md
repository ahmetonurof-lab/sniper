# System Patterns — Sniper Bot

## Sistem Mimarisi

```
bot.py (orchestrator)
├── bot_pipeline.py (sinyal pipeline)
├── bot_positions.py (pozisyon yönetimi)
├── bot_binance.py (Binance REST wrapper)
├── bot_infra.py (utilities, cache, OHLC)
├── analyzer.py (market analizi)
├── state_machine.py (V40 Sniper Flow)
├── risk_manager.py (risk hesaplama)
├── trader.py (emir gönderimi)
├── websocket.py (WS hub)
├── monitor.py (runtime sayaçları)
└── performance.py (trade geçmişi)
```

## V40 Sniper Flow State Machine
**5 state:**
- IDLE → ARMED → WAIT_RETRACE → WAIT_CONFIRM → READY_TO_ENTER → ENTERED

**SNIPER GATE (tek koşul):**
```
sweep_detected AND mss_confirmed AND is_ce_tap AND ltf_confirmed → READY_TO_ENTER
```

## Temel Tasarım Kararları
1. **Tek yönlü bağımlılık zinciri**: Döngüsel import yok.
2. **Event-driven**: Analyzer → EventRouter → StateMachine.
3. **V40 sadeleştirme**: 3 state silindi, Case A/C dallanması kalktı.
4. **Sembol bazlı asyncio lock**: Aynı sembolde eşzamanlı emir engellenir.
