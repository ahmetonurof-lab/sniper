"""
newyork_reversal.py
New York seansı için reversal ve tetik mantığı.
"""

from datetime import datetime, time


class NewYorkReversal:
    def __init__(self, state_manager):
        self.state_manager = state_manager

    def is_newyork_session(self, current_time: datetime) -> bool:
        """
        New York seansı saat aralığı kontrolü (08:30 - 09:30 NY zamanı olarak varsayalım).
        """
        ny_start = time(8, 30)
        ny_end = time(9, 30)
        current_time_only = current_time.time()
        return ny_start <= current_time_only <= ny_end

    def detect_reversal_trigger(self, price_data, choch_detector) -> bool:
        """
        New York seansında reversal tetik kontrolü.
        price_data: 1m pivot, mum kapanışı vb. veriler.
        choch_detector: CHoCH tespit modülü instance.

        Mevcut 15M CHoCH yapısını kontrol eder.

        Returns:
            True: Tetik koşulları sağlandı.
            False: Tetik yok.
        """
        bars_15m = price_data.get("15m_bars", [])
        if not bars_15m:
            return False
        choch_list = choch_detector.detect_choch(bars_15m)
        if choch_list and choch_list[-1].is_convincing:
            return True
        return False
