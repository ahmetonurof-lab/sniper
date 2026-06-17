"""
state_manager.py
Seans zaman dilimi kontrolleri ve genel durum yönetimi.
Çoklu sembol desteği eklendi.
"""

from datetime import datetime


class StateManager:
    def __init__(self):
        # symbol -> {"high": float, "low": float}
        self.asia_ranges = {}
        self.current_session = None

    def record_asia_session_range(self, symbol: str, high: float, low: float) -> None:
        self.asia_ranges[symbol] = {"high": high, "low": low}
        print(f"[{symbol}] Asia session high set to {high}, low set to {low}")

    def get_asia_range(self, symbol: str) -> tuple[float | None, float | None]:
        range_data = self.asia_ranges.get(symbol)
        if range_data:
            return range_data["high"], range_data["low"]
        return None, None
