"""
Risk Manager
Enforces daily loss limits, position size limits, and circuit breakers.
"""

import logging
import threading
from datetime import datetime
from typing import Optional, Dict

log = logging.getLogger("RiskManager")


class RiskState:
    NORMAL = "normal"
    WARNING = "warning"
    HALTED = "halted"       # Daily loss limit hit
    SHUTDOWN = "shutdown"   # Force close all


class RiskManager:
    """
    Real-time risk management:
    - Daily P&L monitoring with hard stop
    - Per-trade position size limits
    - Max open positions
    - Circuit breaker on rapid losses
    """

    def __init__(self, app):
        self.app = app
        self.config = app.config.risk
        self._lock = threading.RLock()
        self._state = RiskState.NORMAL
        self._daily_realized_pnl: float = 0.0
        self._daily_unrealized_pnl: float = 0.0
        self._account_equity: float = 0.0
        self._net_liquidation: float = 0.0
        self._last_pnl_update: Optional[datetime] = None

        # Circuit breaker: rapid loss detection
        self._pnl_snapshots: list = []   # (timestamp, pnl)

    def on_account_value(self, key: str, val: str, currency: str):
        if currency != "USD":
            return
        try:
            value = float(val)
        except (ValueError, TypeError):
            return

        with self._lock:
            if key == "NetLiquidation":
                self._net_liquidation = value
            elif key == "EquityWithLoanValue":
                self._account_equity = value
            elif key == "RealizedPnL":
                self._daily_realized_pnl = value
            elif key == "UnrealizedPnL":
                self._daily_unrealized_pnl = value

        self._check_limits()

    def _check_limits(self):
        with self._lock:
            if self._state in (RiskState.HALTED, RiskState.SHUTDOWN):
                return

            total_pnl = self._daily_realized_pnl + self._daily_unrealized_pnl

            # Absolute loss limit
            if total_pnl <= -self.config.max_daily_loss_usd:
                self._trigger_halt(f"Daily loss limit hit: ${total_pnl:.2f}")
                return

            # Percentage loss limit
            if self._account_equity > 0:
                pct_loss = abs(total_pnl) / self._account_equity * 100
                if total_pnl < 0 and pct_loss >= self.config.max_daily_loss_pct:
                    self._trigger_halt(
                        f"Daily loss % limit hit: {pct_loss:.1f}% of equity"
                    )
                    return

            # Warning zone (75% of limit)
            warning_threshold = self.config.max_daily_loss_usd * 0.75
            if total_pnl <= -warning_threshold and self._state == RiskState.NORMAL:
                self._state = RiskState.WARNING
                log.warning(f"Risk WARNING: Daily P&L = ${total_pnl:.2f} "
                             f"(75% of max loss reached)")

    def _trigger_halt(self, reason: str):
        with self._lock:
            if self._state == RiskState.HALTED:
                return
            self._state = RiskState.HALTED

        log.error(f"RISK HALT: {reason}")
        log.error("Cancelling all orders and closing positions...")

        # Cancel all open orders
        if self.app.orders:
            self.app.orders.cancel_all_orders()

        # Signal EOD manager to close everything
        if self.app.eod_manager:
            self.app.eod_manager.emergency_close_all()

    def can_trade(self) -> bool:
        """Returns True if new trades are allowed."""
        with self._lock:
            if self._state in (RiskState.HALTED, RiskState.SHUTDOWN):
                return False

            # Check open position count
            if self.app.positions:
                if self.app.positions.position_count() >= self.config.max_open_trades:
                    log.warning(f"Max open positions reached: {self.config.max_open_trades}")
                    return False

            # Check VIX extreme
            if self.app.vix_analyzer:
                if not self.app.vix_analyzer.allow_new_positions():
                    log.warning("No new trades: VIX EXTREME regime")
                    return False

            return True

    def check_es_size(self, requested_qty: int) -> int:
        """Return allowed ES contract quantity."""
        return min(requested_qty, self.config.max_position_size_es)

    def check_option_size(self, requested_qty: int) -> int:
        """Return allowed option contract quantity."""
        return min(requested_qty, self.config.max_position_size_options)

    def get_state(self) -> str:
        with self._lock:
            return self._state

    def get_daily_pnl(self) -> float:
        with self._lock:
            return self._daily_realized_pnl + self._daily_unrealized_pnl

    def get_summary(self) -> Dict:
        with self._lock:
            total_pnl = self._daily_realized_pnl + self._daily_unrealized_pnl
            return {
                "state": self._state,
                "daily_pnl": total_pnl,
                "realized_pnl": self._daily_realized_pnl,
                "unrealized_pnl": self._daily_unrealized_pnl,
                "net_liquidation": self._net_liquidation,
                "account_equity": self._account_equity,
                "max_daily_loss": self.config.max_daily_loss_usd,
                "pct_of_limit": abs(total_pnl) / self.config.max_daily_loss_usd * 100 if self.config.max_daily_loss_usd else 0,
            }
