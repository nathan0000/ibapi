"""
VIX Analyzer
Real-time VIX regime detection, IV rank calculation, and 
volatility-based position sizing adjustments.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, Dict, Tuple, Deque

log = logging.getLogger("VIXAnalyzer")


class VIXRegime:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"

    @staticmethod
    def color(regime: str) -> str:
        return {
            "low": "GREEN",
            "medium": "YELLOW",
            "high": "ORANGE",
            "extreme": "RED",
        }.get(regime, "WHITE")

    @staticmethod
    def emoji(regime: str) -> str:
        return {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "extreme": "🔴",
        }.get(regime, "⚪")


class VIXSnapshot:
    """Immutable snapshot of VIX state at a point in time."""
    __slots__ = ["value", "regime", "iv_rank", "iv_percentile",
                 "size_mult", "stop_mult", "timestamp"]

    def __init__(self, value: float, regime: str, iv_rank: float,
                 iv_percentile: float, size_mult: float, stop_mult: float):
        self.value = value
        self.regime = regime
        self.iv_rank = iv_rank
        self.iv_percentile = iv_percentile
        self.size_mult = size_mult
        self.stop_mult = stop_mult
        self.timestamp = datetime.now()

    def __str__(self):
        return (f"VIX={self.value:.2f} | Regime={self.regime.upper()} "
                f"{VIXRegime.emoji(self.regime)} | "
                f"IVRank={self.iv_rank:.0f}% | "
                f"SizeMult={self.size_mult:.1f}x")


class VIXAnalyzer:
    """
    Monitors VIX in real-time and classifies market volatility regime.
    Provides position sizing and risk multipliers based on regime.
    """

    UPDATE_INTERVAL = 5        # seconds between state updates
    HISTORY_DAYS = 252         # trading days for IV rank (1 year)
    HYSTERESIS_TICKS = 0.5     # prevent rapid regime flipping

    def __init__(self, app):
        self.app = app
        self.config = app.config.vix

        self._current: Optional[VIXSnapshot] = None
        self._previous_regime: Optional[str] = None
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Historical VIX data for IV rank
        self._vix_history: Deque[float] = deque(maxlen=self.HISTORY_DAYS)
        self._vix_52w_high: float = 0.0
        self._vix_52w_low: float = float("inf")

        # Regime change callbacks
        self._regime_callbacks = []

        # Statistics
        self._regime_durations: Dict[str, float] = {
            "low": 0.0, "medium": 0.0, "high": 0.0, "extreme": 0.0
        }
        self._last_regime_change: Optional[datetime] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._update_loop,
            name="VIXAnalyzer",
            daemon=True
        )
        self._thread.start()
        log.info("VIX Analyzer started.")

    def stop(self):
        self._running = False

    def on_regime_change(self, callback):
        """Register callback for regime transitions: cb(old, new, vix_value)"""
        self._regime_callbacks.append(callback)

    # ─────────────────────────────────────────────────────────────────────
    # MAIN UPDATE LOOP
    # ─────────────────────────────────────────────────────────────────────

    def _update_loop(self):
        while self._running:
            try:
                self._update()
            except Exception as e:
                log.error(f"VIX update error: {e}", exc_info=True)
            time.sleep(self.UPDATE_INTERVAL)

    def _update(self):
        """Fetch current VIX and update regime."""
        if not self.app.market_data:
            return

        vix = self.app.market_data.get_vix()
        if vix <= 0:
            return

        # Update history
        self._vix_history.append(vix)
        self._vix_52w_high = max(self._vix_52w_high, vix)
        if len(self._vix_history) > 1:
            self._vix_52w_low = min(self._vix_history)

        # Calculate IV rank and percentile
        iv_rank = self._calc_iv_rank(vix)
        iv_pct = self._calc_iv_percentile(vix)

        # Classify regime (with hysteresis)
        regime = self._classify_regime(vix)

        # Get multipliers
        size_mult = self.config.size_multipliers.get(regime, 1.0)
        stop_mult = self.config.stop_multipliers.get(regime, 1.0)

        snapshot = VIXSnapshot(vix, regime, iv_rank, iv_pct, size_mult, stop_mult)

        with self._lock:
            old_regime = self._current.regime if self._current else None
            self._current = snapshot

            if old_regime != regime:
                self._on_regime_change(old_regime, regime, vix)

        log.debug(str(snapshot))

    def _classify_regime(self, vix: float) -> str:
        """Classify VIX into regime with hysteresis to prevent flipping."""
        current_regime = self._current.regime if self._current else None
        cfg = self.config

        # Base classification
        if vix < cfg.low_max:
            new_regime = VIXRegime.LOW
        elif vix < cfg.medium_max:
            new_regime = VIXRegime.MEDIUM
        elif vix < cfg.high_max:
            new_regime = VIXRegime.HIGH
        else:
            new_regime = VIXRegime.EXTREME

        # Apply hysteresis (don't change if within buffer zone)
        if current_regime and current_regime != new_regime:
            thresholds = {
                (VIXRegime.LOW, VIXRegime.MEDIUM): cfg.low_max,
                (VIXRegime.MEDIUM, VIXRegime.LOW): cfg.low_max,
                (VIXRegime.MEDIUM, VIXRegime.HIGH): cfg.medium_max,
                (VIXRegime.HIGH, VIXRegime.MEDIUM): cfg.medium_max,
                (VIXRegime.HIGH, VIXRegime.EXTREME): cfg.high_max,
                (VIXRegime.EXTREME, VIXRegime.HIGH): cfg.high_max,
            }
            threshold = thresholds.get((current_regime, new_regime), 0)
            if threshold > 0:
                if abs(vix - threshold) < self.HYSTERESIS_TICKS:
                    return current_regime  # Stay in current regime

        return new_regime

    def _calc_iv_rank(self, vix: float) -> float:
        """IV Rank: where current VIX falls in 52-week range (0-100)."""
        if len(self._vix_history) < 10:
            return 50.0
        lo = min(self._vix_history)
        hi = max(self._vix_history)
        if hi == lo:
            return 50.0
        return (vix - lo) / (hi - lo) * 100.0

    def _calc_iv_percentile(self, vix: float) -> float:
        """IV Percentile: % of days with VIX below current level (0-100)."""
        if len(self._vix_history) < 10:
            return 50.0
        below = sum(1 for v in self._vix_history if v < vix)
        return below / len(self._vix_history) * 100.0

    def _on_regime_change(self, old_regime: Optional[str],
                           new_regime: str, vix: float):
        """Handle regime transition."""
        if old_regime:
            log.warning(
                f"VIX REGIME CHANGE: {old_regime.upper()} → {new_regime.upper()} "
                f"(VIX={vix:.2f}) {VIXRegime.emoji(new_regime)}"
            )
            # Track duration
            if self._last_regime_change:
                elapsed = (datetime.now() - self._last_regime_change).total_seconds()
                self._regime_durations[old_regime] += elapsed

        self._previous_regime = old_regime
        self._last_regime_change = datetime.now()

        # Fire callbacks
        for cb in self._regime_callbacks:
            try:
                cb(old_regime, new_regime, vix)
            except Exception as e:
                log.error(f"Regime change callback error: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC INTERFACE
    # ─────────────────────────────────────────────────────────────────────

    @property
    def current(self) -> Optional[VIXSnapshot]:
        with self._lock:
            return self._current

    @property
    def regime(self) -> str:
        snap = self.current
        return snap.regime if snap else VIXRegime.MEDIUM

    @property
    def vix_value(self) -> float:
        snap = self.current
        return snap.value if snap else 0.0

    @property
    def size_multiplier(self) -> float:
        snap = self.current
        return snap.size_mult if snap else 1.0

    @property
    def stop_multiplier(self) -> float:
        snap = self.current
        return snap.stop_mult if snap else 1.0

    @property
    def iv_rank(self) -> float:
        snap = self.current
        return snap.iv_rank if snap else 50.0

    def allow_new_positions(self) -> bool:
        """Returns False during extreme volatility."""
        return self.regime != VIXRegime.EXTREME

    def adjust_size(self, base_size: int) -> int:
        """Scale position size by VIX regime multiplier."""
        return max(1, int(base_size * self.size_multiplier))

    def adjust_stop(self, base_stop_ticks: int) -> int:
        """Widen stop loss in high volatility."""
        return max(base_stop_ticks, int(base_stop_ticks * self.stop_multiplier))

    def get_strategy_bias(self) -> Dict[str, bool]:
        """Return strategy preferences for current regime."""
        regime = self.regime
        return {
            "favor_short_premium": regime in (VIXRegime.HIGH, VIXRegime.EXTREME),
            "favor_long_premium": regime == VIXRegime.LOW,
            "use_directional": regime in (VIXRegime.LOW, VIXRegime.MEDIUM),
            "reduce_gamma_risk": regime in (VIXRegime.HIGH, VIXRegime.EXTREME),
            "allow_new_trades": regime != VIXRegime.EXTREME,
        }

    def get_summary(self) -> str:
        snap = self.current
        if not snap:
            return "VIX: No data"
        return str(snap)

    def get_statistics(self) -> Dict:
        """Return analyzer statistics."""
        snap = self.current
        return {
            "current_vix": snap.value if snap else 0.0,
            "regime": snap.regime if snap else "unknown",
            "iv_rank": snap.iv_rank if snap else 0.0,
            "iv_percentile": snap.iv_percentile if snap else 0.0,
            "size_multiplier": snap.size_mult if snap else 1.0,
            "history_days": len(self._vix_history),
            "52w_high": self._vix_52w_high,
            "52w_low": self._vix_52w_low if self._vix_52w_low < float("inf") else 0.0,
            "regime_durations": dict(self._regime_durations),
        }
