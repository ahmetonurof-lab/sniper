"""
logger.py
Terminal log hiyerarşisine uygun zincirleme, parite bazlı loglama yöneticisi.
"""

import threading


class ChainLogManager:
    def __init__(self):
        # Symbol bazında durum satırı tutar
        self.symbol_states = {}
        self.lock = threading.Lock()

    def _can_log(self, symbol, line_index, required_prev_green):
        """
        Belirli satır indexi için loglamaya izin var mı? Znincirleme kısıt.
        required_prev_green: Önceki satırın yeşil olması zorunluysa True.
        """
        if line_index == 0:
            # İlk satır her zaman loglanabilir
            return True
        with self.lock:
            states = self.symbol_states.get(symbol, [])
            if len(states) < line_index:
                return False  # Önceki satırlar yok
            if required_prev_green:
                # Önceki satırın durumunun yeşil (🟩) olması gerekiyor
                prev_state = states[line_index-1]
                return prev_state.get('green', False)
            else:
                return True

    def update_state(self, symbol, line_index, green, text):
        """
        Log durumu güncelle ve logu koşula göre ekrana bas.
        """
        with self.lock:
            if symbol not in self.symbol_states:
                # Yeni sembol için satır listesi oluştur
                self.symbol_states[symbol] = []
            states = self.symbol_states[symbol]

            # Gerekirse satır sayısını doldur
            while len(states) <= line_index:
                states.append({'green': False, 'text': ''})

            # Zincirleme kuralı gereği önceki satır yeşil değilse log basma
            required_prev_green = line_index > 0
            if not self._can_log(symbol, line_index, required_prev_green):
                return

            # Durumu güncelle
            states[line_index]['green'] = green
            states[line_index]['text'] = text

            # İlgili satırdan itibaren satırları sırayla ekrana yazdır
            self._print_chain(symbol)

    def _print_chain(self, symbol):
        states = self.symbol_states.get(symbol, [])
        if not states:
            return

        # Zincirde yeşil satırdan sonra gelen ilk sarıya (🟨) kadar göster
        # Üst satır yeşil değilse aşağıdaki satırlar görünmez

        # Yazdırılacak satırlar
        lines_to_print = []
        for i, state in enumerate(states):
            if i == 0:
                # İlk satır her zaman göster
                lines_to_print.append(state['text'])
                continue

            # Zincirleme kural: önceki satır yeşil olmalı
            prev_green = states[i-1]['green']
            if not prev_green:
                break

            # Eğer mevcut satır sarıysa göster, ardından kes
            if '🟨' in state['text']:
                lines_to_print.append(state['text'])
                break

            # Yeşil satırlar gösterilmeye devam eder
            lines_to_print.append(state['text'])

        # Konsol çıktısını temizleyip yeni durumu göster
        print(f"\n== {symbol} LOG DURUMU ==")
        for line in lines_to_print:
            print(line)


# Örnek kullanım:
# logger = ChainLogManager()
# logger.update_state('BTCUSDT', 0, False, '[INFO] [BTCUSDT] 🟨 BIAS: PENDING | D1: RANGE | H4: SHORT')
# logger.update_state('BTCUSDT', 1, True,  '[INFO] [BTCUSDT] 🟩 BIAS: STRONG_SHORT | D1: SHORT | H4: SHORT')
# ...
