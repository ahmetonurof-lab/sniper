"""
risk_manager.py — Hedge Fon Seviyesinde Risk Yönetim Modülü

Özellikler:
  - Erken Londra (02-08 UTC) risk çarpanı (backtest destekli: EL PF=4.35)
  - Histeresizli devre kesici (DD_trip=15%, DD_reset=10%)
  - Kalıcı state (bot restart'ında hafıza korunur)
  - Thread-safe dosya yazma (filelock ile race condition koruması)
  - 13 coin paralel çalışmaya uygun

Kullanım:
    risk_mgr = RiskManager(initial_equity=10000.0)
    mult = risk_mgr.get_dynamic_risk_multiplier(bakiye, is_early_london=True)
    # ... trade aç ...
    risk_mgr.update_peak(yeni_bakiye)
"""

import json
import os
import logging
from filelock import FileLock

logger = logging.getLogger(__name__)


class RiskManager:
    """Dinamik risk çarpanı + devre kesici + kalıcı state."""

    def __init__(
        self,
        state_file: str = "risk_state.json",
        base_risk: float = 1.0,
        el_mult: float = 1.5,
        dd_trip: float = 15.0,
        dd_reset: float = 10.0,
        initial_equity: float = 10000.0,
    ):
        self.state_file = state_file
        self.lock_file = state_file + ".lock"
        self.base_risk_mult = base_risk
        self.early_london_mult = el_mult
        self.dd_trip = dd_trip
        self.dd_reset = dd_reset

        self.state = self._load_state()
        self.is_circuit_broken = self.state.get("is_circuit_broken", False)
        self.peak_equity = self.state.get("peak_equity", initial_equity)

    # ── State Yönetimi (Thread-Safe) ────────────────────────

    def _load_state(self) -> dict:
        """State dosyasını filelock ile güvenli oku."""
        if not os.path.exists(self.state_file):
            return {"peak_equity": 0.0, "is_circuit_broken": False}
        lock = FileLock(self.lock_file, timeout=5)
        try:
            with lock:
                with open(self.state_file, "r") as f:
                    return json.load(f)
        except json.JSONDecodeError:
            logger.error("State dosyasi bozuk, varsayilan degerlerle baslatiliyor.")
            return {"peak_equity": 0.0, "is_circuit_broken": False}
        except Exception as e:
            logger.error(f"State okunamadi: {e}")
            return {"peak_equity": 0.0, "is_circuit_broken": False}

    def _save_state(self) -> None:
        """Atomik yazma (temp + rename) + filelock ile thread-safe kaydet.

        13 coin paralel calissa bile ayni anda tek yazici gecer.
        """
        lock = FileLock(self.lock_file, timeout=5)
        try:
            with lock:
                temp_file = self.state_file + ".tmp"
                with open(temp_file, "w") as f:
                    json.dump(
                        {
                            "peak_equity": self.peak_equity,
                            "is_circuit_broken": self.is_circuit_broken,
                        },
                        f,
                        indent=4,
                    )
                os.replace(temp_file, self.state_file)
        except Exception as e:
            logger.error(f"State kaydedilemedi: {e}")

    # ── Public API ──────────────────────────────────────────

    def update_peak(self, current_equity: float) -> None:
        """Her trade kapanisinda cagir. Yeni zirve varsa kaydet."""
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            self._save_state()

    def get_current_dd(self, current_equity: float) -> float:
        """Peak'ten simdiki duruma dusus %."""
        if self.peak_equity <= 0:
            return 0.0
        return ((self.peak_equity - current_equity) / self.peak_equity) * 100.0

    def get_dynamic_risk_multiplier(
        self, current_equity: float, is_early_london: bool
    ) -> float:
        """
        Trade acilmadan HEMEN ONCE cagrilir.

        1. Guncel DD'yi hesapla
        2. Histeresizli devre kesiciyi yonet (trip=15%, reset=10%)
        3. Dogru carpani dondur

        Returns:
            early_london_mult (devre kapali ve EL ise)
            base_risk_mult (devre acik veya EL degilse)
        """
        current_dd = self.get_current_dd(current_equity)

        # ── State Machine (Histeresis) ──
        if not self.is_circuit_broken and current_dd >= self.dd_trip:
            self.is_circuit_broken = True
            self._save_state()
            logger.warning(
                "🚨 DEVRE KESICI PATLADI! DD: %%%.2f >= %%%.2f",
                current_dd,
                self.dd_trip,
            )

        elif self.is_circuit_broken and current_dd <= self.dd_reset:
            self.is_circuit_broken = False
            self._save_state()
            logger.info(
                "✅ PORTFOY IYILESTI! DD: %%%.2f <= %%%.2f. Saldiri moduna donuldu.",
                current_dd,
                self.dd_reset,
            )

        # ── Karar ──
        if self.is_circuit_broken:
            return self.base_risk_mult  # Kanama varken EL avantaji kullanilmaZ

        if is_early_london:
            return self.early_london_mult

        return self.base_risk_mult
