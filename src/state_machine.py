# state_machine.py

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from models import Bar

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# ─────────────────────────────────────────────
# STATE DEFINITIONS
# ─────────────────────────────────────────────


class SetupState(StrEnum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    WAIT_RETRACE = "WAIT_RETRACE"
    WAIT_CONFIRM = "WAIT_CONFIRM"
    READY_TO_ENTER = "READY_TO_ENTER"
    ENTERED = "ENTERED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


# ─────────────────────────────────────────────
# PENETRATION ENGINE
# ─────────────────────────────────────────────


class PenetrationEngine:
    """
    FVG içine penetration oranını 0→1 ölçeğinde hesaplar.

    SHORT: anchor = fvg_upper, price yukarıdan aşağı girer
           price = fvg_upper → pen=0 | price = fvg_lower → pen=1
    LONG:  anchor = fvg_lower, price aşağıdan yukarı girer
           price = fvg_lower → pen=0 | price = fvg_upper → pen=1

    Unified formula:
        penetration = |price - anchor| / |fvg_upper - fvg_lower|
    """

    def __init__(self, fvg_upper: float, fvg_lower: float, direction: str) -> None:
        self.fvg_upper = fvg_upper
        self.fvg_lower = fvg_lower
        self.direction = direction
        self.size = abs(fvg_upper - fvg_lower)

    def get_penetration(self, price: float) -> float:
        if self.size == 0:
            return 0.0
        if self.direction == "SHORT":
            # SHORT: price yukarıdan aşağı girer
            if price >= self.fvg_upper:
                return 0.0  # henüz FVG'ye girmedi
            if price <= self.fvg_lower:
                return 1.0  # FVG'yi tamamen geçti
            return (self.fvg_upper - price) / self.size
        else:  # LONG
            # LONG: price aşağıdan yukarı girer
            if price <= self.fvg_lower:
                return 0.0  # henüz FVG'ye girmedi
            if price >= self.fvg_upper:
                return 1.0  # FVG'yi tamamen geçti
            return (price - self.fvg_lower) / self.size


# ─────────────────────────────────────────────
# CORE DATA MODEL
# ─────────────────────────────────────────────


@dataclass
class SymbolState:
    symbol: str

    state: SetupState = SetupState.IDLE
    direction: str | None = None  # LONG / SHORT
    htf_bias: str | None = None
    htf_strength: str | None = None
    d1_bias: str | None = None  # D1 BOS yönü (analyzer)
    h4_bias_val: str | None = None  # H4 binary yönü (analyzer)
    entry_price: float | None = None

    # HTF / 15m structure
    fvg_upper: float | None = None
    fvg_lower: float | None = None
    fvg_time: int | None = None

    sweep_level: float | None = None
    sweep_bar_index: int | None = None
    sweep_tf: str | None = None  # 1H / 2H — telemetry only
    mss_level: float | None = None
    mss_bar_index: int | None = None
    h4_swing_level: float | None = None
    h1_liquidity_level: float | None = None

    created_at: int = field(default_factory=lambda: int(time.time()))
    expires_at: int | None = None

    wait_confirm_since_ts: int | None = None
    fvg_entry_bar_index: int | None = None

    sweep_detected: bool = False
    mss_confirmed: bool = False
    displacement_confirmed: bool = False
    ltf_confirmed: bool = False
    is_ce_tap: bool = False
    displacement_origin: float | None = None

    def reset_flags(self):
        self.sweep_detected = False
        self.mss_confirmed = False
        self.displacement_confirmed = False
        self.ltf_confirmed = False
        self.is_ce_tap = False

        self.sweep_level = None
        self.sweep_bar_index = None
        self.sweep_tf = None

        self.mss_level = None
        self.mss_bar_index = None

        self.fvg_upper = None
        self.fvg_lower = None
        self.fvg_time = None

        self.direction = None
        self.entry_price = None
        self.fvg_entry_bar_index = None
        self.displacement_origin = None
        self.d1_bias = None
        self.h4_bias_val = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


# ─────────────────────────────────────────────
# STATE MACHINE CORE
# ─────────────────────────────────────────────


class StateMachine:
    def __init__(self, config=None):
        self.symbols: dict[str, SymbolState] = {}
        self.config = config
        self._last_bar: Bar | None = None  # zombie setup invalidation için son kapanan bar

    # ─────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────

    def get(self, symbol: str) -> SymbolState:
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolState(symbol=symbol)
        return self.symbols[symbol]

    def update_from_event(self, symbol: str, event: dict):
        state = self.get(symbol)

        if state.is_expired():
            state.state = SetupState.EXPIRED
            return

        event_type = event.get("type")

        if event_type == "SWEEP":
            self._handle_sweep(state, event)
        elif event_type == "MSS":
            self._handle_mss(state, event)
        elif event_type == "FVG_CREATED":
            self._handle_fvg(state, event)
        elif event_type == "LTF_CONFIRM":
            self._handle_ltf(state, event)
        elif event_type == "HTF_BIAS":
            self._handle_htf_bias(state, event)
        elif event_type == "HTF_LEVELS":
            self._handle_htf_levels(state, event)

        self._evaluate(state, last_closed_bar=self._last_bar)

    # ─────────────────────────────────────────
    # EVENT HANDLERS
    # ─────────────────────────────────────────

    def _handle_sweep(self, state: SymbolState, event: dict):
        if event.get("tf") not in ("1H", "2H", "15m"):
            return
        if state.state != SetupState.IDLE:
            logger.debug("[%s] Sweep atlandı — state=%s", state.symbol, state.state)
            return
        state.sweep_detected = True
        state.sweep_level = event.get("level")
        state.sweep_bar_index = event.get("bar_index")
        state.sweep_tf = event.get("tf")  # telemetry: 1H / 2H
        state.state = SetupState.ARMED
        logger.info("[%s] SWEEP → ARMED | tf=%s level=%s", state.symbol, event.get("tf"), event.get("level"))

    def _handle_mss(self, state: SymbolState, event: dict):
        logger.info(
            "[MSS-HANDLE] symbol=%s state=%s level=%s dir=%s",
            state.symbol,
            state.state,
            event.get("level"),
            event.get("direction"),
        )

        if state.state not in (SetupState.ARMED, SetupState.WAIT_RETRACE, SetupState.WAIT_CONFIRM):
            logger.warning(
                "[MSS-SKIP] %s state=%s MSS reddedildi level=%s dir=%s",
                state.symbol,
                state.state,
                event.get("level"),
                event.get("direction"),
            )
            return

        state.mss_confirmed = True
        state.mss_level = event.get("level")
        state.mss_bar_index = event.get("bar_index")

        # WAIT_CONFIRM gate: direction zaten varsa eski setup'ı temizle,
        # yeni MSS yönüyle taze WAIT_RETRACE başlat
        if state.state == SetupState.WAIT_CONFIRM and state.direction is not None:
            logger.warning(
                "[%s] MSS WAIT_CONFIRM gate: direction zaten var (%s) → eski setup sıfırlanıp WAIT_RETRACE'e geç",
                state.symbol,
                state.direction,
            )
            state.is_ce_tap = False
            state.ltf_confirmed = False
            state.fvg_entry_bar_index = None
            state.wait_confirm_since_ts = None
            state.fvg_upper = None
            state.fvg_lower = None
            state.displacement_origin = event.get("impulse_origin") or event.get("level")
            state.direction = event.get("direction")
            max_wait = getattr(self.config, "MAX_SETUP_WAIT_HOURS", 16.0) if self.config else 16.0
            state.expires_at = int(time.time()) + int(max_wait * 3600)
            state.state = SetupState.WAIT_RETRACE
            logger.info(
                "[%s] MSS (WAIT_CONFIRM gate) → WAIT_RETRACE | yeni_dir=%s | expires_in=%.0fh",
                state.symbol,
                state.direction,
                max_wait,
            )
            return

        if state.displacement_origin is None:
            state.displacement_origin = event.get("impulse_origin") or event.get("level")

        if state.direction is None:
            state.direction = event.get("direction")

        if state.state in (SetupState.ARMED, SetupState.WAIT_RETRACE, SetupState.WAIT_CONFIRM):
            sweep_tf = getattr(state, "sweep_tf", "1H")
            if sweep_tf == "15m":
                max_wait = getattr(self.config, "MAX_SETUP_WAIT_HOURS_15M", 8.0) if self.config else 8.0
            else:
                max_wait = getattr(self.config, "MAX_SETUP_WAIT_HOURS", 16.0) if self.config else 16.0
            state.expires_at = int(time.time()) + int(max_wait * 3600)
            state.state = SetupState.WAIT_RETRACE
            logger.info("[%s] MSS confirmed → WAIT_RETRACE | expires_in=%.0fh", state.symbol, max_wait)
        else:
            logger.info("[%s] MSS confirmed → WAIT_RETRACE", state.symbol)

    def _handle_fvg(self, state: SymbolState, event: dict):
        # Terminal state'lerde FVG kabul edilmez
        if state.state in (
            SetupState.INVALIDATED,
            SetupState.EXPIRED,
            SetupState.ENTERED,
        ):
            state.fvg_upper = None
            state.fvg_lower = None
            logger.debug("[%s] FVG event reddedildi — state=%s", state.symbol, state.state)
            return

        # Critical: WAIT_CONFIRM ve READY_TO_ENTER'da FVG değişikliğini reddet.
        # Mid-setup silent overwrite — FVG setup sırasında değişirse
        # entry/SL/TP seviyeleri bozulur, kontrolsüz trade üretilir.
        if state.state in (SetupState.WAIT_CONFIRM, SetupState.READY_TO_ENTER):
            logger.warning(
                "[%s] FVG mid-setup overwrite reddedildi — state=%s | mevcut=[%.5f-%.5f] yeni=[%.5f-%.5f]",
                state.symbol,
                state.state,
                state.fvg_upper,
                state.fvg_lower,
                event.get("upper"),
                event.get("lower"),
            )
            return

        state.fvg_upper = event.get("upper")
        state.fvg_lower = event.get("lower")
        state.fvg_time = event.get("time")

        if state.state == SetupState.WAIT_RETRACE:
            logger.info("[%s] FVG güncellendi — state=%s", state.symbol, state.state)
            return

        if state.mss_confirmed:
            state.state = SetupState.WAIT_RETRACE

        logger.info("[%s] FVG kaydedildi | upper=%.5f lower=%.5f", state.symbol, state.fvg_upper, state.fvg_lower)

    def check_retrace(self, symbol: str, current_bar: Bar, atr: float = 0.0) -> None:
        """
        Her 1m kapanışında check_retrace çağrılır.
        V40 Sniper Flow — minimalist penetrasyon takibi.

        WAIT_RETRACE (kutu dışı, pasif izleme):
          pen >= 0.15 (%15 barajı geçildi) → WAIT_CONFIRM
          pen > 1.00 (%100 tamamen delindi) → _do_invalidate()
          pen < 0.15 olduğu sürece bu state'te kalır, CPU tasarrufu.

        WAIT_CONFIRM (kutu içi, aktif pusu):
          pen > 1.00 (%100 delinirse) → _do_invalidate()
          0.15 <= pen <= 1.00 → LTF_CONFIRM event'i beklenir
        """
        state = self.get(symbol)

        # Son kapanan bar referansını güncelle — event-triggered invalidation için
        self._last_bar = current_bar

        if state.fvg_upper is None or state.fvg_lower is None:
            return
        if state.direction is None:
            return

        engine = PenetrationEngine(state.fvg_upper, state.fvg_lower, state.direction)
        price = current_bar.close
        pen = engine.get_penetration(price)
        pen_min = getattr(self.config, "FVG_PENETRATION_MIN", 0.15)

        if state.state == SetupState.WAIT_RETRACE:
            if pen < pen_min:
                return  # henüz kutuya girmedi, pasif bekle

            if pen >= 1.0:
                self._do_invalidate(state, f"PEN >= %100 (pen={pen:.2f})")
                return

            # pen >= 0.15 → WAIT_CONFIRM
            state.is_ce_tap = True
            state.fvg_entry_bar_index = current_bar.index
            state.wait_confirm_since_ts = getattr(current_bar, "timestamp", None)
            state.state = SetupState.WAIT_CONFIRM
            logger.info(
                "[%s] RETRACE ✓ penetration=%.2f (%.0f%%) → WAIT_CONFIRM | dir=%s | close=%.5f",
                symbol,
                pen,
                pen * 100,
                state.direction,
                price,
            )

        elif state.state == SetupState.WAIT_CONFIRM:
            if pen >= 1.0:
                self._do_invalidate(state, f"PEN >= %100 (pen={pen:.2f})")
                return
            # 0.15 <= pen <= 1.00 → bekle, LTF_CONFIRM event'i gelecek

    def _do_invalidate(self, state: SymbolState, reason: str) -> None:
        """Setup'ı tek çağrıda INVALIDATED yap + flag'leri sıfırla."""
        logger.warning(
            "[%s] ❌ SETUP_INVALIDATED | %s",
            state.symbol,
            reason,
        )
        state.state = SetupState.INVALIDATED
        state.reset_flags()

    def _get_atr(self, state: SymbolState, bars=None) -> float | None:
        """ATR fallback zinciri — sadece SL buffer için kullanılır."""
        import config as cfg

        atr_map = getattr(cfg, "ATR_MAP", {})
        if state.symbol in atr_map:
            return float(atr_map[state.symbol])
        default = getattr(cfg, "DEFAULT_ATR", None)
        if default is not None:
            return float(default)
        return None

    def _handle_ltf(self, state: SymbolState, event: dict):
        if state.state != SetupState.WAIT_CONFIRM:
            logger.debug(
                "[LTF-SKIP] %s state=%s — LTF sadece WAIT_CONFIRM'de kabul edilir",
                state.symbol,
                state.state,
            )
            return

        if state.fvg_upper is None or state.fvg_lower is None:
            logger.warning("[%s] LTF confirm geldi ama FVG seviyeleri yok — atlandı", state.symbol)
            return

        logger.info("[LTF] %s | dir=%s | state=%s", state.symbol, event.get("direction"), state.state)

        # Giriş anında pen tekrar kontrol et
        engine = PenetrationEngine(state.fvg_upper, state.fvg_lower, state.direction)
        pen = engine.get_penetration(event.get("close", 0.0))

        if pen >= 1.0:
            self._do_invalidate(state, f"LTF geldi ama PEN >= %100 (pen={pen:.2f})")
            return

        state.ltf_confirmed = True
        state.entry_price = event.get("close")
        state.state = SetupState.READY_TO_ENTER
        logger.info("[%s] ✅ LTF CONFIRM → READY_TO_ENTER | pen=%.2f", state.symbol, pen)

    def _handle_htf_bias(self, state: SymbolState, event: dict):
        """
        [V4] HTF_BIAS event handler — yeni strength değerleri.

        strength=STRONG     : D1=H4 uyumlu → normal akış
        strength=STRICT_WAIT: D1≠H4 uyumsuz → IDLE/ARMED ise düşür, üstü koru
        strength=RANGE_WAIT : H4/D1 konsolidasyon → ARMED ise IDLE
        strength=PENDING    : D1 inside bar → sadece log, state değiştirme
        strength=SKIP_DAY   : D1 outside bar → ENTERED/EXPIRED dışı hepsini IDLE
        strength=NONE       : D1 yetersiz veri → dokunma
        """
        new_direction = event.get("direction")  # None veya LONG/SHORT
        strength = event.get("strength", "NONE")

        # Her durumda htf metaverilerini güncelle
        state.htf_bias = new_direction
        state.d1_bias = event.get("d1_bias") or state.d1_bias
        state.h4_bias_val = event.get("h4_bias") or state.h4_bias_val
        state.htf_strength = strength

        # ── SKIP_DAY: o gün kesin pas ─────────────────────────────────────
        if strength == "SKIP_DAY":
            if state.state not in (SetupState.IDLE, SetupState.ENTERED, SetupState.EXPIRED):
                logger.warning(
                    "[%s] SKIP_DAY | D1 OUTSIDE_BAR → aktif setup IDLE'a düşürüldü (state=%s)",
                    state.symbol,
                    state.state,
                )
                state.state = SetupState.IDLE
                state.reset_flags()
            else:
                logger.info("[%s] SKIP_DAY | D1 OUTSIDE_BAR — işlem yok", state.symbol)
            return

        # ── STRICT_WAIT: D1≠H4 uyumsuzluğu ──────────────────────────────
        if strength == "STRICT_WAIT":
            if state.state == SetupState.IDLE:
                logger.warning(
                    "[%s] STRICT_WAIT | D1≠H4 divergence — yeni setup engellendi",
                    state.symbol,
                )
            elif state.state == SetupState.ARMED:
                logger.warning(
                    "[%s] STRICT_WAIT | ARMED state'te D1≠H4 → IDLE",
                    state.symbol,
                )
                state.state = SetupState.IDLE
                state.reset_flags()
            else:
                # WAIT_RETRACE ve sonrası: mevcut setup korunur
                logger.warning(
                    "[%s] STRICT_WAIT | aktif setup korunuyor (state=%s) — yeni setup engelli",
                    state.symbol,
                    state.state,
                )
            return

        # ── RANGE_WAIT: konsolidasyon ─────────────────────────────────────
        if strength == "RANGE_WAIT":
            if state.state == SetupState.IDLE:
                logger.warning("[%s] RANGE_WAIT | H4 konsolidasyon — bekleniyor", state.symbol)
            elif state.state == SetupState.ARMED:
                logger.warning(
                    "[%s] RANGE_WAIT | ARMED'ta H4 konsolidasyon → IDLE",
                    state.symbol,
                )
                state.state = SetupState.IDLE
                state.reset_flags()
            else:
                logger.warning(
                    "[%s] RANGE_WAIT | aktif setup korunuyor (state=%s)",
                    state.symbol,
                    state.state,
                )
            return

        # ── PENDING: D1 inside bar → sadece log ──────────────────────────
        if strength == "PENDING":
            logger.info(
                "[%s] D1_PENDING | inside bar — 1H key level izleniyor (state=%s)",
                state.symbol,
                state.state,
            )
            return

        # ── NONE: yetersiz veri → dokunma ────────────────────────────────
        if strength == "NONE":
            logger.debug("[%s] HTF_BIAS strength=NONE — dokunulmadı", state.symbol)
            return

        # ── STRONG: D1=H4 uyumlu → normal akış ──────────────────────────
        # [FIX-8] Sadece IDLE ve ARMED'da direction override et
        if state.state in (SetupState.IDLE, SetupState.ARMED):
            state.direction = new_direction
        elif new_direction is not None and state.direction != new_direction:
            logger.warning(
                "[%s] HTF bias değişti ama direction override edilmedi: %s → %s (state=%s)",
                state.symbol,
                state.direction,
                new_direction,
                state.state,
            )

        logger.debug("[%s] HTF bias set → %s (%s)", state.symbol, state.htf_bias, state.htf_strength)

    def _handle_htf_levels(self, state: SymbolState, event: dict):
        # Sadece IDLE ve ARMED'de HTF seviyelerini güncelle — diğer state'lerde
        # entry anındaki SL/TP korunur.
        if state.state in (SetupState.IDLE, SetupState.ARMED):
            state.h4_swing_level = event.get("h4_swing_level")
            state.h1_liquidity_level = event.get("h1_liquidity_level")
        logger.debug(
            "[%s] HTF levels — h4_sl=%s h1_tp=%s",
            state.symbol,
            state.h4_swing_level,
            state.h1_liquidity_level,
        )

    # ─────────────────────────────────────────
    # DECISION LAYER
    # ─────────────────────────────────────────

    def _check_stale_state(self, state: SymbolState, current_time: datetime) -> bool:
        stale_states = ["ARMED", "WAIT_RETRACE", "WAIT_CONFIRM"]
        if state.state in stale_states:
            if state.expires_at is not None and current_time.timestamp() > state.expires_at:
                logger.warning(
                    "[%s] ZOMBİ SETUP TEMİZLENDİ | State=%s | expires_at aşıldı → IDLE",
                    state.symbol,
                    state.state,
                )
                state.state = SetupState.IDLE
                state.reset_flags()
                return True
        return False

    def _check_invalidation(self, state: SymbolState, last_closed_bar) -> bool:
        if last_closed_bar is None:
            return False

        # Zombi setup önleme — non-terminal state'lerde fiyat MSS seviyesini ihlal ederse IDLE'a düşür.
        if state.state not in (
            SetupState.ARMED,
            SetupState.WAIT_RETRACE,
            SetupState.WAIT_CONFIRM,
        ):
            return False

        mss_level = getattr(state, "mss_level", None)
        if mss_level is None:
            return False

        # Buffer: küçük geri çekilmeleri tolere et
        buffer = mss_level * 0.001

        if state.direction == "SHORT" and last_closed_bar.close > mss_level + buffer:
            logger.warning(
                "[%s] INVALIDATION | close=%.5f > SHORT MSS=%.5f + buffer → IDLE",
                state.symbol,
                last_closed_bar.close,
                mss_level,
            )
            state.state = SetupState.IDLE
            state.reset_flags()
            return True

        elif state.direction == "LONG" and last_closed_bar.close < mss_level - buffer:
            logger.warning(
                "[%s] INVALIDATION | close=%.5f < LONG MSS=%.5f - buffer → IDLE",
                state.symbol,
                last_closed_bar.close,
                mss_level,
            )
            state.state = SetupState.IDLE
            state.reset_flags()
            return True

        return False

    def _evaluate(self, state: SymbolState, current_time: datetime | None = None, last_closed_bar=None):
        if current_time is None:
            current_time = datetime.now()

        # Terminal state'lerde hiçbir şey yapma — debug log bile basma
        if state.state in (SetupState.INVALIDATED, SetupState.ENTERED, SetupState.EXPIRED):
            return

        if self._check_stale_state(state, current_time):
            return
        if self._check_invalidation(state, last_closed_bar):
            return

        old_state = state.state

        logger.debug(
            "[EVALUATE] %s | sweep=%s mss=%s is_ce_tap=%s ltf=%s | state=%s",
            state.symbol,
            state.sweep_detected,
            state.mss_confirmed,
            state.is_ce_tap,
            state.ltf_confirmed,
            state.state,
        )

        # ── V40 SNIPER GATE: Tek giriş kapısı ──────────────────────────
        # Koşul: sweep + mss + is_ce_tap (pen >= %15) + ltf_confirmed + WAIT_CONFIRM
        if (
            state.sweep_detected
            and state.mss_confirmed
            and state.is_ce_tap
            and state.ltf_confirmed
            and state.state == SetupState.WAIT_CONFIRM
        ):
            state.state = SetupState.READY_TO_ENTER
            logger.critical(
                "[%s] 🎯 SNIPER GATE — ALL CONDITIONS MET → READY_TO_ENTER (%s)",
                state.symbol,
                state.direction,
            )
            return

        if old_state != state.state:
            logger.info("[STATE] %s: %s → %s", state.symbol, old_state, state.state)

    # ─────────────────────────────────────────
    # CLEANUP & MANUAL MANIPULATION
    # ─────────────────────────────────────────

    def set_state(self, symbol: str, new_state: SetupState):
        state = self.get(symbol)
        old_state = state.state
        state.state = new_state
        logger.info("[%s] State geçişi: %s → %s", symbol, old_state, new_state)

    def invalidate(self, symbol: str):
        state = self.get(symbol)
        self._do_invalidate(state, "Manual invalidate() call")

    def clear(self, symbol: str):
        if symbol in self.symbols:
            del self.symbols[symbol]
