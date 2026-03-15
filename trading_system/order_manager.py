"""
Order Manager
Manages complete order lifecycle: creation, submission, tracking, modification,
cancellation. Supports market, limit, bracket, and combo orders.
"""

import logging
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable
from enum import Enum

from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.execution import Execution

log = logging.getLogger("OrderManager")


class OrderStatus(Enum):
    PENDING    = "Pending"
    SUBMITTED  = "Submitted"
    ACKNOWLEDGED = "PreSubmitted"
    PARTIAL    = "PartiallyFilled"
    FILLED     = "Filled"
    CANCELLED  = "Cancelled"
    INACTIVE   = "Inactive"
    ERROR      = "Error"


@dataclass
class TradeRecord:
    """Complete record of a single order/trade."""
    order_id: int
    contract: Contract
    action: str          # "BUY" or "SELL"
    quantity: float
    order_type: str      # "MKT", "LMT", "STP", etc.
    limit_price: float = 0.0
    stop_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    submitted_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    tag: str = ""          # User-defined label (strategy name, etc.)
    parent_id: int = 0     # For child orders in brackets
    children: List[int] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.status in (
            OrderStatus.PENDING, OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIAL
        )

    @property
    def realized_pnl(self) -> float:
        if self.filled_qty == 0 or self.avg_fill_price == 0:
            return 0.0
        # PnL calculation requires knowing entry price (tracked externally)
        return 0.0

    @property
    def symbol(self) -> str:
        return self.contract.symbol if self.contract else ""


class OrderManager:
    """
    Full order lifecycle management for IBKR native API.
    """

    def __init__(self, app):
        self.app = app
        self._next_id: int = 1
        self._lock = threading.RLock()
        self._orders: Dict[int, TradeRecord] = {}
        self._fill_callbacks: List[Callable] = []
        self._cancel_callbacks: List[Callable] = []
        self.pending_orders: Dict[int, TradeRecord] = {}

    def set_next_order_id(self, order_id: int):
        with self._lock:
            self._next_id = order_id
        log.info(f"Next order ID set to {order_id}")

    def _get_id(self) -> int:
        with self._lock:
            oid = self._next_id
            self._next_id += 1
            return oid

    def on_fill(self, callback: Callable):
        """Register fill callback: cb(trade_record)"""
        self._fill_callbacks.append(callback)

    def on_cancel(self, callback: Callable):
        """Register cancel callback: cb(order_id)"""
        self._cancel_callbacks.append(callback)

    # ─────────────────────────────────────────────────────────────────────
    # ORDER FACTORIES
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def make_market_order(action: str, quantity: float) -> Order:
        """Create a market order."""
        o = Order()
        o.action = action
        o.totalQuantity = quantity
        o.orderType = "MKT"
        o.tif = "DAY"
        o.transmit = True
        return o

    @staticmethod
    def make_limit_order(action: str, quantity: float, price: float) -> Order:
        """Create a limit order."""
        o = Order()
        o.action = action
        o.totalQuantity = quantity
        o.orderType = "LMT"
        o.lmtPrice = round(price, 2)
        o.tif = "DAY"
        o.transmit = True
        return o

    @staticmethod
    def make_stop_order(action: str, quantity: float, stop_price: float) -> Order:
        """Create a stop order."""
        o = Order()
        o.action = action
        o.totalQuantity = quantity
        o.orderType = "STP"
        o.auxPrice = stop_price
        o.tif = "DAY"
        o.transmit = True
        return o

    @staticmethod
    def make_stop_limit_order(action: str, quantity: float,
                               stop_price: float, limit_price: float) -> Order:
        """Create a stop-limit order."""
        o = Order()
        o.action = action
        o.totalQuantity = quantity
        o.orderType = "STP LMT"
        o.auxPrice = stop_price
        o.lmtPrice = limit_price
        o.tif = "DAY"
        o.transmit = True
        return o

    # ─────────────────────────────────────────────────────────────────────
    # ORDER SUBMISSION
    # ─────────────────────────────────────────────────────────────────────

    def submit_market(self, contract: Contract, action: str,
                      quantity: float, tag: str = "") -> int:
        """Submit a market order. Returns order_id."""
        oid = self._get_id()
        order = self.make_market_order(action, quantity)
        return self._submit(oid, contract, order, tag)

    def submit_limit(self, contract: Contract, action: str,
                     quantity: float, price: float, tag: str = "") -> int:
        """Submit a limit order. Returns order_id."""
        oid = self._get_id()
        order = self.make_limit_order(action, quantity, price)
        return self._submit(oid, contract, order, tag)

    def submit_bracket(self, contract: Contract, action: str,
                       quantity: float, entry_price: Optional[float],
                       stop_price: float, target_price: float,
                       tag: str = "") -> Dict[str, int]:
        """
        Submit a bracket order (entry + stop + target).
        Returns dict with order_ids: {entry, stop, target}
        """
        parent_id = self._get_id()
        stop_id = self._get_id()
        target_id = self._get_id()

        exit_action = "SELL" if action == "BUY" else "BUY"

        # Entry order
        if entry_price:
            entry_order = self.make_limit_order(action, quantity, entry_price)
        else:
            entry_order = self.make_market_order(action, quantity)
        entry_order.transmit = False  # Don't transmit until all legs ready

        # Stop loss
        stop_order = self.make_stop_order(exit_action, quantity, stop_price)
        stop_order.parentId = parent_id
        stop_order.transmit = False

        # Profit target
        target_order = self.make_limit_order(exit_action, quantity, target_price)
        target_order.parentId = parent_id
        target_order.ocaGroup = f"OCA_{parent_id}"
        target_order.ocaType = 2
        stop_order.ocaGroup = f"OCA_{parent_id}"
        stop_order.ocaType = 2
        target_order.transmit = True  # Last order triggers transmission

        # Track all three
        for oid, o, suffix in [
            (parent_id, entry_order, f"{tag}_ENTRY"),
            (stop_id, stop_order, f"{tag}_STOP"),
            (target_id, target_order, f"{tag}_TARGET"),
        ]:
            rec = TradeRecord(
                order_id=oid, contract=contract,
                action=o.action, quantity=quantity,
                order_type=o.orderType,
                limit_price=getattr(o, "lmtPrice", 0.0),
                stop_price=getattr(o, "auxPrice", 0.0),
                parent_id=parent_id if oid != parent_id else 0,
                tag=suffix
            )
            with self._lock:
                self._orders[oid] = rec
                self.pending_orders[oid] = rec

        # Submit all legs
        self.app.placeOrder(parent_id, contract, entry_order)
        self.app.placeOrder(stop_id, contract, stop_order)
        self.app.placeOrder(target_id, contract, target_order)

        log.info(f"Bracket order submitted: entry={parent_id} "
                 f"stop={stop_id} target={target_id} "
                 f"qty={quantity} tag={tag}")

        return {"entry": parent_id, "stop": stop_id, "target": target_id}

    def _submit(self, order_id: int, contract: Contract,
                order: Order, tag: str = "") -> int:
        """Internal order submission."""
        rec = TradeRecord(
            order_id=order_id,
            contract=contract,
            action=order.action,
            quantity=order.totalQuantity,
            order_type=order.orderType,
            limit_price=getattr(order, "lmtPrice", 0.0),
            stop_price=getattr(order, "auxPrice", 0.0),
            tag=tag
        )
        with self._lock:
            self._orders[order_id] = rec
            self.pending_orders[order_id] = rec

        self.app.placeOrder(order_id, contract, order)
        log.info(f"Order {order_id} submitted: {order.action} {order.totalQuantity:.0f} "
                 f"{contract.symbol} {order.orderType} tag={tag}")
        return order_id

    def cancel_order(self, order_id: int):
        """Cancel a specific order."""
        self.app.cancelOrder(order_id, "")
        log.info(f"Cancel requested for order {order_id}")

    def cancel_all_orders(self):
        """Cancel all active orders."""
        with self._lock:
            active = [oid for oid, rec in self._orders.items() if rec.is_active]
        for oid in active:
            self.cancel_order(oid)
        log.warning(f"Cancelled {len(active)} active orders")

    # ─────────────────────────────────────────────────────────────────────
    # CALLBACKS
    # ─────────────────────────────────────────────────────────────────────

    def on_order_status(self, order_id: int, status: str,
                        filled: float, remaining: float, avg_price: float):
        with self._lock:
            rec = self._orders.get(order_id)
            if not rec:
                return

            # Map IBKR status string to enum
            status_map = {
                "PreSubmitted": OrderStatus.ACKNOWLEDGED,
                "Submitted": OrderStatus.SUBMITTED,
                "PartiallyFilled": OrderStatus.PARTIAL,
                "Filled": OrderStatus.FILLED,
                "Cancelled": OrderStatus.CANCELLED,
                "Inactive": OrderStatus.INACTIVE,
            }
            rec.status = status_map.get(status, OrderStatus.SUBMITTED)

            if filled > 0:
                rec.filled_qty = filled
                rec.avg_fill_price = avg_price

            if rec.status == OrderStatus.FILLED:
                rec.filled_at = datetime.now()
                self.pending_orders.pop(order_id, None)
                log.info(f"Order {order_id} FILLED: {rec.action} {filled:.0f} "
                         f"{rec.symbol} @ {avg_price:.4f}")
                for cb in self._fill_callbacks:
                    try:
                        cb(rec)
                    except Exception as e:
                        log.error(f"Fill callback error: {e}")

            elif rec.status == OrderStatus.CANCELLED:
                self.pending_orders.pop(order_id, None)
                for cb in self._cancel_callbacks:
                    try:
                        cb(order_id)
                    except Exception as e:
                        log.error(f"Cancel callback error: {e}")

    def on_execution(self, req_id: int, contract: Contract, execution: Execution):
        with self._lock:
            rec = self._orders.get(execution.orderId)
            if rec and rec.avg_fill_price == 0:
                rec.avg_fill_price = execution.price

    def on_open_order(self, order_id: int, contract: Contract,
                      order: Order, order_state):
        with self._lock:
            if order_id not in self._orders:
                # Order submitted from another session
                rec = TradeRecord(
                    order_id=order_id,
                    contract=contract,
                    action=order.action,
                    quantity=order.totalQuantity,
                    order_type=order.orderType,
                    limit_price=getattr(order, "lmtPrice", 0.0),
                    stop_price=getattr(order, "auxPrice", 0.0),
                    status=OrderStatus.SUBMITTED,
                    tag="external"
                )
                self._orders[order_id] = rec

    def on_open_orders_end(self):
        log.debug("Open orders sync complete.")

    def on_order_error(self, req_id: int, error_code: int, error_msg: str):
        with self._lock:
            rec = self._orders.get(req_id)
            if rec:
                rec.status = OrderStatus.ERROR
                log.error(f"Order {req_id} error [{error_code}]: {error_msg}")

    # ─────────────────────────────────────────────────────────────────────
    # QUERIES
    # ─────────────────────────────────────────────────────────────────────

    def get_order(self, order_id: int) -> Optional[TradeRecord]:
        with self._lock:
            return self._orders.get(order_id)

    def get_active_orders(self) -> List[TradeRecord]:
        with self._lock:
            return [r for r in self._orders.values() if r.is_active]

    def get_filled_orders_today(self) -> List[TradeRecord]:
        today = datetime.now().date()
        with self._lock:
            return [r for r in self._orders.values()
                    if r.status == OrderStatus.FILLED
                    and r.filled_at and r.filled_at.date() == today]

    def get_open_order_count(self) -> int:
        return len(self.pending_orders)
