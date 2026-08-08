# Deployed Version

| Tarih | Commit | Açıklama |
|-------|--------|----------|
| 2026-08-08 03:07 | `daaeeb0` | RECOVERY tick_size parity fix — screen 366235.bot, venv python, run paper-20260808-000537. Teyit: ALGO tick_size=1e-05 / RENDER tick_size=0.001, ikisi de `[TRAIL] trail#1` updated (fvg_scan_multihop) — önceden tick=0.1 yüzünden kilitliydi |
| 2026-08-07 01:45 | `b9c2d53` | Continuation-confirm + is_placeable stale-candidate guard (FVG trailing) — screen 349790.bot, venv python, run paper-20260806-223127 |
| 2026-07-28 18:53 | `1bed283` | Fibo filter + P1-15 mitigation + trailing_manager guard (MIN_SL_DISTANCE_PCT) + tüm P0/P1/P2 fixleri |
| 2026-07-28 23:03 | `9a069e6` | Guard trailing_manager'dan kaldırıldı (kategori hatası). apply_min_sl_distance entry_manager'de kaldı. would_reject backtest'te kaldı. order_manager -2021 handler canlıda kaldı. |

## Güncelleme Talimatı

```bash
cd ~/sniper && git pull
# Bu dosyayı güncelle:
echo "| $(date '+%Y-%m-%d %H:%M') | $(git rev-parse --short HEAD) | $(git log -1 --format='%s') |" >> memory-bank/deployed.md
git add memory-bank/deployed.md
git commit -m "deploy: $(git rev-parse --short HEAD)"
git push
```

Not: Bu dosya deploy anında commit'lenir ve push edilir — böylece hangi ortamda hangi commit'in çalıştığı history'de kalır.
