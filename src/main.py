"""
main.py
Botun asenkron başlangıç noktası.
"""

import asyncio
from state.state_manager import StateManager
from scanner.liquidity_pool import LiquidityPoolScanner
from strategies.bias_detection import BiasDetector
from scanner.sweep_detector import SweepDetector
from scanner.htf_validator import HTFValidator
from execution.order_manager import OrderManager
from utils.logger import ChainLogManager
from execution.risk_manager import RiskManager

async def main():
    # Başlatıcılar
    state_manager = StateManager()
    liquidity_scanner = LiquidityPoolScanner(state_manager)
    sweep_detector = SweepDetector(state_manager)
    htf_validator = HTFValidator(state_manager)
    bias_detector = BiasDetector(state_manager)
    order_manager = OrderManager(state_manager)
    risk_manager = RiskManager(state_manager)

    # Log yöneticisini başlat
    logger = ChainLogManager()

    symbol = "BTCUSDT"

    # A. Bias beklemede logu başlat
    logger.update_state(symbol, 0, False, f"[INFO] [{symbol}] \u001b[33mBIAS: PENDING | D1: RANGE | H4: SHORT\u001b[0m")

    # Adım 1: Asya seansı Likidite Havuzu taraması
    await liquidity_scanner.scan_asia_session()

    # Adım 2: Londra seansında süpürme(tespiti)
    sweep = await sweep_detector.detect_sweep()
    if not sweep:
        print("Sweep (süpürme) tespit edilmedi, işlem yapılmayacak.")
        return

    # Sweep olumluysa B aşamasına geç
    logger.update_state(symbol, 0, True, f"[INFO] [{symbol}] \u001b[32mBIAS: STRONG_SHORT | D1: SHORT | H4: SHORT\u001b[0m")
    logger.update_state(symbol, 1, False, f"[INFO] [{symbol}] \u001b[33mSETUP_SCAN | TYPE: 1H_MAIN | SWEEP(1H): \u001b[32m\u001b[0m | MSS(15M): \u001b[31m\u001b[0m | FVG(1H): \u001b[31m\u001b[0m")

    # Adım 3: HTF onayı
    htf_ok = await htf_validator.validate_htf_on_fvg_or_order_block()
    if not htf_ok:
        print("HTF onayı sağlanmadı, işlem yapılmayacak.")
        return

    # HTF onayı alınırsa C aşamasına geç
    logger.update_state(symbol, 1, True, f"[INFO] [{symbol}] \u001b[32mSETUP_OK | TYPE: 1H_MAIN | SWEEP(1H): \u001b[32m\u001b[0m | MSS(15M): \u001b[32m\u001b[0m | FVG(1H): \u001b[32m\u001b[0m")
    logger.update_state(symbol, 2, False, f"[INFO] [{symbol}] \u001b[33mRETRACE | PEN: %7 \u001b[31m\u001b[0m | WAITING_ZONE...")

    # Adım 4: Bias tespiti
    bias = bias_detector.detect_bias()
    logger.update_state(symbol, 0, True, f"[INFO] [{symbol}] \u001b[32mBIAS: {bias} | D1: SHORT | H4: SHORT\u001b[0m")
    print(f"Tespit edilen Bias: {bias}")

    # Adım 5: Risk yönetimi örneği
    account_balance = 10000.0
    risk_per_trade = 0.01  # %1
    stop_loss_pips = 50
    pip_value = 0.1
    position_size = risk_manager.calculate_position_size(account_balance, risk_per_trade, stop_loss_pips, pip_value)
    print(f"Hesaplanan pozisyon büyüklüğü: {position_size}")

    # Adım 6: Demo emir açma
    await order_manager.place_order(symbol=symbol, side="buy", size=position_size, price=20010.0)

if __name__ == '__main__':
    print("Bot başlatılıyor...")
    asyncio.run(main())
