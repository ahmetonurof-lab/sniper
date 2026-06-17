"""
bias_detection.py
Basit bias tespiti modülü.
"""


class BiasDetector:
    def __init__(self, state_manager: object):
        self.state_manager = state_manager

    def detect_bias(self) -> str:
        # Gerçek bias tespiti burada yapılacak
        asia_high, asia_low = self.state_manager.get_asia_range()
        if asia_high is None or asia_low is None:
            return "No Bias (Asia range undefined)"
        # Örnek bias tespiti (yerine strateji uygulanacak)
        return "Bias Detected: Sample Strategy"
