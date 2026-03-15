"""
State Persistence
Saves and restores session state to handle crashes and restarts.
Tracks: filled orders today, open positions, daily P&L baseline,
VIX history (for IV rank continuity), strategy trade counts.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Dict, List, Any, Optional

log = logging.getLogger("StatePersistence")

STATE_DIR = "state"


@dataclass
class SessionState:
    """Snapshot of session state for persistence."""
    date:                str
    account_id:          str   = ""
    baseline_pnl:        float = 0.0    # PnL at session start (for daily calculation)
    vix_history:         List[float] = field(default_factory=list)
    strategy_trade_counts: Dict[str, int] = field(default_factory=dict)
    last_saved:          str   = ""


class StatePersistence:
    """
    Saves and loads session state to JSON files.
    Auto-saves every N seconds on a background thread.
    """

    SAVE_INTERVAL = 30   # seconds between auto-saves

    def __init__(self, app, state_dir: str = STATE_DIR):
        self.app        = app
        self._state_dir = state_dir
        self._lock      = threading.Lock()
        self._running   = False
        self._thread: Optional[threading.Thread] = None

        os.makedirs(state_dir, exist_ok=True)

    @property
    def _state_file(self) -> str:
        today = date.today().strftime("%Y%m%d")
        return os.path.join(self._state_dir, f"session_{today}.json")

    @property
    def _vix_history_file(self) -> str:
        return os.path.join(self._state_dir, "vix_history.json")

    # ── SAVE ──────────────────────────────────────────────────────────────

    def start(self):
        """Start auto-save background thread."""
        self._running = True
        self._thread  = threading.Thread(
            target=self._save_loop, name="StatePersist", daemon=True
        )
        self._thread.start()
        log.info("State persistence started.")

    def stop(self):
        self._running = False
        self.save_now()   # Final save on stop

    def _save_loop(self):
        import time
        while self._running:
            time.sleep(self.SAVE_INTERVAL)
            try:
                self.save_now()
            except Exception as e:
                log.error(f"Auto-save error: {e}")

    def save_now(self):
        """Snapshot and write current state immediately."""
        state = self._build_state()
        self._write_json(self._state_file, asdict(state))
        # Also persist VIX history separately (used across sessions for IV rank)
        self._save_vix_history()
        log.debug(f"State saved to {self._state_file}")

    def _build_state(self) -> SessionState:
        app  = self.app
        pnl  = 0.0
        vix_hist: List[float] = []
        trade_counts: Dict[str, int] = {}
        acct = ""

        if app.pnl:
            snap = app.pnl.get_snapshot()
            pnl  = snap.get("total_pnl", 0.0)

        if app.vix_analyzer:
            vix_hist = list(app.vix_analyzer._vix_history)

        if app.strategy:
            for s in app.strategy._strategies:
                trade_counts[s.name] = s._trade_count_today

        if app.config:
            acct = app.config.account_id

        return SessionState(
            date       = str(date.today()),
            account_id = acct,
            baseline_pnl = pnl,
            vix_history  = vix_hist,
            strategy_trade_counts = trade_counts,
            last_saved   = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _save_vix_history(self):
        if not self.app.vix_analyzer:
            return
        history = list(self.app.vix_analyzer._vix_history)
        data = {
            "date":    str(date.today()),
            "history": history[-252:]   # Keep last 252 bars (1 trading year)
        }
        self._write_json(self._vix_history_file, data)

    # ── LOAD ──────────────────────────────────────────────────────────────

    def load(self) -> Optional[SessionState]:
        """Load today's saved state if it exists."""
        if not os.path.exists(self._state_file):
            log.info("No saved state for today — starting fresh.")
            return None

        try:
            data  = self._read_json(self._state_file)
            state = SessionState(**{
                k: data.get(k, v)
                for k, v in asdict(SessionState(date="")).items()
            })
            state.date = data.get("date", str(date.today()))

            if state.date != str(date.today()):
                log.info("Saved state is from a previous day — ignoring.")
                return None

            log.info(f"Loaded session state from {self._state_file} "
                     f"(saved {state.last_saved})")
            return state
        except Exception as e:
            log.warning(f"Could not load saved state: {e}")
            return None

    def load_vix_history(self) -> List[float]:
        """Load historical VIX data from disk."""
        if not os.path.exists(self._vix_history_file):
            return []
        try:
            data = self._read_json(self._vix_history_file)
            history = data.get("history", [])
            log.info(f"Loaded {len(history)} VIX history points for IV rank calculation")
            return history
        except Exception as e:
            log.warning(f"Could not load VIX history: {e}")
            return []

    def restore(self, state: SessionState):
        """Apply loaded state to running subsystems."""
        if not state:
            return

        # Restore VIX history
        if self.app.vix_analyzer and state.vix_history:
            self.app.vix_analyzer._vix_history.extend(state.vix_history)
            log.info(f"Restored {len(state.vix_history)} VIX history points")

        # Restore strategy trade counts
        if self.app.strategy and state.strategy_trade_counts:
            for s in self.app.strategy._strategies:
                count = state.strategy_trade_counts.get(s.name, 0)
                s._trade_count_today = count
            log.info(f"Restored strategy trade counts: {state.strategy_trade_counts}")

        log.info("Session state restored successfully.")

    # ── HELPERS ───────────────────────────────────────────────────────────

    def _write_json(self, path: str, data: Any):
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)   # Atomic rename
        except Exception as e:
            log.error(f"Write error {path}: {e}")
            if os.path.exists(tmp):
                os.remove(tmp)

    def _read_json(self, path: str) -> Any:
        with open(path) as f:
            return json.load(f)

    def cleanup_old_states(self, keep_days: int = 30):
        """Remove state files older than keep_days."""
        cutoff = date.today().toordinal() - keep_days
        removed = 0
        for fname in os.listdir(self._state_dir):
            if fname.startswith("session_") and fname.endswith(".json"):
                try:
                    date_str = fname[8:16]   # session_YYYYMMDD.json
                    file_date = date(
                        int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
                    ).toordinal()
                    if file_date < cutoff:
                        os.remove(os.path.join(self._state_dir, fname))
                        removed += 1
                except (ValueError, OSError):
                    pass
        if removed:
            log.info(f"Cleaned up {removed} old state files")
