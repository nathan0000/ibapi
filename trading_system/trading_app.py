"""
TradingApp - Core IBKR EWrapper/EClient implementation
Handles all callbacks and coordinates subsystems.
"""

import logging
import threading
import time
from datetime import datetime, date
from typing import Optional, Dict, List, Callable, Any

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.execution import Execution
from ibapi.commission_and_fees_report import CommissionAndFeesReport
from ibapi.common import TickerId, OrderId, BarData

from config import TradingConfig
from market_data import MarketDataManager
from order_manager import OrderManager
from position_manager import PositionManager
from vix_analyzer import VIXAnalyzer
from risk_manager import RiskManager
from eod_manager import EODManager
from strategy_engine import StrategyEngine
from pnl_monitor import PnLMonitor
from trade_journal import TradeJournal
from reconnect import ReconnectHandler
from alerts import AlertSystem
from options_scanner import OptionsChainScanner
from contract_resolver import ContractResolver
from state_persistence import StatePersistence
from dashboard import Dashboard


log = logging.getLogger("TradingApp")


class TradingApp(EWrapper, EClient):
    """
    Central application class combining EWrapper and EClient.
    Dispatches all IBKR callbacks to appropriate subsystems.
    """

    def __init__(self, config: TradingConfig):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self.config = config
        self.shutdown_requested = False
        self._connected = False
        self._next_order_id: Optional[int] = None
        self._connection_event = threading.Event()

        # Subsystems (initialized after connection)
        self.market_data: Optional[MarketDataManager] = None
        self.orders: Optional[OrderManager] = None
        self.positions: Optional[PositionManager] = None
        self.vix_analyzer: Optional[VIXAnalyzer] = None
        self.risk: Optional[RiskManager] = None
        self.eod_manager: Optional[EODManager] = None
        self.strategy: Optional[StrategyEngine] = None
        self.pnl: Optional[PnLMonitor] = None
        self.journal: Optional[TradeJournal] = None
        self.reconnect: Optional[ReconnectHandler] = None
        self.alerts: Optional[AlertSystem] = None
        self.scanner: Optional[OptionsChainScanner] = None
        self.resolver: Optional[ContractResolver] = None
        self.persistence: Optional[StatePersistence] = None
        self.dashboard: Optional[Dashboard] = None

        # Callbacks registry
        self._tick_callbacks: Dict[int, List[Callable]] = {}
        self._bar_callbacks: Dict[int, List[Callable]] = {}

    # ─────────────────────────────────────────────────────────────────────
    # CONNECTION
    # ─────────────────────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        return self._connected and self._next_order_id is not None

    def on_connected(self):
        """Called after successful connection and receiving nextValidId."""
        log.info("Initializing subsystems...")

        self.market_data = MarketDataManager(self)
        self.orders = OrderManager(self)
        self.positions = PositionManager(self)
        self.vix_analyzer = VIXAnalyzer(self)
        self.risk = RiskManager(self)
        self.eod_manager = EODManager(self)
        self.pnl = PnLMonitor(self)
        self.strategy = StrategyEngine(self)
        self.journal = TradeJournal()
        self.reconnect = ReconnectHandler(self)
        self.alerts = AlertSystem()
        self.scanner = OptionsChainScanner(self)
        self.resolver = ContractResolver(self)
        self.persistence = StatePersistence(self)
        self.dashboard = Dashboard(self)

        # Wire alerts to VIX regime changes
        self.vix_analyzer.on_regime_change(
            lambda old, new, vix: self.alerts.vix_regime_change(old or "", new, vix)
        )

        # Wire alerts to risk breaches
        self.risk.on_halt = lambda: self.alerts.risk_limit_breached(
            self.pnl.get_snapshot().get("total_pnl", 0),
            self.config.risk.max_daily_loss_usd
        )

        # Wire journal to fills
        self.orders.on_fill(self._on_fill_journal)

        # Request account data
        self.reqAccountUpdates(True, "")
        time.sleep(0.5)

        # Load persisted state
        saved = self.persistence.load()
        if saved:
            self.persistence.restore(saved)

        # Subscribe market data
        self.market_data.subscribe_all()
        time.sleep(1)

        # Start subsystem threads
        self.vix_analyzer.start()
        self.eod_manager.start()
        self.pnl.start()
        self.strategy.start()
        self.persistence.start()
        self.dashboard.start()

        log.info("All subsystems initialized and running.")

    def _on_fill_journal(self, order_record):
        """Log fill to journal with current market context."""
        if not self.journal:
            return
        vix_snap = self.vix_analyzer.current if self.vix_analyzer else None
        pnl_snap = self.pnl.get_snapshot() if self.pnl else {}
        self.journal.log_fill(
            order_record,
            vix_regime=vix_snap.regime if vix_snap else "",
            vix_value=vix_snap.value if vix_snap else 0.0,
            account_pnl=pnl_snap.get("total_pnl", 0.0)
        )

    def heartbeat(self):
        """Called periodically from main loop."""
        if self._connected:
            self.reqCurrentTime()

    def initiate_shutdown(self):
        """Signal all subsystems to stop gracefully."""
        log.info("Initiating graceful shutdown...")
        self.shutdown_requested = True
        if self.strategy:
            self.strategy.stop()

    def cleanup_and_disconnect(self):
        """Stop all subsystems and disconnect."""
        if self.eod_manager:
            self.eod_manager.emergency_close_all()
        if self.market_data:
            self.market_data.cancel_all()
        if self.dashboard:
            self.dashboard.stop()
        if self.persistence:
            self.persistence.stop()
        if self.journal:
            self.journal.print_session_report()
        self._connected = False
        self.disconnect()
        log.info("Disconnected from IBKR.")

    # ─────────────────────────────────────────────────────────────────────
    # EWRAPPER CALLBACKS - CONNECTION
    # ─────────────────────────────────────────────────────────────────────

    def connectAck(self):
        log.info("Connection acknowledged by TWS/Gateway.")
        self._connected = True

    def nextValidId(self, orderId: int):
        log.info(f"Next valid order ID: {orderId}")
        self._next_order_id = orderId
        if self.orders:
            self.orders.set_next_order_id(orderId)
        self._connection_event.set()

    def connectionClosed(self):
        log.warning("Connection to TWS/Gateway closed.")
        self._connected = False

    def error(self, reqId: TickerId, errorCode: int, errorString: str,
              advancedOrderRejectJson: str = "", arg5=""):
        # Filter informational messages
        info_codes = {2104, 2106, 2108, 2158, 2119, 10197}
        if errorCode in info_codes:
            log.debug(f"TWS info [{errorCode}]: {errorString}")
            return

        if errorCode == 1102:   # Reconnected
            log.warning("Reconnected to TWS. Re-requesting data...")
            if self.market_data:
                self.market_data.subscribe_all()
            return

        level = logging.WARNING if errorCode < 2000 else logging.ERROR
        log.log(level, f"IBKR Error [{errorCode}] reqId={reqId}: {errorString}")

        # Notify relevant subsystem
        if self.orders and reqId in self.orders.pending_orders:
            self.orders.on_order_error(reqId, errorCode, errorString)

    def currentTime(self, time_: int):
        log.debug(f"Server time: {datetime.fromtimestamp(time_)}")

    # ─────────────────────────────────────────────────────────────────────
    # EWRAPPER CALLBACKS - MARKET DATA
    # ─────────────────────────────────────────────────────────────────────

    def tickPrice(self, reqId: TickerId, tickType: int, price: float, attrib):
        if self.market_data:
            self.market_data.on_tick_price(reqId, tickType, price)
        for cb in self._tick_callbacks.get(reqId, []):
            cb("price", tickType, price)

    def tickSize(self, reqId: TickerId, tickType: int, size: float):
        if self.market_data:
            self.market_data.on_tick_size(reqId, tickType, size)

    def tickOptionComputation(self, reqId: TickerId, tickType: int,
                               tickAttrib: int, impliedVol: float, delta: float,
                               optPrice: float, pvDividend: float, gamma: float,
                               vega: float, theta: float, undPrice: float):
        if self.market_data:
            self.market_data.on_option_greeks(reqId, tickType, {
                "impliedVol": impliedVol, "delta": delta, "gamma": gamma,
                "vega": vega, "theta": theta, "optPrice": optPrice,
                "undPrice": undPrice,
            })

    def tickGeneric(self, reqId: TickerId, tickType: int, value: float):
        if self.market_data:
            self.market_data.on_tick_generic(reqId, tickType, value)

    def realtimeBar(self, reqId: TickerId, time: int, open_: float,
                    high: float, low: float, close: float,
                    volume: float, wap: float, count: int):
        bar = {"time": time, "open": open_, "high": high,
               "low": low, "close": close, "volume": volume, "wap": wap}
        if self.market_data:
            self.market_data.on_realtime_bar(reqId, bar)
        for cb in self._bar_callbacks.get(reqId, []):
            cb(bar)

    def historicalData(self, reqId: int, bar: BarData):
        if self.market_data:
            self.market_data.on_historical_bar(reqId, bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if self.market_data:
            self.market_data.on_historical_data_end(reqId)

    # ─────────────────────────────────────────────────────────────────────
    # EWRAPPER CALLBACKS - ORDERS
    # ─────────────────────────────────────────────────────────────────────

    def orderStatus(self, orderId: OrderId, status: str, filled: float,
                    remaining: float, avgFillPrice: float, permId: int,
                    parentId: int, lastFillPrice: float, clientId: int,
                    whyHeld: str, mktCapPrice: float):
        log.info(f"Order {orderId}: {status} filled={filled:.0f} "
                 f"remaining={remaining:.0f} avgPrice={avgFillPrice:.4f}")
        if self.orders:
            self.orders.on_order_status(orderId, status, filled,
                                        remaining, avgFillPrice)

    def execDetails(self, reqId: int, contract: Contract, execution: Execution):
        log.info(f"Execution: {execution.execId} {execution.side} "
                 f"{execution.shares:.0f} {contract.symbol} @ {execution.price:.4f}")
        if self.orders:
            self.orders.on_execution(reqId, contract, execution)
        if self.pnl:
            self.pnl.on_execution(contract, execution)

    def commissionReport(self, commissionReport: CommissionAndFeesReport):
        log.debug(f"Commission: {commissionReport.commission:.4f} "
                  f"for exec {commissionReport.execId}")
        if self.pnl:
            self.pnl.on_commission(commissionReport)

    def openOrder(self, orderId: OrderId, contract: Contract,
                  order: Order, orderState):
        if self.orders:
            self.orders.on_open_order(orderId, contract, order, orderState)

    def openOrderEnd(self):
        log.debug("Open orders sync complete.")
        if self.orders:
            self.orders.on_open_orders_end()

    # ─────────────────────────────────────────────────────────────────────
    # EWRAPPER CALLBACKS - ACCOUNT & POSITIONS
    # ─────────────────────────────────────────────────────────────────────

    def updateAccountValue(self, key: str, val: str, currency: str,
                           accountName: str):
        if not self.config.account_id and accountName:
            self.config.account_id = accountName
        if self.pnl:
            self.pnl.on_account_value(key, val, currency, accountName)
        if self.risk:
            self.risk.on_account_value(key, val, currency)

    def updatePortfolio(self, contract: Contract, position: float,
                        marketPrice: float, marketValue: float,
                        averageCost: float, unrealizedPNL: float,
                        realizedPNL: float, accountName: str):
        if self.positions:
            self.positions.on_portfolio_update(
                contract, position, marketPrice, marketValue,
                averageCost, unrealizedPNL, realizedPNL
            )
        if self.pnl:
            self.pnl.on_portfolio_update(contract, unrealizedPNL, realizedPNL)

    def accountDownloadEnd(self, accountName: str):
        log.debug(f"Account data download complete for {accountName}")
        if self.positions:
            self.positions.on_account_download_end()

    def position(self, account: str, contract: Contract,
                 position: float, avgCost: float):
        if self.positions:
            self.positions.on_position(account, contract, position, avgCost)

    def positionEnd(self):
        if self.positions:
            self.positions.on_position_end()

    # ─────────────────────────────────────────────────────────────────────
    # EWRAPPER CALLBACKS - CONTRACT DETAILS
    # ─────────────────────────────────────────────────────────────────────

    def contractDetails(self, reqId: int, contractDetails):
        if self.market_data:
            self.market_data.on_contract_details(reqId, contractDetails)

    def contractDetailsEnd(self, reqId: int):
        if self.market_data:
            self.market_data.on_contract_details_end(reqId)

    def securityDefinitionOptionalParameter(self, reqId: int, exchange: str,
            underlyingConId: int, tradingClass: str, multiplier: str,
            expirations, strikes):
        if self.market_data:
            self.market_data.on_option_chain(reqId, exchange, expirations, strikes)

    def securityDefinitionOptionalParameterEnd(self, reqId: int):
        if self.market_data:
            self.market_data.on_option_chain_end(reqId)

    # ─────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────

    def get_next_order_id(self) -> int:
        oid = self._next_order_id
        self._next_order_id += 1
        return oid

    def register_tick_callback(self, req_id: int, callback: Callable):
        self._tick_callbacks.setdefault(req_id, []).append(callback)

    def register_bar_callback(self, req_id: int, callback: Callable):
        self._bar_callbacks.setdefault(req_id, []).append(callback)
