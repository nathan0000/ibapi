"""
EOD Manager
Monitors time and automatically closes all ES futures and options positions 
before market close. Runs on a background thread with multiple safety checkpoints.
"""

import logging
import threading
import time
from datetime import datetime, time as dtime
from typing import Optional, List
from zoneinfo import ZoneInfo

log = logging.getLogger("EODManager")

# US Eastern timezone
ET = ZoneInfo("America/New_York")


def get_et_time() -> dtime:
    """Return current time in US Eastern."""
    return datetime.now(ET).time()


def parse_time(time_str: str) -> dtime:
    """Parse 'HH:MM' string to time object."""
    h, m = map(int, time_str.split(":"))
    return dtime(h, m)


class EODManager:
    """
    Manages end-of-day position closure with multiple safety levels.

    Timeline (ET):
      15:45 - Warning: log open positions
      15:50 - Begin closing with limit orders
      15:55 - Force close with market orders
      16:00 - Final sweep for any remaining
    """

    CHECK_INTERVAL = 10    # Check every 10 seconds

    def __init__(self, app):
        self.app = app
        self.config = app.config.eod
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Closure state
        self._warning_issued = False
        self._close_started = False
        self._force_close_done = False
        self._positions_closed = False

        # Parse configured times
        self._warning_time = parse_time(self.config.warning_time)
        self._close_time = parse_time(self.config.close_time)
        self._hard_close_time = parse_time(self.config.hard_close_time)
        self._futures_close_time = parse_time(self.config.futures_close_time)

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="EODManager",
            daemon=True
        )
        self._thread.start()
        log.info("EOD Manager started.")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                self._check_eod()
            except Exception as e:
                log.error(f"EOD check error: {e}", exc_info=True)
            time.sleep(self.CHECK_INTERVAL)

    def _check_eod(self):
        """Check if EOD actions are needed based on current time."""
        now = get_et_time()
        
        # Skip outside market hours (no need to run overnight)
        if now < dtime(9, 0) or now > dtime(17, 0):
            return

        # 15:45 - Warning
        if now >= self._warning_time and not self._warning_issued:
            self._warning_issued = True
            self._issue_warning()

        # 15:50 - Begin close
        if now >= self._close_time and not self._close_started:
            self._close_started = True
            self._begin_close()

        # 15:55 - Force close
        if now >= self._hard_close_time and not self._force_close_done:
            self._force_close_done = True
            self._force_close()

        # 16:00 - Final sweep
        if now >= self._futures_close_time and not self._positions_closed:
            self._positions_closed = True
            remaining = self._get_closeable_positions()
            if remaining:
                log.error(f"FINAL EOD SWEEP: {len(remaining)} positions still open!")
                self._close_positions_market(remaining)

    def _issue_warning(self):
        """Log warning about open positions."""
        if not self.app.positions:
            return
        positions = self.app.positions.get_all_closeable()
        if not positions:
            log.info("EOD Check 15:45: No open positions to close.")
            return

        log.warning(f"EOD WARNING (15:45): {len(positions)} positions open:")
        for p in positions:
            log.warning(f"  {p.symbol} {p.sec_type} qty={p.quantity:.0f} "
                        f"pnl={p.unrealized_pnl:.2f}")

    def _begin_close(self):
        """Start closing positions with limit orders (15:50)."""
        positions = self._get_closeable_positions()
        if not positions:
            log.info("EOD Close 15:50: No positions to close.")
            return

        log.warning(f"EOD CLOSE STARTING (15:50): Closing {len(positions)} positions")

        # Cancel all open orders first
        if self.config.cancel_open_orders_first and self.app.orders:
            self.app.orders.cancel_all_orders()
            time.sleep(0.5)

        for pos in positions:
            try:
                self._close_position(pos, use_market=False)
            except Exception as e:
                log.error(f"Error closing {pos.symbol}: {e}")

    def _force_close(self):
        """Force close any remaining positions with market orders (15:55)."""
        positions = self._get_closeable_positions()
        if not positions:
            log.info("EOD Force Close 15:55: No remaining positions.")
            return

        log.error(f"EOD FORCE CLOSE (15:55): {len(positions)} positions still open - using MARKET orders!")
        self._close_positions_market(positions)

    def emergency_close_all(self):
        """Emergency closure - called by risk manager on limit breach."""
        positions = self._get_closeable_positions()
        if not positions:
            log.info("Emergency close: no positions to close.")
            return

        log.error(f"EMERGENCY CLOSE: Closing {len(positions)} positions NOW")

        if self.app.orders:
            self.app.orders.cancel_all_orders()
            time.sleep(0.5)

        self._close_positions_market(positions)

    def _close_positions_market(self, positions: List):
        """Close all positions using market orders."""
        for pos in positions:
            try:
                self._close_position(pos, use_market=True)
            except Exception as e:
                log.error(f"Error in emergency close {pos.symbol}: {e}")

    def _close_position(self, position, use_market: bool = True):
        """Close a single position."""
        if not self.app.orders or not self.app.market_data:
            return

        action = "SELL" if position.quantity > 0 else "BUY"
        qty = abs(position.quantity)

        log.info(f"EOD closing: {action} {qty:.0f} {position.symbol} "
                 f"({'MKT' if use_market else 'LMT'})")

        if use_market or self.config.use_market_orders_eod:
            self.app.orders.submit_market(
                position.contract, action, qty, tag="EOD_CLOSE"
            )
        else:
            # Use aggressive limit (slightly inside market)
            price = position.market_price or position.avg_cost
            tick = 0.25 if position.sec_type == "FUT" else 0.05
            limit = price - tick if action == "SELL" else price + tick
            self.app.orders.submit_limit(
                position.contract, action, qty, limit, tag="EOD_CLOSE_LMT"
            )

    def _get_closeable_positions(self) -> List:
        """Get all positions that need to be closed at EOD."""
        if not self.app.positions:
            return []
        return self.app.positions.get_all_closeable()

    def reset_daily_state(self):
        """Reset for new trading day."""
        with self._lock:
            self._warning_issued = False
            self._close_started = False
            self._force_close_done = False
            self._positions_closed = False
        log.info("EOD Manager: daily state reset.")

    @property
    def is_market_hours(self) -> bool:
        now = get_et_time()
        return dtime(9, 30) <= now <= dtime(16, 0)

    def get_status(self) -> dict:
        now = get_et_time()
        return {
            "current_time_et": now.strftime("%H:%M:%S"),
            "market_hours": self.is_market_hours,
            "warning_issued": self._warning_issued,
            "close_started": self._close_started,
            "force_close_done": self._force_close_done,
            "positions_closed": self._positions_closed,
        }
