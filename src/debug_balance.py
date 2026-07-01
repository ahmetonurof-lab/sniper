"""
debug_balance.py — Binance hesap bakiyesi + açık emirleri tek seferlik kontrol.

Çalıştırma:
    cd C:\\Users\\Administrator\\Desktop\\nexus-mcp\\sniper\\src
    python debug_balance.py

Bu script bot.py ile AYNI .env ve config'i kullanır, ama hiçbir trade
açmaz/kapatmaz — sadece okuma yapar (GET istekleri).
"""

import asyncio
import sys
import os

# src/ klasörünü path'e ekle (config, bot_binance import edebilmek için)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
from bot_binance import BinanceRESTClient
from bot_infra import _RateLimiter


async def main():
    if not cfg.BINANCE_API_KEY:
        print("HATA: API key bulunamadi (.env kontrol et)")
        return

    base_url = (
        "https://demo-fapi.binance.com"
        if cfg.IS_TESTNET
        else "https://fapi.binance.com"
    )
    print(f"Mod: {'TESTNET' if cfg.IS_TESTNET else 'MAINNET'}")
    print(f"Base URL: {base_url}\n")

    rest = BinanceRESTClient(
        api_key=cfg.BINANCE_API_KEY,
        api_secret=cfg.BINANCE_API_SECRET,
        base_url=base_url,
        rate_limiter=_RateLimiter(1200),
        semaphore=asyncio.Semaphore(5),
    )

    try:
        # ── Ham /fapi/v2/account cevabı ──────────────────────────
        r = await rest.get("/fapi/v2/account")
        if r.is_err:
            print(f"HATA: account endpoint basarisiz: {r.error}")
            return

        data = r.value
        print("=" * 60)
        print("HESAP BAKIYESI (USDT)")
        print("=" * 60)
        for asset in data.get("assets", []):
            if asset.get("asset") == "USDT":
                wallet_bal = asset.get("walletBalance", "N/A")
                avail_bal = asset.get("availableBalance", "N/A")
                margin_bal = asset.get("marginBalance", "N/A")
                unreal_pnl = asset.get("unrealizedProfit", "N/A")
                init_margin = asset.get("initialMargin", "N/A")
                maint_margin = asset.get("maintMargin", "N/A")
                print(f"  walletBalance     : {wallet_bal}")
                print(f"  availableBalance  : {avail_bal}")
                print(f"  marginBalance     : {margin_bal}")
                print(f"  unrealizedProfit  : {unreal_pnl}")
                print(f"  initialMargin     : {init_margin}")
                print(f"  maintMargin       : {maint_margin}")
                if wallet_bal != "N/A" and avail_bal != "N/A":
                    diff = float(wallet_bal) - float(avail_bal)
                    print(f"  --> FARK (wallet-available): {diff:.4f}")

        # ── Açık pozisyonlar ──────────────────────────────────────
        print("\n" + "=" * 60)
        print("ACIK POZISYONLAR")
        print("=" * 60)
        open_positions = [
            p for p in data.get("positions", []) if float(p.get("positionAmt", 0)) != 0
        ]
        if not open_positions:
            print("  (yok)")
        else:
            for p in open_positions:
                print(
                    f"  {p['symbol']}: amt={p['positionAmt']} "
                    f"entry={p.get('entryPrice')} "
                    f"isolatedMargin={p.get('isolatedMargin', 'N/A')}"
                )

        # ── Tüm semboller için açık emirler ───────────────────────
        print("\n" + "=" * 60)
        print("ACIK EMIRLER (tum semboller)")
        print("=" * 60)
        total_orders = 0
        for sym in cfg.SYMBOLS:
            orders = await rest.get_all_orders(sym)
            if orders:
                total_orders += len(orders)
                print(f"\n  {sym}: {len(orders)} emir")
                for o in orders:
                    otype = rest.get_order_type(o)
                    oprice = rest.get_order_price(o)
                    reduce_only = o.get("reduceOnly", o.get("closePosition", "N/A"))
                    oid = o.get("orderId") or o.get("algoId")
                    print(
                        f"    id={oid} type={otype} side={o.get('side')} "
                        f"trigger={oprice} reduceOnly={reduce_only} qty={o.get('origQty', o.get('quantity'))}"
                    )
        if total_orders == 0:
            print("  (hicbir sembolde acik emir yok)")
        else:
            print(f"\n  TOPLAM: {total_orders} acik emir")

    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
