"""
Reconnection Handler
Manages automatic reconnection to TWS/Gateway with exponential backoff.
Re-subscribes market data and requests account updates after reconnect.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger("Reconnect")


class ReconnectHandler:
    """
    Handles connection loss and automatic reconnection.
    Uses exponential backoff: 5s, 10s, 20s, 40s … up to 120s.
    """

    MIN_DELAY  = 5.0
    MAX_DELAY  = 120.0
    MULTIPLIER = 2.0
    MAX_ATTEMPTS = 20

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._attempt_count  = 0
        self._current_delay  = self.MIN_DELAY
        self._reconnecting   = False
        self._last_connected: Optional[datetime] = None
        self._last_disconnect: Optional[datetime] = None
        self._total_reconnects = 0

    def on_disconnect(self):
        """Call when connection drops."""
        with self._lock:
            if self._reconnecting:
                return
            self._reconnecting = True
            self._last_disconnect = datetime.now()

        log.warning("Connection lost — starting reconnect sequence")
        t = threading.Thread(target=self._reconnect_loop,
                             name="Reconnect", daemon=True)
        t.start()

    def _reconnect_loop(self):
        """Attempt reconnection with exponential backoff."""
        cfg = self.app.config
        self._current_delay = self.MIN_DELAY

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._attempt_count = attempt
            log.info(f"Reconnect attempt {attempt}/{self.MAX_ATTEMPTS} "
                     f"in {self._current_delay:.0f}s...")
            time.sleep(self._current_delay)

            # Grow delay for next attempt
            self._current_delay = min(
                self._current_delay * self.MULTIPLIER, self.MAX_DELAY
            )

            try:
                log.info(f"Connecting to {cfg.host}:{cfg.port} "
                         f"(clientId={cfg.client_id + attempt})")
                self.app.disconnect()
                time.sleep(1)

                # Use incremented client ID to avoid "already connected" errors
                self.app.connect(cfg.host, cfg.port, cfg.client_id + attempt)

                # Wait for nextValidId
                timeout = 10
                start = time.time()
                while not self.app.is_connected() and time.time() - start < timeout:
                    time.sleep(0.2)

                if self.app.is_connected():
                    self._on_reconnect_success(attempt)
                    return

            except Exception as e:
                log.error(f"Reconnect attempt {attempt} failed: {e}")

        log.error(f"Failed to reconnect after {self.MAX_ATTEMPTS} attempts. "
                  "Manual intervention required.")
        with self._lock:
            self._reconnecting = False

    def _on_reconnect_success(self, attempt: int):
        """Restore subscriptions after successful reconnect."""
        self._last_connected = datetime.now()
        self._total_reconnects += 1
        elapsed = (self._last_connected - self._last_disconnect).total_seconds()

        log.info(f"Reconnected successfully on attempt {attempt} "
                 f"(downtime: {elapsed:.0f}s, total reconnects: {self._total_reconnects})")

        # Re-subscribe market data
        if self.app.market_data:
            log.info("Re-subscribing market data...")
            self.app.market_data.subscribe_all()

        # Re-request account data
        log.info("Re-requesting account data...")
        self.app.reqAccountUpdates(True, self.app.config.account_id)

        # Restart strategy engine if it stopped
        if self.app.strategy and not self.app.strategy._running:
            self.app.strategy.start()

        with self._lock:
            self._reconnecting = False
            self._current_delay = self.MIN_DELAY
            self._attempt_count = 0

    @property
    def is_reconnecting(self) -> bool:
        with self._lock:
            return self._reconnecting

    def get_stats(self) -> dict:
        return {
            "total_reconnects": self._total_reconnects,
            "is_reconnecting": self._reconnecting,
            "attempt": self._attempt_count,
            "current_delay": self._current_delay,
            "last_connected": str(self._last_connected),
            "last_disconnect": str(self._last_disconnect),
        }
