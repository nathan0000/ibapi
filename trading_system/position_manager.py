"""
Position Manager - Tracks all open positions and portfolio state.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from ibapi.contract import Contract

log = logging.getLogger("PositionManager")


@dataclass
class Position:
    """Represents a single open position."""
    contract: Contract
    quantity: float           # positive = long, negative = short
    avg_cost: float
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    last_update: datetime = field(default_factory=datetime.now)

    @property
    def symbol(self) -> str:
        return self.contract.symbol

    @property
    def sec_type(self) -> str:
        return self.contract.secType

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def abs_quantity(self) -> float:
        return abs(self.quantity)

    def key(self) -> str:
        c = self.contract
        return f"{c.symbol}_{c.secType}_{c.lastTradeDateOrContractMonth}_{c.strike}_{c.right}"


class PositionManager:
    """Tracks all open positions across ES, SPX options, and ES options."""

    def __init__(self, app):
        self.app = app
        self._positions: Dict[str, Position] = {}
        self._lock = threading.RLock()
        self._download_complete = threading.Event()

    def on_portfolio_update(self, contract: Contract, position: float,
                             market_price: float, market_value: float,
                             avg_cost: float, unrealized_pnl: float,
                             realized_pnl: float):
        key = f"{contract.symbol}_{contract.secType}_{getattr(contract, 'lastTradeDateOrContractMonth', '')}_{getattr(contract, 'strike', '')}_{getattr(contract, 'right', '')}"
        with self._lock:
            if position == 0:
                self._positions.pop(key, None)
            else:
                pos = Position(
                    contract=contract, quantity=position,
                    avg_cost=avg_cost, market_price=market_price,
                    market_value=market_value, unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl
                )
                self._positions[key] = pos
                log.debug(f"Position updated: {contract.symbol} {position:.0f} "
                          f"@ {avg_cost:.2f} pnl={unrealized_pnl:.2f}")

    def on_position(self, account: str, contract: Contract,
                    position: float, avg_cost: float):
        key = f"{contract.symbol}_{contract.secType}_{getattr(contract, 'lastTradeDateOrContractMonth', '')}_{getattr(contract, 'strike', '')}_{getattr(contract, 'right', '')}"
        with self._lock:
            if position == 0:
                self._positions.pop(key, None)
            else:
                existing = self._positions.get(key)
                if existing:
                    existing.quantity = position
                    existing.avg_cost = avg_cost
                else:
                    self._positions[key] = Position(
                        contract=contract, quantity=position, avg_cost=avg_cost
                    )

    def on_account_download_end(self):
        self._download_complete.set()

    def on_position_end(self):
        self._download_complete.set()

    def get_all(self) -> List[Position]:
        with self._lock:
            return [p for p in self._positions.values() if not p.is_flat]

    def get_es_positions(self) -> List[Position]:
        with self._lock:
            return [p for p in self._positions.values()
                    if p.symbol == "ES" and p.sec_type == "FUT" and not p.is_flat]

    def get_spx_option_positions(self) -> List[Position]:
        with self._lock:
            return [p for p in self._positions.values()
                    if p.symbol == "SPX" and p.sec_type == "OPT" and not p.is_flat]

    def get_es_option_positions(self) -> List[Position]:
        with self._lock:
            return [p for p in self._positions.values()
                    if p.symbol == "ES" and p.sec_type == "FOP" and not p.is_flat]

    def get_all_closeable(self) -> List[Position]:
        """All ES futures and options positions that must close at EOD."""
        return self.get_es_positions() + self.get_spx_option_positions() + self.get_es_option_positions()

    def get_total_unrealized_pnl(self) -> float:
        with self._lock:
            return sum(p.unrealized_pnl for p in self._positions.values())

    def get_total_realized_pnl(self) -> float:
        with self._lock:
            return sum(p.realized_pnl for p in self._positions.values())

    def has_open_positions(self) -> bool:
        return len(self.get_all()) > 0

    def position_count(self) -> int:
        return len(self.get_all())

    def print_summary(self):
        positions = self.get_all()
        if not positions:
            log.info("No open positions.")
            return
        log.info(f"{'Symbol':<12} {'Type':<6} {'Qty':>8} {'AvgCost':>10} "
                 f"{'MktPrice':>10} {'UnrPnL':>10}")
        log.info("-" * 65)
        for p in positions:
            log.info(f"{p.symbol:<12} {p.sec_type:<6} {p.quantity:>8.0f} "
                     f"{p.avg_cost:>10.2f} {p.market_price:>10.2f} "
                     f"{p.unrealized_pnl:>10.2f}")
