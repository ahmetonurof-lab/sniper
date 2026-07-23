"""
protection_lifecycle.py — Protection policy merkezi (Patch Set 3).

verify_protection, trailing replace semantiği, cleanup ve repair
kararlarını tek yerde toplar. OrderManager mekanik (REST), bu modül
politika (karar).

Plan referansi: new_refactoring_plan1.md Patch Set 3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from models import UNRESTRICTED_STATUSES

log = logging.getLogger("sniper.protection_lifecycle")

_TRIGGERED_RESULTS = frozenset({"SL", "TP"})
_HISTORY_MAX = 5


# ── Dönüş tipleri ──────────────────────────────────────────────


@dataclass
class ProtectionCheckResult:
    """verify() dönüşü — (bool, bool) tuple yerine anlamlı yapı.

    sl_present / tp_present: Binance'teki açık emirler içinde ID bulundu mu?
    sl_healthy / tp_healthy: koruma beklendiği gibi mi? (present VEYA not-required)
    needs_repair: herhangi bir koruma eksik ve onarım gerekli mi?
    """

    sl_present: bool
    tp_present: bool
    sl_healthy: bool
    tp_healthy: bool
    needs_repair: bool
    detail: str = ""

    @property
    def all_healthy(self) -> bool:
        return self.sl_healthy and self.tp_healthy

    def __iter__(self):
        return iter((self.sl_present, self.tp_present))


@dataclass
class CleanupPlan:
    """cleanup_after_confirmed_exit() dönüşü — hangi ID'ler iptal edilmeli?"""

    cancel_ids: list[str] = field(default_factory=list)
    needs_emergency_close: bool = False
    emergency_close_reason: str = ""


# ── ProtectionLifecycleService ──────────────────────────────────


class ProtectionLifecycleService:
    """Protection policy kararlarını merkezileştiren servis.

    REST çağrısı yapmaz — tüm girdiler dışarıdan (open_order_ids, trade)
    verilir. State değişiklikleri trade nesnesi üzerinde yapılır.
    """

    # ── ID envanteri ───────────────────────────────────────────

    def known_ids(self, trade: Any) -> set[str]:
        """Aktif trade'in sahip olabileceği tüm SL/TP order ID
        kaynaklarını toplar: current, prev, history, pending.

        Bu metod, recovery_manager._known_protection_ids() ile
        birebir aynı kümeyi üretir. Geçiş halindeki (henüz cancel
        edilmemiş eski / henüz confirm edilmemiş yeni) ID'lerin
        orphan sanılmaması içindir (A5).
        """
        known: set[str] = set()
        for k in (
            "sl_order_id",
            "tp_order_id",
            "sl_order_id_prev",
            "tp_order_id_prev",
            "pending_sl_order_id",
            "pending_tp_order_id",
        ):
            oid = trade.get(k)
            if oid:
                known.add(str(oid))
        for k in ("sl_order_id_history", "tp_order_id_history"):
            for oid in trade.get(k) or []:
                if oid:
                    known.add(str(oid))
        return known

    # ── Transition guard ────────────────────────────────────────

    def should_skip_reconcile(self, trade: Any) -> bool:
        """Orphan sweep bu trade için atlanmalı mı?

        TRAIL_REPLACING, EXIT_VERIFYING, REPAIR_REQUIRED gibi
        transition state'lerinde yeni emirlerin orphan sanılıp
        iptal edilmesini engeller (A5, A9, A14).
        """
        status = trade.get("status", "")
        return status not in UNRESTRICTED_STATUSES

    # ── Doğrulama ──────────────────────────────────────────────

    def verify(
        self, trade: Any, open_order_ids: set[str] | None
    ) -> ProtectionCheckResult:
        """SL/TP emirlerinin Binance'te hâlâ açık olup olmadığını
        kontrol et.

        open_order_ids: OrderManager.get_open_order_ids()'ten gelen
        güncel açık emir ID'leri. None ise sorgu basarisiz demektir —
        fail-safe: needs_repair=False (dokunma).
        """
        if open_order_ids is None:
            return ProtectionCheckResult(
                sl_present=True,
                tp_present=True,
                sl_healthy=True,
                tp_healthy=True,
                needs_repair=False,
            )
        s_id = str(trade.get("sl_order_id", ""))
        t_id = str(trade.get("tp_order_id", ""))
        expects_sl = bool(trade.get("sl"))
        expects_tp = bool(trade.get("tp"))

        sl_present = bool(s_id) and s_id in open_order_ids
        tp_present = bool(t_id) and t_id in open_order_ids
        sl_healthy = (not expects_sl) or sl_present
        tp_healthy = (not expects_tp) or tp_present
        needs_repair = (expects_sl and not sl_present) or (
            expects_tp and not tp_present
        )

        parts: list[str] = []
        if expects_sl and not sl_present:
            parts.append("SL eksik")
        if expects_tp and not tp_present:
            parts.append("TP eksik")
        if not parts:
            parts.append("healthy")

        return ProtectionCheckResult(
            sl_present=sl_present,
            tp_present=tp_present,
            sl_healthy=sl_healthy,
            tp_healthy=tp_healthy,
            needs_repair=needs_repair,
            detail=", ".join(parts),
        )

    # ── Onarım kararı ───────────────────────────────────────────

    def maybe_repair(self, trade: Any, check: ProtectionCheckResult) -> bool:
        """Onarım gerekli mi? verify() sonucuna göre karar verir.

        Returns: True ise çağıran taraf OrderManager.repair_protection()
        çağırmalı.
        """
        if check.all_healthy:
            return False
        if not check.needs_repair:
            return False
        return True

    # ── Exit sonrası temizlik planı ─────────────────────────────

    def cleanup_after_confirmed_exit(self, trade: Any, result: str) -> CleanupPlan:
        """Exit commit edildikten sonra hangi koruma emirleri iptal
        edilmeli?

        FIX (A8): Davranış 3 sınıfa ayrılır:
          1. result == "SL"  → kalan TP iptal et
          2. result == "TP"  → kalan SL iptal et
          3. TRAIL_CLOSE / WS_FALLBACK / TIMEOUT / MANUAL_CLOSE vb.
             → her iki tarafı da iptal etmeye çalış

        FIX (A7): Acil market close YALNIZCA SL/TP tetiklenme
        path'inde ve tetiklenen tarafın Binance ID'si yoksa.
        Synthetic/market path'lerde pozisyon zaten kapatılmıştır.
        """
        cancel_ids: list[str] = []

        if result == "SL":
            tp_id = trade.get("tp_order_id")
            if tp_id:
                cancel_ids.append(str(tp_id))
        elif result == "TP":
            sl_id = trade.get("sl_order_id")
            if sl_id:
                cancel_ids.append(str(sl_id))
        else:
            for k in ("sl_order_id", "tp_order_id"):
                oid = trade.get(k)
                if oid:
                    cancel_ids.append(str(oid))

        plan = CleanupPlan(cancel_ids=cancel_ids)

        if result in _TRIGGERED_RESULTS:
            trigger_id = (
                trade.get("sl_order_id") if result == "SL" else trade.get("tp_order_id")
            )
            if not trigger_id:
                plan.needs_emergency_close = True
                plan.emergency_close_reason = (
                    f"tetiklenen {result} emri Binance ID'si olmadigi "
                    "icin acil market kapanisi gerekli"
                )

        return plan

    # ── Pending replacement yönetimi ────────────────────────────

    def begin_replace_sl(self, trade: Any, new_id: str) -> None:
        """Yeni SL emri alındı — pending olarak işaretle.

        FIX (A6): Eski ID hemen silinmez; yeni emir pending'de
        bekler. promote_sl() ile current'a taşınır.
        """
        trade["pending_sl_order_id"] = new_id

    def begin_replace_tp(self, trade: Any, new_id: str) -> None:
        """Yeni TP emri alındı — pending olarak işaretle."""
        trade["pending_tp_order_id"] = new_id

    def promote_sl(self, trade: Any) -> None:
        """Pending SL'yi current'a taşı, eski current'ı prev/history'ye
        arşivle. Pending temizlenir."""
        new_id = trade.get("pending_sl_order_id", "")
        if not new_id:
            return

        old_id = trade.get("sl_order_id", "")
        if old_id:
            trade["sl_order_id_prev"] = old_id
            hist = trade.setdefault("sl_order_id_history", [])
            if not isinstance(hist, list):
                hist = []
                trade["sl_order_id_history"] = hist
            hist.append(old_id)
            trade["sl_order_id_history"] = hist[-_HISTORY_MAX:]

        trade["sl_order_id"] = new_id
        trade["pending_sl_order_id"] = ""

    def promote_tp(self, trade: Any) -> None:
        """Pending TP'yi current'a taşı, eski current'ı prev/history'ye
        arşivle. Pending temizlenir."""
        new_id = trade.get("pending_tp_order_id", "")
        if not new_id:
            return

        old_id = trade.get("tp_order_id", "")
        if old_id:
            trade["tp_order_id_prev"] = old_id
            hist = trade.setdefault("tp_order_id_history", [])
            if not isinstance(hist, list):
                hist = []
                trade["tp_order_id_history"] = hist
            hist.append(old_id)
            trade["tp_order_id_history"] = hist[-_HISTORY_MAX:]

        trade["tp_order_id"] = new_id
        trade["pending_tp_order_id"] = ""
