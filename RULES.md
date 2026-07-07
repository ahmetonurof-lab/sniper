# RULES — sniper

## Zorunlu Protokol

1. **Bismillahirrahmanirrahim** — Her göreve bu selamla başla. Atlanırsa görev sayılmaz.

2. **index.json ile navigasyon** — Kod aramak için dosyaları tek tek tarama. Önce `index.json`'u oku. İçinde `function_name → dosya:satır` var. Bulduktan sonra o dosyayı oku, değiştir. Bu, context ve token tasarrufu içindir.

3. **Memory Bank** — İşlem bittiğinde `memory-bank/` altındaki dosyaları güncelle. Dosya yoksa bu adımı atla.

4. **Sürüm Kontrolü** — `git add .`, pre-commit hook'larını ezme, commit et, push yap.

5. **Kapanış** — Teknik işi özetleyen Türkçe rapor sun. "Hazır reis" gibi sabit kalıplar kullanma.
