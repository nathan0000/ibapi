"""
Trade Journal
Logs every fill to a daily CSV file and maintains in-memory statistics.
Provides end-of-day performance reports.
"""

import csv
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Dict
from zoneinfo import ZoneInfo

log = logging.getLogger("TradeJournal")
ET = ZoneInfo("America/New_York")


@dataclass
class JournalEntry:
    """Single trade record."""
    timestamp:      str
    order_id:       int
    symbol:         str
    sec_type:       str
    expiry:         str
    strike:         float
    right:          str
    action:         str
    quantity:       float
    fill_price:     float
    commission:     float
    realized_pnl:   float
    tag:            str
    vix_regime:     str
    vix_value:      float
    account_pnl:    float


@dataclass
class SessionStats:
    """Aggregate statistics for the trading session."""
    date:            str
    total_trades:    int   = 0
    winning_trades:  int   = 0
    losing_trades:   int   = 0
    total_realized:  float = 0.0
    total_commission:float = 0.0
    net_pnl:         float = 0.0
    max_win:         float = 0.0
    max_loss:        float = 0.0
    gross_profit:    float = 0.0
    gross_loss:      float = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0: return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0: return float("inf")
        return self.gross_profit / self.gross_loss

    @property
    def average_win(self) -> float:
        if self.winning_trades == 0: return 0.0
        return self.gross_profit / self.winning_trades

    @property
    def average_loss(self) -> float:
        if self.losing_trades == 0: return 0.0
        return self.gross_loss / self.losing_trades


CSV_FIELDS = [
    "timestamp", "order_id", "symbol", "sec_type", "expiry",
    "strike", "right", "action", "quantity", "fill_price",
    "commission", "realized_pnl", "tag", "vix_regime", "vix_value",
    "account_pnl",
]


class TradeJournal:
    """
    Thread-safe trade journal. Writes each fill to CSV immediately.
    """

    def __init__(self, log_dir: str = "logs"):
        self._lock = threading.Lock()
        self._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        today = date.today().strftime("%Y%m%d")
        self._csv_path = os.path.join(log_dir, f"trades_{today}.csv")
        self._stats = SessionStats(date=today)
        self._entries: List[JournalEntry] = []

        self._init_csv()

    def _init_csv(self):
        """Write CSV header if file is new."""
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
            log.info(f"Trade journal created: {self._csv_path}")

    def log_fill(self, order_record, commission: float = 0.0,
                 realized_pnl: float = 0.0, vix_regime: str = "",
                 vix_value: float = 0.0, account_pnl: float = 0.0):
        """Record a fill to the journal. Call from order fill callback."""
        c = order_record.contract
        entry = JournalEntry(
            timestamp   = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
            order_id    = order_record.order_id,
            symbol      = c.symbol if c else "",
            sec_type    = c.secType if c else "",
            expiry      = getattr(c, "lastTradeDateOrContractMonth", "") if c else "",
            strike      = getattr(c, "strike", 0.0) if c else 0.0,
            right       = getattr(c, "right", "") if c else "",
            action      = order_record.action,
            quantity    = order_record.filled_qty,
            fill_price  = order_record.avg_fill_price,
            commission  = commission,
            realized_pnl= realized_pnl,
            tag         = order_record.tag,
            vix_regime  = vix_regime,
            vix_value   = vix_value,
            account_pnl = account_pnl,
        )

        with self._lock:
            self._entries.append(entry)
            self._update_stats(entry)
            self._write_row(entry)

        log.info(f"Journal: {entry.action} {entry.quantity:.0f} {entry.symbol}"
                 f" @ {entry.fill_price:.4f}  pnl={realized_pnl:+.2f}"
                 f"  regime={vix_regime}")

    def _write_row(self, entry: JournalEntry):
        try:
            with open(self._csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writerow({
                    "timestamp":    entry.timestamp,
                    "order_id":     entry.order_id,
                    "symbol":       entry.symbol,
                    "sec_type":     entry.sec_type,
                    "expiry":       entry.expiry,
                    "strike":       entry.strike,
                    "right":        entry.right,
                    "action":       entry.action,
                    "quantity":     entry.quantity,
                    "fill_price":   entry.fill_price,
                    "commission":   entry.commission,
                    "realized_pnl": entry.realized_pnl,
                    "tag":          entry.tag,
                    "vix_regime":   entry.vix_regime,
                    "vix_value":    entry.vix_value,
                    "account_pnl":  entry.account_pnl,
                })
        except Exception as e:
            log.error(f"Journal write error: {e}")

    def _update_stats(self, entry: JournalEntry):
        pnl = entry.realized_pnl
        if pnl == 0:
            return
        self._stats.total_trades   += 1
        self._stats.total_realized += pnl
        self._stats.total_commission += entry.commission
        self._stats.net_pnl = self._stats.total_realized - self._stats.total_commission

        if pnl > 0:
            self._stats.winning_trades += 1
            self._stats.gross_profit   += pnl
            self._stats.max_win = max(self._stats.max_win, pnl)
        else:
            self._stats.losing_trades += 1
            self._stats.gross_loss    += abs(pnl)
            self._stats.max_loss = min(self._stats.max_loss, pnl)

    def get_stats(self) -> SessionStats:
        with self._lock:
            return self._stats

    def print_session_report(self):
        s = self.get_stats()
        log.info("=" * 55)
        log.info(f"  SESSION REPORT  —  {s.date}")
        log.info("=" * 55)
        log.info(f"  Trades:       {s.total_trades:>6}  (W:{s.winning_trades} L:{s.losing_trades})")
        log.info(f"  Win Rate:     {s.win_rate:>6.1f}%")
        log.info(f"  Profit Factor:{s.profit_factor:>6.2f}")
        log.info(f"  Gross P&L:    ${s.total_realized:>+10,.2f}")
        log.info(f"  Commission:   ${s.total_commission:>+10,.2f}")
        log.info(f"  Net P&L:      ${s.net_pnl:>+10,.2f}")
        log.info(f"  Best Trade:   ${s.max_win:>+10,.2f}")
        log.info(f"  Worst Trade:  ${s.max_loss:>+10,.2f}")
        log.info(f"  Avg Win:      ${s.average_win:>+10,.2f}")
        log.info(f"  Avg Loss:     ${s.average_loss:>+10,.2f}")
        log.info(f"  Journal file: {self._csv_path}")
        log.info("=" * 55)

    @property
    def csv_path(self) -> str:
        return self._csv_path

    def get_entries(self) -> List[JournalEntry]:
        with self._lock:
            return list(self._entries)
