"""
P&L Monitor
Real-time profit/loss tracking across all positions.
Generates intraday reports and tracks daily statistics.
"""

import logging
import threading
import time
from datetime import datetime, date
from dataclasses import dataclass, field
from collections import deque
from typing import Optional, Dict, List, Deque
from ibapi.contract import Contract
from ibapi.execution import Execution
from ibapi.commission_and_fees_report import CommissionAndFeesReport

log = logging.getLogger("PnLMonitor")


@dataclass
class TradeEntry:
    """Record of a completed round-trip trade."""
    symbol: str
    sec_type: str
    action: str
    quantity: float
    entry_price: float
    exit_price: float = 0.0
    commission: float = 0.0
    realized_pnl: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    tag: str = ""

    @property
    def multiplier(self) -> float:
        if self.sec_type == "FUT":  return 50.0
        if self.sec_type in ("OPT", "FOP"): return 100.0
        return 1.0

    @property
    def gross_pnl(self) -> float:
        return self.realized_pnl + self.commission


@dataclass
class DailyStats:
    date: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_pnl: float = 0.0
    commission: float = 0.0
    net_pnl: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    def update(self, trade: TradeEntry):
        self.total_trades += 1
        pnl = trade.realized_pnl
        self.gross_pnl += pnl
        self.commission += trade.commission
        self.net_pnl = self.gross_pnl - self.commission

        if pnl > 0:
            self.winning_trades += 1
            self.max_win = max(self.max_win, pnl)
        elif pnl < 0:
            self.losing_trades += 1
            self.max_loss = min(self.max_loss, pnl)

        if self.total_trades > 0:
            self.win_rate = self.winning_trades / self.total_trades * 100

        total_wins = sum(t.realized_pnl for t in [] if t.realized_pnl > 0)  # simplified
        total_losses = abs(sum(t.realized_pnl for t in [] if t.realized_pnl < 0))
        if total_losses > 0:
            self.profit_factor = total_wins / total_losses


class PnLMonitor:
    """
    Real-time P&L monitoring with logging and statistics.
    """

    LOG_INTERVAL = 60    # Log P&L summary every 60 seconds

    def __init__(self, app):
        self.app = app
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Account values
        self._net_liquidation: float = 0.0
        self._buying_power: float = 0.0
        self._unrealized_pnl: float = 0.0
        self._realized_pnl: float = 0.0

        # Position-level P&L tracking
        self._position_pnl: Dict[str, float] = {}   # symbol -> unrealized pnl
        self._position_rpnl: Dict[str, float] = {}  # symbol -> realized pnl

        # Execution tracking
        self._pending_executions: Dict[str, float] = {}  # execId -> price

        # Trade history
        self._trades_today: List[TradeEntry] = []
        self._daily_stats: DailyStats = DailyStats(date=str(date.today()))

        # P&L history for charting (last 500 data points)
        self._pnl_history: Deque[Dict] = deque(maxlen=500)

        # High-water mark for drawdown tracking
        self._session_peak_pnl: float = 0.0
        self._max_drawdown: float = 0.0

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._log_loop,
            name="PnLMonitor",
            daemon=True
        )
        self._thread.start()

    def stop(self):
        self._running = False

    def _log_loop(self):
        """Periodically log P&L summary."""
        while self._running:
            time.sleep(self.LOG_INTERVAL)
            try:
                self._log_summary()
            except Exception as e:
                log.error(f"P&L log error: {e}")

    def _log_summary(self):
        snap = self.get_snapshot()
        log.info(
            f"P&L | NLV: ${snap['net_liquidation']:>10,.2f} | "
            f"Unrealized: ${snap['unrealized_pnl']:>+8,.2f} | "
            f"Realized: ${snap['realized_pnl']:>+8,.2f} | "
            f"Total: ${snap['total_pnl']:>+8,.2f} | "
            f"Drawdown: ${snap['max_drawdown']:>.2f}"
        )

    def on_account_value(self, key: str, val: str, currency: str, account: str):
        try:
            value = float(val)
        except (ValueError, TypeError):
            return

        with self._lock:
            if key == "NetLiquidation" and currency == "USD":
                self._net_liquidation = value
            elif key == "BuyingPower" and currency == "USD":
                self._buying_power = value
            elif key == "UnrealizedPnL" and currency == "USD":
                self._unrealized_pnl = value
            elif key == "RealizedPnL" and currency == "USD":
                self._realized_pnl = value

            total_pnl = self._unrealized_pnl + self._realized_pnl
            self._session_peak_pnl = max(self._session_peak_pnl, total_pnl)
            drawdown = self._session_peak_pnl - total_pnl
            self._max_drawdown = max(self._max_drawdown, drawdown)

    def on_portfolio_update(self, contract: Contract,
                             unrealized_pnl: float, realized_pnl: float):
        key = f"{contract.symbol}_{contract.secType}"
        with self._lock:
            self._position_pnl[key] = unrealized_pnl
            self._position_rpnl[key] = realized_pnl

    def on_execution(self, contract: Contract, execution: Execution):
        """Track executions for P&L calculation."""
        with self._lock:
            self._pending_executions[execution.execId] = execution.price
        log.info(f"Execution recorded: {execution.execId} "
                 f"{execution.side} {execution.shares:.0f} "
                 f"{contract.symbol} @ {execution.price:.4f}")

    def on_commission(self, report: CommissionAndFeesReport):
        with self._lock:
            pnl = getattr(report, "realizedPNL", 0.0) or 0.0
            commission = getattr(report, "commission", 0.0) or 0.0
            if pnl != 0:
                log.debug(f"Realized PnL: ${pnl:.2f} commission: ${commission:.4f}")

    def get_snapshot(self) -> Dict:
        with self._lock:
            total = self._unrealized_pnl + self._realized_pnl
            return {
                "net_liquidation": self._net_liquidation,
                "buying_power": self._buying_power,
                "unrealized_pnl": self._unrealized_pnl,
                "realized_pnl": self._realized_pnl,
                "total_pnl": total,
                "session_peak": self._session_peak_pnl,
                "max_drawdown": self._max_drawdown,
                "daily_stats": {
                    "trades": self._daily_stats.total_trades,
                    "win_rate": self._daily_stats.win_rate,
                    "gross_pnl": self._daily_stats.gross_pnl,
                    "net_pnl": self._daily_stats.net_pnl,
                }
            }

    def print_daily_report(self):
        snap = self.get_snapshot()
        log.info("=" * 50)
        log.info("DAILY P&L REPORT")
        log.info("=" * 50)
        log.info(f"Net Liquidation:  ${snap['net_liquidation']:>12,.2f}")
        log.info(f"Total P&L:        ${snap['total_pnl']:>+12,.2f}")
        log.info(f"Unrealized:       ${snap['unrealized_pnl']:>+12,.2f}")
        log.info(f"Realized:         ${snap['realized_pnl']:>+12,.2f}")
        log.info(f"Max Drawdown:     ${snap['max_drawdown']:>12,.2f}")
        log.info("=" * 50)
