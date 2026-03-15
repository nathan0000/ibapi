"""
Market Data Manager
Manages all market data subscriptions: ES futures, SPX/ES options, VIX.
Maintains price caches and OHLC bar history.
"""

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Optional, Dict, List, Callable, Deque, Any, Set

from ibapi.contract import Contract
from ibapi.common import TickerId

log = logging.getLogger("MarketData")

# IBKR tick type constants
TICK_BID = 1
TICK_ASK = 2
TICK_LAST = 4
TICK_HIGH = 6
TICK_LOW  = 7
TICK_CLOSE = 9
TICK_VOLUME = 8
TICK_OPEN = 14
TICK_HALTED = 49


def make_es_contract(expiry: str = "") -> Contract:
    """Create an E-mini S&P 500 futures contract."""
    c = Contract()
    c.symbol = "ES"
    c.secType = "FUT"
    c.exchange = "CME"
    c.currency = "USD"
    if expiry:
        c.lastTradeDateOrContractMonth = expiry
    return c


def make_spx_option_contract(expiry: str, strike: float, right: str) -> Contract:
    """Create an SPX index option contract."""
    c = Contract()
    c.symbol = "SPX"
    c.secType = "OPT"
    c.exchange = "CBOE"
    c.currency = "USD"
    c.lastTradeDateOrContractMonth = expiry
    c.strike = strike
    c.right = right   # "C" or "P"
    c.multiplier = "100"
    return c


def make_es_option_contract(expiry: str, strike: float, right: str) -> Contract:
    """Create an ES futures option contract."""
    c = Contract()
    c.symbol = "ES"
    c.secType = "FOP"
    c.exchange = "CME"
    c.currency = "USD"
    c.lastTradeDateOrContractMonth = expiry
    c.strike = strike
    c.right = right
    c.multiplier = "50"
    return c


def make_vix_contract() -> Contract:
    """Create VIX index contract."""
    c = Contract()
    c.symbol = "VIX"
    c.secType = "IND"
    c.exchange = "CBOE"
    c.currency = "USD"
    return c


class BarBuffer:
    """Thread-safe circular buffer for OHLC bars."""

    def __init__(self, maxlen: int = 500):
        self._bars: Deque[Dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, bar: Dict):
        with self._lock:
            self._bars.append(bar)

    def get_all(self) -> List[Dict]:
        with self._lock:
            return list(self._bars)

    def get_last(self, n: int) -> List[Dict]:
        with self._lock:
            bars = list(self._bars)
            return bars[-n:] if len(bars) >= n else bars

    def __len__(self):
        return len(self._bars)


class QuoteCache:
    """Thread-safe quote data cache."""

    def __init__(self):
        self._data: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_update: Optional[datetime] = None

    def update(self, key: str, value: float):
        with self._lock:
            self._data[key] = value
            self._last_update = datetime.now()

    def get(self, key: str, default: float = 0.0) -> float:
        with self._lock:
            return self._data.get(key, default)

    @property
    def bid(self): return self.get("bid")
    @property
    def ask(self): return self.get("ask")
    @property
    def last(self): return self.get("last")
    @property
    def mid(self):
        b, a = self.bid, self.ask
        return (b + a) / 2 if b > 0 and a > 0 else self.last

    @property
    def age_seconds(self) -> float:
        if not self._last_update:
            return float("inf")
        return (datetime.now() - self._last_update).total_seconds()


class OptionGreeksCache:
    """Cache for option greeks."""

    def __init__(self):
        self.implied_vol: float = 0.0
        self.delta: float = 0.0
        self.gamma: float = 0.0
        self.vega: float = 0.0
        self.theta: float = 0.0
        self.opt_price: float = 0.0
        self.und_price: float = 0.0
        self._lock = threading.Lock()

    def update(self, data: Dict):
        with self._lock:
            for k, v in data.items():
                if hasattr(self, k) and v is not None and v != -1.0:
                    setattr(self, k, v)


class MarketDataManager:
    """
    Manages all market data subscriptions.
    Provides clean interface to price data, bars, and option greeks.
    """

    def __init__(self, app):
        self.app = app
        self._req_id_counter = 1000
        self._lock = threading.Lock()

        # Maps: req_id -> symbol identifier
        self._req_to_symbol: Dict[int, str] = {}
        self._symbol_to_req: Dict[str, int] = {}

        # Price caches
        self._quotes: Dict[str, QuoteCache] = {}
        self._greeks: Dict[int, OptionGreeksCache] = {}
        self._bars: Dict[str, BarBuffer] = {}

        # Historical data callbacks
        self._hist_callbacks: Dict[int, List[Callable]] = {}
        self._hist_done: Dict[int, threading.Event] = {}

        # Option chain data
        self._option_chains: Dict[int, Dict] = {}
        self._chain_events: Dict[int, threading.Event] = {}

        # Contract detail events
        self._contract_events: Dict[int, threading.Event] = {}
        self._contracts: Dict[int, Any] = {}

        # Subscribed req_ids
        self._active_subs: Set[int] = set()

        # Initialize standard symbols
        for sym in ["ES", "SPX", "VIX"]:
            self._quotes[sym] = QuoteCache()
            self._bars[sym] = BarBuffer()

    def _next_req_id(self) -> int:
        with self._lock:
            self._req_id_counter += 1
            return self._req_id_counter

    # ─────────────────────────────────────────────────────────────────────
    # SUBSCRIPTIONS
    # ─────────────────────────────────────────────────────────────────────

    def subscribe_all(self):
        """Subscribe to all required market data."""
        log.info("Subscribing to market data...")

        # ES Futures (front month - resolved dynamically)
        self._subscribe_es()

        # SPX index (for reference)
        self._subscribe_spx()

        # VIX
        self._subscribe_vix()

        log.info("Market data subscriptions requested.")

    def _subscribe_es(self):
        """Subscribe to ES futures market data and real-time bars."""
        c = make_es_contract()
        req_id = self._next_req_id()
        self._req_to_symbol[req_id] = "ES"
        self._symbol_to_req["ES"] = req_id
        self._active_subs.add(req_id)
        self.app.reqMktData(req_id, c, "", False, False, [])

        # Real-time bars (5-second)
        bar_req = self._next_req_id()
        self._req_to_symbol[bar_req] = "ES_BARS"
        self._symbol_to_req["ES_BARS"] = bar_req
        self._active_subs.add(bar_req)
        self.app.reqRealTimeBars(bar_req, c, 5, "TRADES", True, [])
        log.debug(f"ES subscription: mktdata={req_id}, bars={bar_req}")

    def _subscribe_spx(self):
        """Subscribe to SPX index data."""
        c = Contract()
        c.symbol = "SPX"
        c.secType = "IND"
        c.exchange = "CBOE"
        c.currency = "USD"

        req_id = self._next_req_id()
        self._req_to_symbol[req_id] = "SPX"
        self._symbol_to_req["SPX"] = req_id
        self._active_subs.add(req_id)
        self.app.reqMktData(req_id, c, "", False, False, [])
        log.debug(f"SPX subscription: {req_id}")

    def _subscribe_vix(self):
        """Subscribe to VIX data."""
        c = make_vix_contract()
        req_id = self._next_req_id()
        self._req_to_symbol[req_id] = "VIX"
        self._symbol_to_req["VIX"] = req_id
        self._active_subs.add(req_id)
        self.app.reqMktData(req_id, c, "", False, False, [])
        log.debug(f"VIX subscription: {req_id}")

    def subscribe_option(self, contract: Contract, symbol_key: str) -> int:
        """Subscribe to option market data. Returns req_id."""
        req_id = self._next_req_id()
        self._req_to_symbol[req_id] = symbol_key
        self._symbol_to_req[symbol_key] = req_id
        self._quotes[symbol_key] = QuoteCache()
        self._greeks[req_id] = OptionGreeksCache()
        self._active_subs.add(req_id)
        self.app.reqMktData(req_id, contract, "100,101,106", False, False, [])
        log.debug(f"Option subscription {symbol_key}: {req_id}")
        return req_id

    def cancel_subscription(self, req_id: int):
        """Cancel a market data subscription."""
        if req_id in self._active_subs:
            self.app.cancelMktData(req_id)
            self._active_subs.discard(req_id)

    def cancel_all(self):
        """Cancel all active subscriptions."""
        for req_id in list(self._active_subs):
            try:
                self.app.cancelMktData(req_id)
            except Exception:
                pass
        self._active_subs.clear()

    def request_historical_bars(self, contract: Contract, duration: str,
                                  bar_size: str, what_to_show: str = "TRADES",
                                  callback: Optional[Callable] = None) -> int:
        """Request historical bar data."""
        req_id = self._next_req_id()
        if callback:
            self._hist_callbacks[req_id] = [callback]
        done_event = threading.Event()
        self._hist_done[req_id] = done_event
        self.app.reqHistoricalData(
            req_id, contract, "", duration, bar_size,
            what_to_show, 1, 1, False, []
        )
        return req_id

    def request_option_chain(self, underlying_con_id: int, symbol: str,
                              sec_type: str = "STK") -> Dict:
        """Request option chain (expirations and strikes)."""
        req_id = self._next_req_id()
        event = threading.Event()
        self._chain_events[req_id] = event
        self._option_chains[req_id] = {}
        self.app.reqSecDefOptParams(req_id, symbol, "", sec_type, underlying_con_id)
        event.wait(timeout=10)
        return self._option_chains.get(req_id, {})

    # ─────────────────────────────────────────────────────────────────────
    # PRICE ACCESSORS
    # ─────────────────────────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> QuoteCache:
        return self._quotes.get(symbol, QuoteCache())

    def get_es_price(self) -> float:
        return self._quotes["ES"].last or self._quotes["ES"].mid

    def get_spx_price(self) -> float:
        return self._quotes["SPX"].last or self._quotes["SPX"].mid

    def get_vix(self) -> float:
        return self._quotes["VIX"].last or self._quotes["VIX"].mid

    def get_es_bars(self, n: int = 100) -> List[Dict]:
        return self._bars["ES"].get_last(n)

    def get_greeks(self, req_id: int) -> Optional[OptionGreeksCache]:
        return self._greeks.get(req_id)

    # ─────────────────────────────────────────────────────────────────────
    # CALLBACKS FROM TradingApp
    # ─────────────────────────────────────────────────────────────────────

    def on_tick_price(self, req_id: int, tick_type: int, price: float):
        symbol = self._req_to_symbol.get(req_id)
        if not symbol or price <= 0:
            return
        q = self._quotes.get(symbol)
        if not q:
            self._quotes[symbol] = QuoteCache()
            q = self._quotes[symbol]

        if tick_type == TICK_BID:    q.update("bid", price)
        elif tick_type == TICK_ASK:  q.update("ask", price)
        elif tick_type == TICK_LAST: q.update("last", price)
        elif tick_type == TICK_HIGH: q.update("high", price)
        elif tick_type == TICK_LOW:  q.update("low", price)
        elif tick_type == TICK_CLOSE: q.update("close", price)

    def on_tick_size(self, req_id: int, tick_type: int, size: float):
        symbol = self._req_to_symbol.get(req_id)
        if symbol and tick_type == TICK_VOLUME:
            q = self._quotes.get(symbol)
            if q:
                q.update("volume", size)

    def on_tick_generic(self, req_id: int, tick_type: int, value: float):
        symbol = self._req_to_symbol.get(req_id)
        if symbol == "VIX" and tick_type == TICK_LAST:
            self._quotes["VIX"].update("last", value)

    def on_option_greeks(self, req_id: int, tick_type: int, data: Dict):
        if req_id in self._greeks:
            self._greeks[req_id].update(data)
        # Also update the quote bid/ask from model price
        symbol = self._req_to_symbol.get(req_id)
        if symbol and "optPrice" in data and data["optPrice"] > 0:
            q = self._quotes.get(symbol)
            if q:
                q.update("last", data["optPrice"])

    def on_realtime_bar(self, req_id: int, bar: Dict):
        symbol = self._req_to_symbol.get(req_id, "").replace("_BARS", "")
        if symbol in self._bars:
            self._bars[symbol].append(bar)
        # Update last price from bar
        if symbol in self._quotes:
            self._quotes[symbol].update("last", bar["close"])

    def on_historical_bar(self, req_id: int, bar):
        callbacks = self._hist_callbacks.get(req_id, [])
        for cb in callbacks:
            cb(bar)

    def on_historical_data_end(self, req_id: int):
        event = self._hist_done.get(req_id)
        if event:
            event.set()
        self._hist_callbacks.pop(req_id, None)

    def on_contract_details(self, req_id: int, details):
        self._contracts[req_id] = details
        event = self._contract_events.get(req_id)
        if event:
            event.set()

    def on_contract_details_end(self, req_id: int):
        event = self._contract_events.get(req_id)
        if event:
            event.set()

    def on_option_chain(self, req_id: int, exchange: str,
                         expirations, strikes):
        chain = self._option_chains.get(req_id, {})
        chain["expirations"] = sorted(expirations)
        chain["strikes"] = sorted(strikes)
        chain["exchange"] = exchange
        self._option_chains[req_id] = chain

    def on_option_chain_end(self, req_id: int):
        event = self._chain_events.get(req_id)
        if event:
            event.set()
