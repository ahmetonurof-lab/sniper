"""
console_reporter.py — _pl() loglama + state dedup + print + format + separator.

PaperTrader._pl() buraya delegate eder. Imza birebir aynı kalır.

Faz 6.2: display_active_position, display_session_status, display_sweep_status,
display_fvg_status metodları eklendi — _on_15m_close() içindeki ~100 satır
pure string formatlama buraya taşındı.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from session import SessionState
    from retrace_state import RetraceStateMachine

TR_TZ = timezone(timedelta(hours=3))


def _fmt_price(v: float, max_decimals: int = 6) -> str:
    """Fiyatı kuyruk sıfırları olmadan, hassas ondalıkla formatlar."""
    return f"{v:.{max_decimals}f}".rstrip("0").rstrip(".")


class ConsoleReporter:
    """Konsol çıktı formatlaması ve state-based dedup.

    PaperTrader'dan ayrıştırılmıştır. Tüm _pl() mantığı buradadır:
      - State dedup: aynı (sym, key) için tekrarlayan mesajları bastırır
      - Separator: sembol değiştiğinde boş satır basar
      - Timestamp: TR saat diliminde formatlar
    """

    def __init__(self):
        self._log_state: dict[str, dict[str, str]] = {}
        self._prev_print_sym: str | None = None

    def emit(self, sym: str, key: str, msg: str, force: bool = False) -> None:
        """PaperTrader._pl() ile birebir aynı imza ve davranış.

        Args:
            sym: Sembol adı (örn: "BTCUSDT") veya "SYSTEM"
            key: Durum anahtarı (dedup için)
            msg: Yazdırılacak mesaj
            force: True ise dedup'u atla, her zaman yazdır
        """
        prev = self._log_state.get(sym, {}).get(key)
        if not force and prev == msg:
            return
        self._log_state.setdefault(sym, {})[key] = msg
        ts = datetime.now(TR_TZ).strftime("%H:%M:%S")
        separator = "" if self._prev_print_sym == sym else "\n"
        self._prev_print_sym = sym
        print(f"{separator}[{ts}] [{sym:<12}] {msg}", flush=True)

    def clear_state(self, sym: str, key: str) -> None:
        """Belirli bir state anahtarını temizle (zorla yeniden yazdırmak için)."""
        self._log_state.get(sym, {}).pop(key, None)

    # ── Faz 6.2: Display formatlama metodları ──────────────────

    def display_active_position(
        self, sym: str, trade: dict, hour: int, minute: int
    ) -> None:
        """Pozisyon açıkken 15m display. Orijinal st_ses force print ile birebir aynı.

        Yan etki: st_fvg ve st_wck state'lerini temizler.
        """
        self.clear_state(sym, "st_fvg")
        self.clear_state(sym, "st_wck")
        fvg_top = trade.get("fvg_top")
        fvg_bottom = trade.get("fvg_bottom")
        fvg_dir = trade.get("fvg_direction", "")
        if fvg_top is not None and fvg_bottom is not None:
            fvg_label = f"FVG: {fvg_dir} {_fmt_price(fvg_top)}-{_fmt_price(fvg_bottom)}"
        else:
            fvg_label = "FVG: ISLEMDE"
        self.emit(sym, "st_fvg", f"\U0001f7e9 {fvg_label}", force=True)
        side_icon = "\U0001f7e9" if trade["side"] == "long" else "\U0001f7e5"
        ts = f"{hour:02d}:{minute:02d}"
        self.emit(
            sym,
            "st_ses",
            f"{side_icon} POZISYON AKTIF | {trade['side'].upper()} @ {_fmt_price(trade['entry_price'])}"
            f" | SL: {_fmt_price(trade['sl'])} | TP: {_fmt_price(trade['tp'])}"
            f" | TRAIL: {trade.get('trailing_count', 0)}x | {ts} UTC",
            force=True,
        )

    def display_session_status(
        self, sym: str, session: str, hour: int, minute: int, ss: "SessionState"
    ) -> None:
        """London/NY session display: CBDR locked + bias + range type."""
        from session import DailyBias

        # CBDR type emoji
        cbdr_type = self._cbdr_label(ss.cbdr_start, ss.cbdr_end)
        cbdr_emojis = {
            "ASIA_RANGE": "\U0001f534",
            "DEFAULT": "\U0001f535",
            "REAL_CBDR": "\u26aa",
        }
        ses_emoji = cbdr_emojis.get(cbdr_type, "\U0001f7e9")

        ts = f"{hour:02d}:{minute:02d}"

        bias_str = ""
        if ss.daily_bias != DailyBias.NEUTRAL:
            d = "LONG" if ss.daily_bias == DailyBias.BULLISH else "SHORT"
            c = "\U0001f7e9" if d == "LONG" else "\U0001f7e5"
            bias_str = f" | BIAS: {c}{d}"

        cbdr_s = "\u2705 LOCKED" if ss.cbdr_locked else "\u23f3 BODY TRACKING..."
        rt = ss.range_type if ss.range_type in ("CBDR", "ASIA", "DEAD") else ""
        rt_str = f" | RANGE: {rt}" if rt else ""

        self.emit(
            sym,
            "st_ses",
            f"{ses_emoji} {cbdr_type} | {ts} UTC | CBDR: {cbdr_s}{rt_str}{bias_str}",
            force=True,
        )

    def _cbdr_label(self, start: int, end: int) -> str:
        if start == 1 and end == 5:
            return "ASIA_RANGE"
        if start == 19 and end == 1:
            return "REAL_CBDR"
        return "DEFAULT"

    def display_sweep_status(
        self,
        sym: str,
        ss: "SessionState",
        rsm: "RetraceStateMachine",
        hour: int,
        minute: int,
    ) -> str:
        """Sweep durum display. RSM state'ine dayanir (ss latch'ine degil).

        RSM SWEEP_DETECTED/TRIGGER_READY -> "DETECTED"
        RSM BIAS_LOCKED               -> "TAMAMLANDI" (bias kilitli, FVG bekleniyor)
        RSM IDLE + ss.sweep_confirmed -> "DETECTED" (bekleyen latch'li sweep)
        RSM IDLE + bias belirlenmis   -> "TAMAMLANDI" (sweep olmus, tüketilmis)
        RSM IDLE + bias yok           -> "BEKLENIYOR"
        range DEAD                    -> "DEAD"

        Returns:
            "detected" — sweep bulundu/isleniyor
            "waiting"  — sweep bekleniyor
            "dead"     — CBDR/ASIA dead
        """
        from session import DailyBias

        ts = f"{hour:02d}:{minute:02d}"
        st = rsm.state_name

        # RSM bir sweep'i isliyorsa / trigger hazirsa -> DETECTED
        if st in ("SWEEP_DETECTED", "TRIGGER_READY"):
            sd = rsm.direction or ss.sweep_direction or "bullish"
            sl = rsm.sweep_level or ss.sweep_level or 0.0
            si = "\U0001f7e9" if sd == "bullish" else "\U0001f7e5"
            self.emit(
                sym,
                "st_swp",
                f"\U0001f7e9 SWEEP: DETECTED | {si}{sd.upper()} | [{sl:.2f}]"
                f" | CBDR: [{ss.cbdr_body_low:.4f}-{ss.cbdr_body_high:.4f}]",
                force=True,
            )
            return "detected"

        # RSM bias kilitli ise sweep zaten olmus — TAMAMLANDI, FVG bekleniyor
        if st == "BIAS_LOCKED":
            d = "LONG" if rsm.direction == "bullish" else "SHORT"
            c = "\U0001f7e9" if d == "LONG" else "\U0001f7e5"
            rt = ss.range_type if ss.range_type in ("CBDR", "ASIA") else "CBDR"
            cbdr_pct = (
                ((ss.cbdr_body_high - ss.cbdr_body_low) / ss.cbdr_body_low * 100)
                if ss.cbdr_body_low > 0
                else 0
            )
            self.emit(
                sym,
                "st_swp",
                f"\u2705 SWEEP: TAMAMLANDI | {c}{d} bias | {rt}: "
                f"[{ss.cbdr_body_low:.4f}-{ss.cbdr_body_high:.4f}]"
                f" | (%{cbdr_pct:.2f}) | FVG bekleniyor | {ts}",
                force=True,
            )
            return "detected"

        # Sweep yok — CBDR/ASIA dead
        if ss.range_type == "DEAD":
            self.emit(
                sym,
                "st_swp",
                f"\U0001f480 CBDR/ASIA DEAD \u2014 sweep aranm\u0131yor | {ts}",
                force=True,
            )
            return "dead"

        # RSM IDLE: bekleyen latch'li sweep (on_sweep dedup senaryosu)
        if ss.sweep_confirmed:
            sd = ss.sweep_direction or "bullish"
            sl = ss.sweep_level or 0.0
            si = "\U0001f7e9" if sd == "bullish" else "\U0001f7e5"
            self.emit(
                sym,
                "st_swp",
                f"\U0001f7e9 SWEEP: DETECTED | {si}{sd.upper()} | [{sl:.2f}]"
                f" | CBDR: [{ss.cbdr_body_low:.4f}-{ss.cbdr_body_high:.4f}]",
                force=True,
            )
            return "detected"

        # RSM IDLE: bias belirlenmis ise sweep zaten olmus/tüketilmis —
        # "BEKLENIYOR" yanlis. Bot bias yönündeki FVG'yi bekliyor.
        if ss.daily_bias != DailyBias.NEUTRAL:
            d = "LONG" if ss.daily_bias == DailyBias.BULLISH else "SHORT"
            c = "\U0001f7e9" if d == "LONG" else "\U0001f7e5"
            rt = ss.range_type if ss.range_type in ("CBDR", "ASIA") else "CBDR"
            cbdr_pct = (
                ((ss.cbdr_body_high - ss.cbdr_body_low) / ss.cbdr_body_low * 100)
                if ss.cbdr_body_low > 0
                else 0
            )
            self.emit(
                sym,
                "st_swp",
                f"\u2705 SWEEP: TAMAMLANDI | {c}{d} bias | {rt}: "
                f"[{ss.cbdr_body_low:.4f}-{ss.cbdr_body_high:.4f}]"
                f" | (%{cbdr_pct:.2f}) | FVG bekleniyor | {ts}",
                force=True,
            )
            return "detected"

        # Sweep yok — gercekten bekleniyor (bias henuz belirlenmedi)
        rt = ss.range_type if ss.range_type in ("CBDR", "ASIA") else "CBDR"
        cbdr_pct = (
            ((ss.cbdr_body_high - ss.cbdr_body_low) / ss.cbdr_body_low * 100)
            if ss.cbdr_body_low > 0
            else 0
        )
        self.emit(
            sym,
            "st_swp",
            f"\U0001f7e8 SWEEP: BEKLENIYOR | {rt}: [{ss.cbdr_body_low:.4f}-{ss.cbdr_body_high:.4f}]"
            f" | (%{cbdr_pct:.2f}) | {ts}",
            force=True,
        )
        self.clear_state(sym, "st_fvg")
        self.clear_state(sym, "st_wck")
        return "waiting"

    def display_fvg_status(
        self,
        sym: str,
        rsm: "RetraceStateMachine",
        ss: "SessionState",
        min_fvg: float,
        current_close: float,
    ) -> None:
        """GAP (FVG/IFVG) durum display.

        Tarama:  🟨 GAP_SCAN | ARANIYOR...
        FVG:     🟩 FVG:[...] | ✅ HAZIR | CLOSE: ... | ➡️ ENTRY BEKLENIYOR
        IFVG:    🟩 IFVG:[...] | ✅ HAZIR | CLOSE: ... | ➡️ ENTRY BEKLENIYOR

        Gap bulundugunda st_fvg (GAP_SCAN) kaldirilir, st_wck tek satir
        olarak FVG veya IFVG detayini basar.
        """
        from session import DailyBias

        if rsm.state_name == "TRIGGER_READY":
            tfvg = rsm.trigger_fvg
            # IFVG mi FVG mi? Kaynak etiketine gore sec
            src = getattr(rsm, "_last_trigger_source", None)
            tag = "IFVG" if src == "IFVG" else "FVG"
            # Tarama satirini kaldir, tek satir detay bas
            self.clear_state(sym, "st_fvg")
            self.emit(
                sym,
                "st_wck",
                f"\U0001f7e9 {tag}:[{_fmt_price(tfvg.bottom)} - {_fmt_price(tfvg.top)}]"
                f" | \u2705 HAZIR | CLOSE: {_fmt_price(current_close)}"
                f" | \u27a1\ufe0f ENTRY BEKLENIYOR",
                force=True,
            )
        elif rsm.state_name in ("SWEEP_DETECTED", "BIAS_LOCKED"):
            self.emit(
                sym,
                "st_fvg",
                f"\U0001f7e8 GAP_SCAN | MIN_SIZE: {min_fvg:.6f} | ARANIYOR...",
                force=True,
            )
            self.clear_state(sym, "st_wck")
        else:
            # IDLE — sweep durumuna gore GAP tarama gostergesi.
            if ss.daily_bias == DailyBias.NEUTRAL:
                # Sweep henuez yok, tarama sirasi gelmedi.
                self.clear_state(sym, "st_fvg")
                self.clear_state(sym, "st_wck")
            else:
                # Sweep tamamlandi (bias belirlendi), RSM IDLE —
                # yeni sweep gelmez (latch/lock), GAP bekleniyor.
                self.emit(
                    sym,
                    "st_fvg",
                    f"\U0001f7e8 GAP_SCAN | MIN_SIZE: {min_fvg:.6f} | ARANIYOR...",
                    force=True,
                )
                self.clear_state(sym, "st_wck")
