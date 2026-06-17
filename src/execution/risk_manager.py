"""
risk_manager.py
Pozisyon büyüklüğü, risk oranı ve stop-loss hesaplamaları için modül.
Sıfıra bölünme hatası (ZeroDivisionError) koruması eklendi.
"""


class RiskManager:
    def __init__(self, state_manager):
        self.state_manager = state_manager

    def calculate_position_size(self, account_balance: float, risk_per_trade: float, stop_loss_pips: float, pip_value: float) -> float:
        """
        Pozisyon büyüklüğünü hesaplar.
        account_balance: Toplam hesap bakiyesi
        risk_per_trade: Her işlemde risk edilecek yüzde (0-1 arası)
        stop_loss_pips: Stop-loss mesafesi pip cinsinden
        pip_value: Pip başına para değeri
        """
        if stop_loss_pips <= 0 or pip_value <= 0:
            return 0.0
            
        risk_amount = account_balance * risk_per_trade
        position_size = risk_amount / (stop_loss_pips * pip_value)
        return round(position_size, 8)

    def check_risk_limits(self, current_risk: float, max_risk: float) -> bool:
        """
        Şu anki risk, belirlenen maksimum riski aşarsa False döner.
        """
        return current_risk <= max_risk
