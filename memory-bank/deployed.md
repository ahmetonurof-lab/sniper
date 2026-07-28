# Deployed Version

| Tarih | Commit | Açıklama |
|-------|--------|----------|
| 2026-07-28 18:53 | `1bed283` | Fibo filter + P1-15 mitigation + trailing_manager guard (MIN_SL_DISTANCE_PCT) + tüm P0/P1/P2 fixleri |

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
