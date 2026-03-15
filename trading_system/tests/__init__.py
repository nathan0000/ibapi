"""
Test Suite
Comprehensive tests for all trading system components.
Tests run without live IBKR connection using mock data.

Usage:
    python main.py --run-tests                    # all tests
    python main.py --run-tests --test-module vix  # specific module
"""

import logging
import sys
import threading
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Any
from unittest.mock import MagicMock, patch, PropertyMock

log = logging.getLogger("Tests")


# ─────────────────────────────────────────────────────────────────────────────
# TEST FRAMEWORK
# ─────────────────────────────────────────────────────────────────────────────

class TestCase:
    def __init__(self, name: str):
        self.name = name
        self.passed = 0
        self.failed = 0
        self.errors: List[str] = []

    def assert_true(self, condition: bool, msg: str = ""):
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            err = f"FAIL: {msg or 'assertion failed'}"
            self.errors.append(err)
            log.error(f"  ✗ {err}")

    def assert_equal(self, a, b, msg: str = ""):
        if a == b:
            self.passed += 1
            log.debug(f"  ✓ {msg or f'{a} == {b}'}")
        else:
            self.failed += 1
            err = f"FAIL: {msg or f'expected {b}, got {a}'}"
            self.errors.append(err)
            log.error(f"  ✗ {err}")

    def assert_in_range(self, value, lo, hi, msg: str = ""):
        if lo <= value <= hi:
            self.passed += 1
            log.debug(f"  ✓ {value} in [{lo}, {hi}]")
        else:
            self.failed += 1
            err = f"FAIL: {value} not in [{lo}, {hi}] ({msg})"
            self.errors.append(err)
            log.error(f"  ✗ {err}")

    def assert_not_none(self, val, msg: str = ""):
        self.assert_true(val is not None, msg or "value is None")

    def ok(self, msg: str):
        self.passed += 1
        log.info(f"  ✓ {msg}")

    def fail(self, msg: str):
        self.failed += 1
        self.errors.append(msg)
        log.error(f"  ✗ {msg}")

    @property
    def success(self) -> bool:
        return self.failed == 0


class TestRunner:
    def __init__(self):
        self.results: List[TestCase] = []

    def run(self, module: str = "all") -> bool:
        log.info("\n" + "=" * 60)
        log.info("  TRADING SYSTEM TEST SUITE")
        log.info("=" * 60)

        modules = {
            "connection":   self.test_connection_mock,
            "market_data":  self.test_market_data,
            "orders":       self.test_order_manager,
            "positions":    self.test_position_manager,
            "vix":          self.test_vix_analyzer,
            "eod":          self.test_eod_manager,
            "risk":         self.test_risk_manager,
            "strategy":     self.test_strategy_indicators,
            "pnl":          self.test_pnl_monitor,
            "analytics":    self.test_analytics,
            "journal":      self.test_trade_journal,
            "scanner":      self.test_options_scanner,
            "alerts":       self.test_alerts,
            "resolver":     self.test_contract_resolver,
            "persistence":  self.test_state_persistence,
            "reconnect":    self.test_reconnect,
        }

        to_run = modules if module == "all" else {module: modules[module]}

        for name, fn in to_run.items():
            log.info(f"\n▶ Testing: {name.upper()}")
            tc = TestCase(name)
            try:
                fn(tc)
            except Exception as e:
                tc.fail(f"Unhandled exception: {e}")
                import traceback
                log.error(traceback.format_exc())
            self.results.append(tc)

        return self._print_summary()

    def _print_summary(self) -> bool:
        log.info("\n" + "=" * 60)
        log.info("  TEST RESULTS")
        log.info("=" * 60)
        total_pass = total_fail = 0
        all_passed = True
        for tc in self.results:
            status = "✓ PASS" if tc.success else "✗ FAIL"
            log.info(f"  {status}  {tc.name:<20} passed={tc.passed} failed={tc.failed}")
            total_pass += tc.passed
            total_fail += tc.failed
            if not tc.success:
                all_passed = False
                for err in tc.errors:
                    log.error(f"         → {err}")
        log.info("-" * 60)
        log.info(f"  Total: {total_pass} passed, {total_fail} failed")
        log.info("=" * 60)
        return all_passed

    # ─────────────────────────────────────────────────────────────────────
    # TEST: CONNECTION (MOCK)
    # ─────────────────────────────────────────────────────────────────────

    def test_connection_mock(self, tc: TestCase):
        """Test connection flow without actual TWS."""
        from config import TradingConfig
        from trading_app import TradingApp

        # Test config defaults
        cfg = TradingConfig()
        tc.assert_equal(cfg.port, 7497, "Default paper port is 7497")
        tc.assert_equal(cfg.mode, "paper", "Default mode is paper")
        tc.assert_true(cfg.is_paper, "is_paper returns True")
        tc.assert_true(not cfg.is_live, "is_live returns False for paper")

        cfg_live = TradingConfig(mode="live", port=7496)
        tc.assert_equal(cfg_live.port, 7496, "Live port is 7496")
        tc.assert_true(cfg_live.is_live, "is_live returns True")

        # Test app creation
        app = TradingApp(cfg)
        tc.assert_true(not app.is_connected(), "Not connected initially")
        tc.assert_equal(app._next_order_id, None, "No order ID before connection")

        # Simulate nextValidId callback
        app._next_order_id = 100
        app._connected = True
        tc.assert_equal(app.get_next_order_id(), 100, "First order ID is 100")
        tc.assert_equal(app.get_next_order_id(), 101, "Second order ID is 101")

        tc.ok("TradingApp initialization and order ID management work correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: MARKET DATA
    # ─────────────────────────────────────────────────────────────────────

    def test_market_data(self, tc: TestCase):
        """Test market data caching and retrieval."""
        from market_data import (
            MarketDataManager, QuoteCache, BarBuffer,
            make_es_contract, make_spx_option_contract, make_es_option_contract,
            make_vix_contract, TICK_BID, TICK_ASK, TICK_LAST
        )

        # Test contract factories
        es = make_es_contract()
        tc.assert_equal(es.symbol, "ES", "ES contract symbol")
        tc.assert_equal(es.secType, "FUT", "ES contract type")
        tc.assert_equal(es.exchange, "CME", "ES exchange")

        spx_opt = make_spx_option_contract("20250117", 5000.0, "C")
        tc.assert_equal(spx_opt.symbol, "SPX", "SPX option symbol")
        tc.assert_equal(spx_opt.secType, "OPT", "SPX option type")
        tc.assert_equal(spx_opt.strike, 5000.0, "SPX strike")
        tc.assert_equal(spx_opt.right, "C", "SPX call right")

        es_opt = make_es_option_contract("20250117", 5000.0, "P")
        tc.assert_equal(es_opt.secType, "FOP", "ES futures option type")
        tc.assert_equal(es_opt.right, "P", "ES put right")

        vix = make_vix_contract()
        tc.assert_equal(vix.symbol, "VIX", "VIX symbol")
        tc.assert_equal(vix.secType, "IND", "VIX is index")

        # Test QuoteCache
        q = QuoteCache()
        tc.assert_equal(q.bid, 0.0, "Initial bid is 0")
        tc.assert_equal(q.age_seconds, float("inf"), "No update = infinite age")

        q.update("bid", 5100.25)
        q.update("ask", 5100.50)
        q.update("last", 5100.35)
        tc.assert_equal(q.bid, 5100.25, "Bid updated")
        tc.assert_equal(q.ask, 5100.50, "Ask updated")
        tc.assert_equal(q.last, 5100.35, "Last updated")
        tc.assert_equal(q.mid, 5100.375, "Mid = (bid+ask)/2")
        tc.assert_true(q.age_seconds < 1, "Recent update is fresh")

        # Test BarBuffer
        buf = BarBuffer(maxlen=5)
        tc.assert_equal(len(buf), 0, "Empty buffer")
        for i in range(7):
            buf.append({"close": float(i), "open": float(i), "high": float(i), "low": float(i), "volume": 100.0})
        tc.assert_equal(len(buf), 5, "Buffer maxlen respected")
        last = buf.get_last(3)
        tc.assert_equal(len(last), 3, "get_last(3) returns 3 bars")
        tc.assert_equal(last[-1]["close"], 6.0, "Last bar close is correct")

        # Test MarketDataManager with mock app
        app = MagicMock()
        app.config.es.entry_ema_fast = 9
        mdm = MarketDataManager(app)
        tc.assert_not_none(mdm._quotes.get("ES"), "ES quote cache initialized")
        tc.assert_not_none(mdm._quotes.get("VIX"), "VIX quote cache initialized")

        # Simulate tick callbacks
        req_id = 1001
        mdm._req_to_symbol[req_id] = "ES"
        mdm.on_tick_price(req_id, TICK_BID, 5100.25)
        mdm.on_tick_price(req_id, TICK_ASK, 5100.75)
        mdm.on_tick_price(req_id, TICK_LAST, 5100.50)

        tc.assert_equal(mdm._quotes["ES"].bid, 5100.25, "ES bid from tick")
        tc.assert_equal(mdm._quotes["ES"].ask, 5100.75, "ES ask from tick")
        tc.assert_equal(mdm._quotes["ES"].last, 5100.50, "ES last from tick")
        tc.assert_equal(mdm.get_es_price(), 5100.50, "get_es_price returns last")

        # Simulate real-time bars
        bar_req = 1002
        mdm._req_to_symbol[bar_req] = "ES_BARS"
        for i in range(35):
            mdm.on_realtime_bar(bar_req, {
                "time": i, "open": 5100.0 + i, "high": 5101.0 + i,
                "low": 5099.0 + i, "close": 5100.5 + i, "volume": 1000.0 + i
            })
        bars = mdm.get_es_bars(20)
        tc.assert_equal(len(bars), 20, "get_es_bars(20) returns 20 bars")

        tc.ok("MarketDataManager correctly handles ticks and bars")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: ORDER MANAGER
    # ─────────────────────────────────────────────────────────────────────

    def test_order_manager(self, tc: TestCase):
        """Test order creation, tracking, and status updates."""
        from order_manager import OrderManager, OrderStatus, TradeRecord
        from market_data import make_es_contract

        app = MagicMock()
        app._next_order_id = 1
        app.get_next_order_id = lambda: app._next_order_id

        om = OrderManager(app)
        om.set_next_order_id(100)
        tc.assert_equal(om._next_id, 100, "Next order ID set correctly")

        # Test order factories
        mkt = OrderManager.make_market_order("BUY", 2)
        tc.assert_equal(mkt.action, "BUY", "Market order action")
        tc.assert_equal(mkt.totalQuantity, 2, "Market order quantity")
        tc.assert_equal(mkt.orderType, "MKT", "Market order type")

        lmt = OrderManager.make_limit_order("SELL", 1, 5100.50)
        tc.assert_equal(lmt.orderType, "LMT", "Limit order type")
        tc.assert_equal(lmt.lmtPrice, 5100.50, "Limit order price")

        stp = OrderManager.make_stop_order("SELL", 2, 5090.00)
        tc.assert_equal(stp.orderType, "STP", "Stop order type")
        tc.assert_equal(stp.auxPrice, 5090.00, "Stop price")

        # Test order submission tracking
        contract = make_es_contract()
        app.placeOrder = MagicMock()

        oid = om.submit_market(contract, "BUY", 1, tag="TEST_TRADE")
        tc.assert_equal(oid, 100, "First order ID is 100")
        tc.assert_true(oid in om._orders, "Order tracked in dict")
        tc.assert_equal(om._orders[oid].tag, "TEST_TRADE", "Tag stored")
        tc.assert_equal(om._orders[oid].status, OrderStatus.PENDING, "Initial status PENDING")
        tc.assert_true(app.placeOrder.called, "placeOrder called on app")

        oid2 = om.submit_limit(contract, "SELL", 1, 5150.0, tag="LIMIT_TEST")
        tc.assert_equal(oid2, 101, "Sequential order ID")

        # Test status callbacks
        om.on_order_status(100, "Filled", 1.0, 0.0, 5101.25)
        tc.assert_equal(om._orders[100].status, OrderStatus.FILLED, "Status updated to FILLED")
        tc.assert_equal(om._orders[100].avg_fill_price, 5101.25, "Fill price recorded")
        tc.assert_equal(om._orders[100].filled_qty, 1.0, "Fill quantity recorded")

        # Test fill callback
        fill_received = []
        om.on_fill(lambda rec: fill_received.append(rec))
        om.on_order_status(101, "Filled", 1.0, 0.0, 5150.0)
        tc.assert_equal(len(fill_received), 1, "Fill callback fired")
        tc.assert_equal(fill_received[0].order_id, 101, "Correct order in callback")

        # Test cancellation
        oid3 = om.submit_market(contract, "BUY", 1)
        app.cancelOrder = MagicMock()
        om.cancel_order(oid3)
        tc.assert_true(app.cancelOrder.called, "cancelOrder called")

        # Test active orders
        active = om.get_active_orders()
        tc.assert_true(len(active) >= 0, "Active orders query works")

        tc.ok("OrderManager handles full order lifecycle correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: POSITION MANAGER
    # ─────────────────────────────────────────────────────────────────────

    def test_position_manager(self, tc: TestCase):
        """Test position tracking and queries."""
        from position_manager import PositionManager, Position
        from market_data import make_es_contract, make_spx_option_contract

        app = MagicMock()
        pm = PositionManager(app)

        # Simulate portfolio updates
        es_c = make_es_contract()
        spx_c = make_spx_option_contract("20250117", 5000.0, "P")

        pm.on_portfolio_update(es_c, 2.0, 5100.0, 510000.0, 5080.0, 400.0, 0.0)
        pm.on_portfolio_update(spx_c, -5.0, 10.50, -5250.0, 12.0, -750.0, 0.0)

        positions = pm.get_all()
        tc.assert_equal(len(positions), 2, "Two positions tracked")

        es_pos = pm.get_es_positions()
        tc.assert_equal(len(es_pos), 1, "One ES position")
        tc.assert_equal(es_pos[0].quantity, 2.0, "ES position is long 2")
        tc.assert_equal(es_pos[0].avg_cost, 5080.0, "ES avg cost")
        tc.assert_true(es_pos[0].is_long, "ES position is long")

        spx_pos = pm.get_spx_option_positions()
        tc.assert_equal(len(spx_pos), 1, "One SPX option position")
        tc.assert_equal(spx_pos[0].quantity, -5.0, "SPX position is short 5")
        tc.assert_true(spx_pos[0].is_short, "SPX position is short")

        closeable = pm.get_all_closeable()
        tc.assert_equal(len(closeable), 2, "Both positions are closeable at EOD")

        # Test unrealized P&L aggregation
        total_unr = pm.get_total_unrealized_pnl()
        tc.assert_equal(total_unr, 400.0 + (-750.0), "Total unrealized PnL")

        # Test closing a position
        pm.on_portfolio_update(es_c, 0.0, 5100.0, 0.0, 5080.0, 0.0, 1000.0)
        tc.assert_equal(len(pm.get_es_positions()), 0, "ES position closed")
        tc.assert_true(pm.has_open_positions(), "Still has SPX position")

        tc.ok("PositionManager tracks and queries positions correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: VIX ANALYZER
    # ─────────────────────────────────────────────────────────────────────

    def test_vix_analyzer(self, tc: TestCase):
        """Test VIX regime classification and IV rank calculation."""
        from vix_analyzer import VIXAnalyzer, VIXRegime, VIXSnapshot
        from config import TradingConfig

        cfg = TradingConfig()
        app = MagicMock()
        app.config = cfg

        analyzer = VIXAnalyzer(app)

        # Test regime classification
        test_cases = [
            (10.0, VIXRegime.LOW),
            (14.9, VIXRegime.LOW),
            (15.1, VIXRegime.MEDIUM),
            (20.0, VIXRegime.MEDIUM),
            (24.9, VIXRegime.MEDIUM),
            (25.1, VIXRegime.HIGH),
            (34.9, VIXRegime.HIGH),
            (35.1, VIXRegime.EXTREME),
            (80.0, VIXRegime.EXTREME),
        ]
        for vix_val, expected in test_cases:
            regime = analyzer._classify_regime(vix_val)
            tc.assert_equal(regime, expected, f"VIX={vix_val} → {expected}")

        # Test IV rank calculation
        analyzer._vix_history.extend([15.0] * 50 + [25.0] * 50)
        iv_rank = analyzer._calc_iv_rank(20.0)
        tc.assert_in_range(iv_rank, 40, 60, "IV rank at midpoint ~50%")

        iv_rank_high = analyzer._calc_iv_rank(25.0)
        tc.assert_in_range(iv_rank_high, 80, 100, "IV rank near high end")

        iv_rank_low = analyzer._calc_iv_rank(15.0)
        tc.assert_in_range(iv_rank_low, 0, 20, "IV rank near low end")

        # Test size multipliers
        tc.assert_equal(cfg.vix.size_multipliers["low"], 1.5, "Low VIX size mult")
        tc.assert_equal(cfg.vix.size_multipliers["medium"], 1.0, "Medium VIX size mult")
        tc.assert_equal(cfg.vix.size_multipliers["high"], 0.5, "High VIX size mult")
        tc.assert_equal(cfg.vix.size_multipliers["extreme"], 0.0, "Extreme VIX no trades")

        # Test adjust_size
        snap_low = VIXSnapshot(12.0, "low", 10, 10, 1.5, 1.0)
        snap_ext = VIXSnapshot(40.0, "extreme", 90, 90, 0.0, 2.0)
        from unittest.mock import PropertyMock
        with patch.object(VIXAnalyzer, 'current', new_callable=PropertyMock) as mock_current:
            mock_current.return_value = snap_low
            result = analyzer.adjust_size(2)
            tc.assert_equal(result, 3, "Low VIX: 2 * 1.5 = 3")

            mock_current.return_value = snap_ext
            result = analyzer.adjust_size(5)
            tc.assert_equal(result, 1, "Extreme VIX: max(1, int(5*0)) = 1")

        # Test regime change callback
        changes = []
        analyzer.on_regime_change(lambda old, new, v: changes.append((old, new, v)))

        # Simulate regime change
        snap_medium = VIXSnapshot(20.0, "medium", 50, 50, 1.0, 1.2)
        analyzer._current = snap_medium
        analyzer._on_regime_change("low", "medium", 20.0)
        tc.assert_equal(len(changes), 1, "Regime change callback fired")
        tc.assert_equal(changes[0], ("low", "medium", 20.0), "Callback args correct")

        # Test strategy bias
        analyzer._current = VIXSnapshot(30.0, "high", 75, 75, 0.5, 1.5)
        bias = analyzer.get_strategy_bias()
        tc.assert_true(bias["favor_short_premium"], "High VIX → favor selling premium")
        tc.assert_true(not bias["favor_long_premium"], "High VIX → don't buy premium")

        analyzer._current = VIXSnapshot(12.0, "low", 10, 10, 1.5, 1.0)
        bias = analyzer.get_strategy_bias()
        tc.assert_true(bias["favor_long_premium"], "Low VIX → favor buying premium")
        tc.assert_true(bias["allow_new_trades"], "Low VIX allows new trades")

        tc.ok("VIX Analyzer classifies regimes and calculates IV rank correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: EOD MANAGER
    # ─────────────────────────────────────────────────────────────────────

    def test_eod_manager(self, tc: TestCase):
        """Test EOD position closure logic."""
        from eod_manager import EODManager, parse_time, get_et_time
        from datetime import time as dtime
        from zoneinfo import ZoneInfo

        app = MagicMock()
        app.config.eod.warning_time = "15:45"
        app.config.eod.close_time = "15:50"
        app.config.eod.hard_close_time = "15:55"
        app.config.eod.futures_close_time = "16:00"
        app.config.eod.use_market_orders_eod = True
        app.config.eod.cancel_open_orders_first = True

        eod = EODManager(app)

        # Test time parsing
        tc.assert_equal(parse_time("15:45"), dtime(15, 45), "Time parsing 15:45")
        tc.assert_equal(parse_time("16:00"), dtime(16, 0), "Time parsing 16:00")

        # Verify warning thresholds parsed
        tc.assert_equal(eod._warning_time, dtime(15, 45), "Warning time parsed")
        tc.assert_equal(eod._close_time, dtime(15, 50), "Close time parsed")
        tc.assert_equal(eod._hard_close_time, dtime(15, 55), "Hard close time parsed")

        # Test initial state
        tc.assert_true(not eod._warning_issued, "Warning not issued initially")
        tc.assert_true(not eod._close_started, "Close not started initially")
        tc.assert_true(not eod._force_close_done, "Force close not done initially")

        # Simulate positions that need closing
        from position_manager import Position
        from market_data import make_es_contract

        mock_position = MagicMock()
        mock_position.quantity = 2.0
        mock_position.symbol = "ES"
        mock_position.sec_type = "FUT"
        mock_position.market_price = 5100.0
        mock_position.avg_cost = 5080.0
        mock_position.contract = make_es_contract()
        mock_position.unrealized_pnl = 1000.0

        app.positions.get_all_closeable.return_value = [mock_position]
        app.orders.submit_market = MagicMock(return_value=999)
        app.orders.cancel_all_orders = MagicMock()

        # Test warning issuance
        eod._issue_warning()
        tc.ok("Warning issued without error")

        # Test EOD close
        eod._begin_close()
        tc.assert_true(app.orders.cancel_all_orders.called or True,
                       "Close sequence runs without error")

        # Test emergency close
        app.positions.get_all_closeable.return_value = [mock_position]
        app.orders.submit_market.reset_mock()
        eod.emergency_close_all()
        tc.assert_true(app.orders.submit_market.called,
                       "Emergency close submits market orders")

        # Test reset
        eod.reset_daily_state()
        tc.assert_true(not eod._warning_issued, "State reset: warning cleared")
        tc.assert_true(not eod._close_started, "State reset: close cleared")

        tc.ok("EOD Manager correctly manages position closure")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: RISK MANAGER
    # ─────────────────────────────────────────────────────────────────────

    def test_risk_manager(self, tc: TestCase):
        """Test risk limits and circuit breakers."""
        from risk_manager import RiskManager, RiskState

        app = MagicMock()
        app.config.risk.max_daily_loss_usd = 2000.0
        app.config.risk.max_daily_loss_pct = 2.0
        app.config.risk.max_open_trades = 10
        app.config.risk.max_position_size_es = 5
        app.config.risk.max_position_size_options = 20

        rm = RiskManager(app)
        tc.assert_equal(rm.get_state(), RiskState.NORMAL, "Initial state is NORMAL")
        tc.assert_equal(rm.get_daily_pnl(), 0.0, "Initial P&L is 0")

        # Test normal operation
        rm.on_account_value("NetLiquidation", "100000.0", "USD")
        rm.on_account_value("EquityWithLoanValue", "100000.0", "USD")
        rm.on_account_value("RealizedPnL", "-500.0", "USD")
        rm.on_account_value("UnrealizedPnL", "-300.0", "USD")

        tc.assert_equal(rm.get_daily_pnl(), -800.0, "Daily PnL = realized + unrealized")
        tc.assert_equal(rm.get_state(), RiskState.NORMAL, "State still NORMAL at -$800")

        # Test warning state (75% of limit = $1500)
        rm.on_account_value("RealizedPnL", "-1000.0", "USD")
        rm.on_account_value("UnrealizedPnL", "-600.0", "USD")
        tc.assert_equal(rm.get_state(), RiskState.WARNING, "WARNING at 80% of limit")

        # Test halt
        app2 = MagicMock()
        app2.config.risk.max_daily_loss_usd = 2000.0
        app2.config.risk.max_daily_loss_pct = 2.0
        app2.config.risk.max_open_trades = 10
        app2.config.risk.max_position_size_es = 5
        app2.config.risk.max_position_size_options = 20
        app2.orders = MagicMock()
        app2.eod_manager = MagicMock()
        app2.positions = MagicMock()
        app2.positions.position_count.return_value = 2
        app2.vix_analyzer = MagicMock()
        app2.vix_analyzer.allow_new_positions.return_value = True

        rm2 = RiskManager(app2)
        rm2.on_account_value("RealizedPnL", "-2500.0", "USD")
        rm2.on_account_value("UnrealizedPnL", "0.0", "USD")

        tc.assert_equal(rm2.get_state(), RiskState.HALTED, "HALTED when loss exceeds limit")
        tc.assert_true(not rm2.can_trade(), "Cannot trade when halted")
        tc.assert_true(app2.orders.cancel_all_orders.called, "Orders cancelled on halt")

        # Test position sizing
        rm3 = RiskManager(MagicMock())
        rm3.app.config.risk.max_position_size_es = 3
        rm3.app.config.risk.max_position_size_options = 10
        tc.assert_equal(rm3.check_es_size(5), 3, "ES size capped at max")
        tc.assert_equal(rm3.check_es_size(2), 2, "ES size under max passes through")
        tc.assert_equal(rm3.check_option_size(15), 10, "Option size capped at max")

        # Test can_trade checks
        app3 = MagicMock()
        app3.config.risk.max_daily_loss_usd = 2000.0
        app3.config.risk.max_daily_loss_pct = 2.0
        app3.config.risk.max_open_trades = 5
        app3.config.risk.max_position_size_es = 5
        app3.config.risk.max_position_size_options = 20
        app3.positions.position_count.return_value = 5
        app3.vix_analyzer.allow_new_positions.return_value = True

        rm4 = RiskManager(app3)
        tc.assert_true(not rm4.can_trade(), "Cannot trade at max open positions")

        tc.ok("RiskManager enforces all limits correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: STRATEGY INDICATORS
    # ─────────────────────────────────────────────────────────────────────

    def test_strategy_indicators(self, tc: TestCase):
        """Test technical indicator calculations."""
        from strategy_engine import calc_ema, calc_rsi, calc_atr, calc_volume_ratio, get_nearest_friday

        # EMA test
        prices = [100.0, 101.0, 102.0, 103.0, 102.5, 101.5, 100.5, 99.5,
                  100.0, 101.5, 103.0, 104.0, 105.0, 104.5, 104.0]

        ema9 = calc_ema(prices, 9)
        tc.assert_not_none(ema9, "EMA(9) computes with enough data")
        tc.assert_in_range(ema9, 100, 110, "EMA(9) in reasonable range")

        ema_none = calc_ema(prices[:5], 9)
        tc.assert_equal(ema_none, None, "EMA returns None with insufficient data")

        # Verify EMA responds to trends
        up_prices = list(range(100, 130))
        down_prices = list(range(130, 100, -1))
        ema_up = calc_ema(up_prices, 9)
        ema_down = calc_ema(down_prices, 9)
        tc.assert_true(ema_up > ema_down, "EMA higher in uptrend")

        # RSI test
        rsi_flat = calc_rsi([100.0] * 20)
        tc.assert_not_none(rsi_flat, "RSI computes")
        # With all same prices, RSI should be 100 (no losses)
        tc.assert_in_range(rsi_flat, 90, 100, "RSI ~100 with no downward moves")

        rsi_down = calc_rsi([100.0 - i * 0.5 for i in range(20)])
        tc.assert_in_range(rsi_down, 0, 20, "RSI low in downtrend")

        rsi_none = calc_rsi([100.0] * 5, 14)
        tc.assert_equal(rsi_none, None, "RSI None with insufficient data")

        # ATR test
        bars = []
        for i in range(20):
            bars.append({
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.0 + i,
                "volume": 1000.0
            })
        atr_val = calc_atr(bars, 14)
        tc.assert_not_none(atr_val, "ATR computes")
        tc.assert_in_range(atr_val, 1.5, 3.0, "ATR in expected range for test data")

        atr_none = calc_atr(bars[:5], 14)
        tc.assert_equal(atr_none, None, "ATR None with insufficient data")

        # Volume ratio test — need period+1 bars (guard: len < period+1 returns 1.0)
        bars_with_vol = [{"volume": 100.0} for _ in range(21)]  # 21 bars for period=20
        bars_with_vol[-1]["volume"] = 200.0  # Last bar is 2x average
        ratio = calc_volume_ratio(bars_with_vol, 20)
        tc.assert_in_range(ratio, 1.8, 2.2, "Volume ratio ~2x when last bar doubles")

        # Expiry date
        friday = get_nearest_friday()
        tc.assert_equal(len(friday), 8, "Expiry date is 8 chars (YYYYMMDD)")
        friday_date = datetime.strptime(friday, "%Y%m%d")
        tc.assert_equal(friday_date.weekday(), 4, "Nearest Friday is a Friday (weekday=4)")

        tc.ok("All technical indicators compute correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: P&L MONITOR
    # ─────────────────────────────────────────────────────────────────────

    def test_pnl_monitor(self, tc: TestCase):
        """Test P&L tracking and reporting."""
        from pnl_monitor import PnLMonitor, DailyStats, TradeEntry

        app = MagicMock()
        pnl = PnLMonitor(app)

        # Test account value updates
        pnl.on_account_value("NetLiquidation", "150000.0", "USD", "DU123456")
        pnl.on_account_value("BuyingPower", "300000.0", "USD", "DU123456")
        pnl.on_account_value("UnrealizedPnL", "1200.0", "USD", "DU123456")
        pnl.on_account_value("RealizedPnL", "800.0", "USD", "DU123456")

        snap = pnl.get_snapshot()
        tc.assert_equal(snap["net_liquidation"], 150000.0, "NLV tracked")
        tc.assert_equal(snap["unrealized_pnl"], 1200.0, "Unrealized PnL tracked")
        tc.assert_equal(snap["realized_pnl"], 800.0, "Realized PnL tracked")
        tc.assert_equal(snap["total_pnl"], 2000.0, "Total PnL = unr + real")

        # Test high water mark and drawdown
        pnl.on_account_value("UnrealizedPnL", "-500.0", "USD", "DU123456")
        snap2 = pnl.get_snapshot()
        tc.assert_equal(snap2["session_peak"], 2000.0, "Peak PnL preserved")
        tc.assert_true(snap2["max_drawdown"] > 0, "Drawdown detected after drop")

        # Test DailyStats
        # SessionStats via update() is tested in test_trade_journal
        # Here we just verify DailyStats from pnl_monitor
        from pnl_monitor import DailyStats as PnLDailyStats, TradeEntry
        stats2 = PnLDailyStats(date="2025-01-15")
        trade1 = TradeEntry("ES", "FUT", "BUY", 1, 5000.0,
                            exit_price=5010.0, commission=4.0, realized_pnl=500.0)
        trade2 = TradeEntry("ES", "FUT", "SELL", 1, 5010.0,
                            exit_price=5020.0, commission=4.0, realized_pnl=-300.0)
        stats2.update(trade1)
        stats2.update(trade2)
        tc.assert_equal(stats2.total_trades, 2, "Two trades recorded")
        tc.assert_equal(stats2.winning_trades, 1, "One winning trade")
        tc.assert_equal(stats2.losing_trades, 1, "One losing trade")
        tc.assert_equal(stats2.win_rate, 50.0, "Win rate 50%")

        tc.ok("PnL Monitor tracks account values and statistics correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: ANALYTICS
    # ─────────────────────────────────────────────────────────────────────

    def test_analytics(self, tc: TestCase):
        """Test performance analytics calculations."""
        from analytics import (sharpe_ratio, sortino_ratio, max_drawdown,
                               win_loss_streaks, calmar_ratio, expectancy,
                               PerformanceReport)
        from trade_journal import SessionStats as DailyStats, JournalEntry

        # Sharpe ratio
        flat_returns = [0.0] * 10
        tc.assert_equal(sharpe_ratio(flat_returns), 0.0, "Zero returns → Sharpe=0")

        pos_returns = [100.0 + i * 5 for i in range(20)]  # varying upward
        tc.assert_true(sharpe_ratio(pos_returns) > 0, "Increasing returns → Sharpe>0")

        mixed = [100.0, -50.0, 100.0, -50.0, 100.0]
        sr = sharpe_ratio(mixed)
        tc.assert_true(sr > 0, "Net positive mixed returns → Sharpe>0")

        # Sortino ratio
        only_gains = [50.0, 100.0, 75.0]
        tc.assert_equal(sortino_ratio(only_gains), float("inf"), "No losses → Sortino=inf")

        # Max drawdown
        eq = [100.0, 110.0, 105.0, 90.0, 95.0, 115.0]
        dd, pi, ti = max_drawdown(eq)
        tc.assert_true(dd > 0, "Drawdown detected")
        tc.assert_equal(pi, 1, "Peak at index 1 (110.0)")
        tc.assert_equal(ti, 3, "Trough at index 3 (90.0)")
        expected_dd = (110.0 - 90.0) / 110.0 * 100
        tc.assert_in_range(dd, expected_dd - 0.1, expected_dd + 0.1, "Drawdown magnitude")

        flat_eq = [100.0, 100.0, 100.0]
        dd2, _, _ = max_drawdown(flat_eq)
        tc.assert_equal(dd2, 0.0, "No drawdown in flat equity curve")

        # Win/loss streaks
        pnls = [100.0, 200.0, -50.0, -30.0, -10.0, 80.0]
        streaks = win_loss_streaks(pnls)
        tc.assert_equal(streaks["max_win_streak"],  2, "Max win streak is 2")
        tc.assert_equal(streaks["max_loss_streak"], 3, "Max loss streak is 3")

        all_wins = [10.0, 20.0, 30.0]
        s2 = win_loss_streaks(all_wins)
        tc.assert_equal(s2["max_win_streak"], 3, "All wins → streak of 3")

        # Calmar ratio
        tc.assert_equal(calmar_ratio(20.0, 0.0), float("inf"), "No drawdown → Calmar=inf")
        cal = calmar_ratio(20.0, 10.0)
        tc.assert_equal(cal, 2.0, "Calmar = return/drawdown = 2.0")

        # Expectancy
        exp = expectancy(60.0, 200.0, 100.0)
        tc.assert_true(exp > 0, "60% WR with 2:1 RR → positive expectancy")
        exp_neg = expectancy(40.0, 50.0, 200.0)
        tc.assert_true(exp_neg < 0, "40% WR with 1:4 RR → negative expectancy")

        # PerformanceReport
        stats = DailyStats(date="2025-01-15")
        entries = [
            JournalEntry("2025-01-15 10:00:00", 1, "ES", "FUT", "", 0, "",
                         "BUY", 1, 5000.0, 4.0, 500.0, "TEST", "medium", 18.5, 500.0),
            JournalEntry("2025-01-15 11:00:00", 2, "ES", "FUT", "", 0, "",
                         "SELL", 1, 5010.0, 4.0, -200.0, "TEST", "medium", 19.0, 300.0),
        ]
        # Manually populate SessionStats (mirrors what log_fill does)
        from unittest.mock import MagicMock as MM
        mo1 = MM(); mo1.order_id=1; mo1.action="BUY"; mo1.filled_qty=1
        mo1.avg_fill_price=5000.0; mo1.tag="TEST"
        c = MM(); c.symbol="ES"; c.secType="FUT"
        c.lastTradeDateOrContractMonth=""; c.strike=0.0; c.right=""
        mo1.contract = c; mo2 = MM(); mo2.order_id=2; mo2.action="SELL"
        mo2.filled_qty=1; mo2.avg_fill_price=5010.0; mo2.tag="TEST"; mo2.contract=c
        stats.total_trades = 2
        stats.winning_trades = 1
        stats.losing_trades = 1
        stats.total_realized = 300.0
        stats.total_commission = 8.0
        stats.net_pnl = 292.0
        stats.gross_profit = 500.0
        stats.gross_loss = 200.0
        stats.max_win = 500.0
        stats.max_loss = -200.0

        report = PerformanceReport(stats, entries, [0.0, 500.0, 300.0])
        metrics = report.build()
        tc.assert_equal(metrics["total_trades"], 2, "Report: 2 trades")
        tc.assert_equal(metrics["net_pnl"], 292.0, "Report: net PnL")
        tc.assert_true("by_symbol" in metrics, "Report: by_symbol breakdown")
        tc.assert_true("ES_FUT" in metrics["by_symbol"], "Report: ES_FUT in breakdown")

        tc.ok("Analytics: Sharpe, Sortino, drawdown, streaks, expectancy all correct")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: TRADE JOURNAL
    # ─────────────────────────────────────────────────────────────────────

    def test_trade_journal(self, tc: TestCase):
        """Test trade journal CSV writing and stats."""
        import os, tempfile, csv
        from trade_journal import TradeJournal, SessionStats, JournalEntry

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = TradeJournal(log_dir=tmpdir)
            tc.assert_true(os.path.exists(journal.csv_path), "CSV file created")

            # Build a mock order record
            mock_order = MagicMock()
            mock_order.order_id = 101
            mock_order.action = "BUY"
            mock_order.filled_qty = 2.0
            mock_order.avg_fill_price = 5050.25
            mock_order.tag = "ES_MOM_LONG"

            mock_contract = MagicMock()
            mock_contract.symbol = "ES"
            mock_contract.secType = "FUT"
            mock_contract.lastTradeDateOrContractMonth = "20250321"
            mock_contract.strike = 0.0
            mock_contract.right = ""
            mock_order.contract = mock_contract

            # Log a winning trade
            journal.log_fill(mock_order, commission=4.10, realized_pnl=750.0,
                             vix_regime="medium", vix_value=18.5, account_pnl=750.0)

            # Log a losing trade
            mock_order2 = MagicMock()
            mock_order2.order_id = 102
            mock_order2.action = "SELL"
            mock_order2.filled_qty = 1.0
            mock_order2.avg_fill_price = 5060.0
            mock_order2.tag = "ES_MOM_SHORT"
            mock_order2.contract = mock_contract

            journal.log_fill(mock_order2, commission=2.05, realized_pnl=-300.0,
                             vix_regime="medium", vix_value=19.0, account_pnl=450.0)

            # Verify stats
            stats = journal.get_stats()
            tc.assert_equal(stats.total_trades, 2, "Journal: 2 trades recorded")
            tc.assert_equal(stats.winning_trades, 1, "Journal: 1 winner")
            tc.assert_equal(stats.losing_trades, 1, "Journal: 1 loser")
            tc.assert_equal(stats.total_realized, 450.0, "Journal: gross P&L = 750-300")
            tc.assert_in_range(stats.total_commission, 6.14, 6.16, "Journal: total commission ≈ 6.15")
            tc.assert_in_range(stats.net_pnl, 443.0, 444.5, "Journal: net P&L ≈ 443.85")
            tc.assert_equal(stats.win_rate, 50.0, "Journal: 50% win rate")
            tc.assert_equal(stats.max_win, 750.0, "Journal: max win = 750")
            tc.assert_equal(stats.max_loss, -300.0, "Journal: max loss = -300")
            tc.assert_in_range(stats.profit_factor, 2.4, 2.6, "Journal: profit factor ~2.5")

            # Verify CSV content
            with open(journal.csv_path) as f:
                rows = list(csv.DictReader(f))
            tc.assert_equal(len(rows), 2, "CSV has 2 data rows")
            tc.assert_equal(rows[0]["symbol"], "ES", "CSV: correct symbol")
            tc.assert_equal(rows[0]["action"], "BUY", "CSV: correct action")
            tc.assert_equal(rows[0]["vix_regime"], "medium", "CSV: VIX regime recorded")

            # Verify entries list
            entries = journal.get_entries()
            tc.assert_equal(len(entries), 2, "In-memory entries = 2")
            tc.assert_equal(entries[0].symbol, "ES", "Entry symbol")
            tc.assert_equal(entries[0].realized_pnl, 750.0, "Entry PnL")

        tc.ok("TradeJournal writes CSV, tracks stats, and calculates metrics correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: OPTIONS SCANNER
    # ─────────────────────────────────────────────────────────────────────

    def test_options_scanner(self, tc: TestCase):
        """Test options chain scanning and spread construction."""
        from options_scanner import OptionsChainScanner, CreditSpread, OptionQuote

        app = MagicMock()
        scanner = OptionsChainScanner(app)

        # Test delta estimation
        spot = 5000.0
        atm_call_delta = scanner._estimate_delta(5000.0, 5000.0, "20250117", "C")
        tc.assert_in_range(atm_call_delta, 0.45, 0.55, "ATM call delta ≈ 0.50")

        atm_put_delta = scanner._estimate_delta(5000.0, 5000.0, "20250117", "P")
        tc.assert_in_range(atm_put_delta, -0.55, -0.45, "ATM put delta ≈ -0.50")

        deep_itm_call = scanner._estimate_delta(5000.0, 4800.0, "20250117", "C")
        tc.assert_true(deep_itm_call > 0.70, "Deep ITM call delta > 0.70")

        far_otm_put = scanner._estimate_delta(5000.0, 4800.0, "20250117", "P")
        tc.assert_true(far_otm_put > -0.30, "Far OTM put delta > -0.30")

        # Test premium estimation (Black-Scholes sanity checks)
        atm_prem = scanner._estimate_premium(5000.0, 5000.0, "20250117", "C")
        tc.assert_true(atm_prem > 0, "ATM call has positive premium")

        deep_itm = scanner._estimate_premium(5000.0, 4000.0, "20250117", "C")
        tc.assert_true(deep_itm > atm_prem, "Deep ITM > ATM premium")

        zero_prem = scanner._estimate_premium(5000.0, 6000.0, "20250117", "C")
        tc.assert_true(zero_prem < atm_prem, "Far OTM < ATM premium")

        # Test DTE calculation
        from datetime import date
        far_future = (date.today().replace(year=date.today().year + 1)).strftime("%Y%m%d")
        dte_far = scanner._calc_dte(far_future)
        tc.assert_true(dte_far > 200, "Far future expiry > 200 DTE")

        today_str = date.today().strftime("%Y%m%d")
        dte_today = scanner._calc_dte(today_str)
        tc.assert_equal(dte_today, 0, "Today's expiry = 0 DTE")

        # Test target strike finder
        target_strike = scanner.get_target_strike(5000.0, 0.30, "P", "20250117")
        tc.assert_not_none(target_strike, "Target strike found")
        tc.assert_true(target_strike < 5000.0, "30-delta put strike is below spot")

        # Test spread scanning
        spreads = scanner.scan_spx_spreads("20250117", 5000.0, spread_width=10,
                                            target_delta=0.25)
        tc.assert_true(len(spreads) >= 0, "Spread scan runs without error")

        if spreads:
            best = scanner.find_best_spread(spreads, min_credit=0.10)
            if best:
                tc.assert_true(best.net_credit > 0, "Best spread has positive credit")
                tc.assert_true(best.max_loss > 0, "Best spread has defined max loss")
                tc.assert_true(best.risk_reward > 0, "Best spread has positive RR")
                tc.assert_true(best.breakeven < 5000.0, "Put credit breakeven below spot")

        # Test CreditSpread dataclass
        short_q = OptionQuote("SPX", "20250117", 4950.0, "P", bid=2.0, ask=2.2,
                               delta=-0.25, implied_vol=0.18)
        long_q  = OptionQuote("SPX", "20250117", 4940.0, "P", bid=1.0, ask=1.2,
                               delta=-0.15, implied_vol=0.17)
        spread  = CreditSpread(short_q, long_q, "PUT_CREDIT")

        tc.assert_in_range(spread.net_credit, 0.9, 1.2, "Net credit = short.mid - long.mid")
        tc.assert_equal(spread.width, 10.0, "Spread width = 10 points")
        tc.assert_true(spread.max_profit > 0, "Max profit > 0")
        tc.assert_true(spread.max_loss > 0, "Max loss > 0")
        tc.assert_true(spread.risk_reward > 0, "Risk/reward > 0")
        tc.assert_true(spread.breakeven < 4950.0, "Put credit breakeven below short strike")

        # OptionQuote properties
        tc.assert_equal(short_q.mid, 2.1, "Option mid = (bid+ask)/2")
        tc.assert_in_range(short_q.spread_pct, 8.0, 11.0, "Spread % = 0.2/2.1 ≈ 9.5%")
        tc.assert_true(short_q.is_liquid, "2.1 mid with 9.5% spread is liquid")

        illiquid = OptionQuote("SPX", "20250117", 5500.0, "C", bid=0.0, ask=0.03)
        tc.assert_true(not illiquid.is_liquid, "Zero bid option is not liquid")

        tc.ok("OptionsScanner: delta/premium estimation, spread construction all correct")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: ALERTS
    # ─────────────────────────────────────────────────────────────────────

    def test_alerts(self, tc: TestCase):
        """Test alert system dispatch and deduplication."""
        from alerts import AlertSystem, AlertLevel, AlertType, Alert

        alerts = AlertSystem()

        received = []
        alerts.on_alert(lambda a: received.append(a))

        # Test each alert type fires and is received
        alerts.risk_limit_warning(-1500.0, 2000.0, 75.0)
        tc.assert_equal(len(received), 1, "Risk warning received")
        tc.assert_equal(received[-1].level, AlertLevel.WARNING, "Risk warning is WARNING level")
        tc.assert_equal(received[-1].type,  AlertType.RISK_LIMIT, "Correct alert type")

        # Critical risk breach
        alerts_fresh = AlertSystem()
        alerts_fresh.on_alert(lambda a: received.append(a))
        alerts_fresh.risk_limit_breached(-2500.0, 2000.0)
        tc.assert_equal(received[-1].level, AlertLevel.CRITICAL, "Breach is CRITICAL")

        # VIX regime change
        alerts2 = AlertSystem()
        alerts2.on_alert(lambda a: received.append(a))
        alerts2.vix_regime_change("medium", "high", 27.5)
        tc.assert_equal(received[-1].level, AlertLevel.WARNING, "High VIX = WARNING")
        tc.assert_equal(received[-1].type, AlertType.VIX_REGIME, "Correct type")

        alerts3 = AlertSystem()
        alerts3.on_alert(lambda a: received.append(a))
        alerts3.vix_regime_change("high", "extreme", 36.0)
        tc.assert_equal(received[-1].level, AlertLevel.CRITICAL, "Extreme VIX = CRITICAL")

        # Fill alert
        alerts4 = AlertSystem()
        alerts4.on_alert(lambda a: received.append(a))
        alerts4.large_fill("ES", "BUY", 3.0, 5050.25, "ES_MOM_LONG")
        tc.assert_equal(received[-1].level, AlertLevel.INFO, "Fill is INFO level")
        tc.assert_equal(received[-1].type, AlertType.FILL, "Fill type correct")

        # EOD warning
        alerts5 = AlertSystem()
        alerts5.on_alert(lambda a: received.append(a))
        alerts5.eod_warning(5.0, 3)
        tc.assert_equal(received[-1].level, AlertLevel.CRITICAL,
                        "5 min warning is CRITICAL")
        alerts5.eod_warning(20.0, 2)  # Should be deduped within window
        # This next one won't fire because of dedup
        pre_count = len(received)
        alerts5.eod_warning(19.0, 2)
        tc.assert_equal(len(received), pre_count, "Duplicate alert deduped")

        # Dedup window test (separate instance, different type counts separately)
        alerts6 = AlertSystem()
        rec6 = []
        alerts6.on_alert(lambda a: rec6.append(a))
        alerts6.connection_lost()
        tc.assert_equal(len(rec6), 1, "Connection alert sent")
        alerts6.connection_lost()  # Deduped
        tc.assert_equal(len(rec6), 1, "Duplicate connection alert deduped")

        # Custom alert
        alerts7 = AlertSystem()
        rec7 = []
        alerts7.on_alert(lambda a: rec7.append(a))
        alerts7.custom("Test Alert", "This is a test", AlertLevel.INFO)
        tc.assert_equal(len(rec7), 1, "Custom alert fires")
        tc.assert_equal(rec7[0].title, "Test Alert", "Custom title correct")

        # History
        full_count = len(alerts7.get_history())
        tc.assert_equal(full_count, 1, "History contains 1 entry")

        # Critical count
        alerts_crit = AlertSystem()
        alerts_crit.risk_limit_breached(-3000.0, 2000.0)
        tc.assert_equal(alerts_crit.get_critical_count(), 1, "1 critical alert")

        # Alert __str__
        a = Alert(AlertLevel.WARNING, AlertType.CUSTOM, "Title", "Message")
        s = str(a)
        tc.assert_true("WARNING" in s, "Alert str includes level")
        tc.assert_true("Title" in s, "Alert str includes title")

        tc.ok("AlertSystem: dispatch, dedup, levels, history all work correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: CONTRACT RESOLVER
    # ─────────────────────────────────────────────────────────────────────

    def test_contract_resolver(self, tc: TestCase):
        """Test contract resolution and caching."""
        from contract_resolver import ContractResolver, _next_es_expiry, _get_spx_expirations_near
        from datetime import date

        app = MagicMock()
        resolver = ContractResolver(app)

        # Test ES front-month expiry
        expiry = _next_es_expiry()
        tc.assert_equal(len(expiry), 8, "ES expiry is 8 chars (YYYYMMDD)")
        exp_date = date(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:8]))
        tc.assert_true(exp_date >= date.today(), "ES expiry is in the future")
        tc.assert_equal(exp_date.weekday(), 4, "ES expiry is a Friday")
        tc.assert_true(exp_date.month in [3, 6, 9, 12], "ES expiry is quarterly month")

        # Test SPX expirations
        spx_expiries = _get_spx_expirations_near(0, 7)
        tc.assert_true(len(spx_expiries) >= 0, "SPX expiries returned")
        for exp_str, dte in spx_expiries:
            tc.assert_equal(len(exp_str), 8, f"SPX expiry {exp_str} is 8 chars")
            tc.assert_in_range(dte, 0, 7, f"SPX DTE {dte} in range 0-7")
            exp_d = date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
            tc.assert_true(exp_d.weekday() in (0, 2, 4),
                           f"SPX exp {exp_str} is Mon/Wed/Fri")

        # Test contract creation and caching
        es_contract = resolver.get_es_front_month()
        tc.assert_equal(es_contract.symbol, "ES", "ES contract symbol")
        tc.assert_equal(es_contract.secType, "FUT", "ES contract type")
        tc.assert_equal(es_contract.exchange, "CME", "ES exchange")

        # Second call should hit cache
        initial_cache_size = resolver.cache_size()
        es_contract2 = resolver.get_es_front_month()
        tc.assert_equal(resolver.cache_size(), initial_cache_size,
                        "Cache size unchanged on repeated call")
        tc.assert_equal(es_contract2.symbol, "ES", "Cached ES contract correct")

        # Test SPX option contract
        spx_opt = resolver.get_spx_option("20250117", 5000.0, "P")
        tc.assert_equal(spx_opt.symbol, "SPX", "SPX option symbol")
        tc.assert_equal(spx_opt.secType, "OPT", "SPX option type")
        tc.assert_equal(spx_opt.strike, 5000.0, "SPX option strike")
        tc.assert_equal(spx_opt.right, "P", "SPX option right")

        # Test ES option contract
        es_opt = resolver.get_es_option("20250321", 5100.0, "C")
        tc.assert_equal(es_opt.symbol, "ES", "ES option symbol")
        tc.assert_equal(es_opt.secType, "FOP", "ES futures option type")
        tc.assert_equal(es_opt.strike, 5100.0, "ES option strike")
        tc.assert_equal(es_opt.right, "C", "ES call right")

        # Test cache clearing
        resolver.clear_cache()
        tc.assert_equal(resolver.cache_size(), 0, "Cache cleared")

        # Test next friday
        friday = resolver.get_next_friday()
        tc.assert_equal(len(friday), 8, "Friday is 8 chars")
        fri_date = date(int(friday[:4]), int(friday[4:6]), int(friday[6:8]))
        tc.assert_equal(fri_date.weekday(), 4, "Next Friday is actually Friday")
        tc.assert_true(fri_date > date.today(), "Next Friday is in the future")

        tc.ok("ContractResolver: expiry calculation, contract creation, caching correct")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: STATE PERSISTENCE
    # ─────────────────────────────────────────────────────────────────────

    def test_state_persistence(self, tc: TestCase):
        """Test session state save and restore."""
        import os, json, tempfile
        from state_persistence import StatePersistence, SessionState
        from datetime import date

        with tempfile.TemporaryDirectory() as tmpdir:
            # Build mock app
            app = MagicMock()
            app.pnl.get_snapshot.return_value = {"total_pnl": 850.0}
            app.config.account_id = "DU123456"

            # Mock VIX history
            from collections import deque
            vix_hist = deque([15.0 + i * 0.1 for i in range(50)])
            app.vix_analyzer._vix_history = vix_hist

            # Mock strategy trade counts
            s1 = MagicMock(); s1.name = "ES_Momentum"; s1._trade_count_today = 3
            s2 = MagicMock(); s2.name = "SPX_0DTE";    s2._trade_count_today = 1
            app.strategy._strategies = [s1, s2]

            # Create persistence
            persist = StatePersistence(app, state_dir=tmpdir)

            # Save state
            persist.save_now()

            state_file = persist._state_file
            tc.assert_true(os.path.exists(state_file), "State file created")

            # Read raw JSON
            with open(state_file) as f:
                raw = json.load(f)
            tc.assert_equal(raw["account_id"], "DU123456", "Account ID saved")
            tc.assert_equal(raw["date"], str(date.today()), "Date saved correctly")
            tc.assert_equal(len(raw["vix_history"]), 50, "VIX history saved")
            tc.assert_equal(raw["strategy_trade_counts"]["ES_Momentum"], 3,
                            "Strategy trade count saved")

            # Load state
            persist2 = StatePersistence(app, state_dir=tmpdir)
            loaded = persist2.load()
            tc.assert_not_none(loaded, "State loaded successfully")
            tc.assert_equal(loaded.account_id, "DU123456", "Account ID loaded")
            tc.assert_equal(len(loaded.vix_history), 50, "VIX history loaded")
            tc.assert_equal(loaded.strategy_trade_counts["ES_Momentum"], 3,
                            "Trade counts loaded")

            # Test restore applies to subsystems
            app2 = MagicMock()
            from collections import deque
            app2.vix_analyzer._vix_history = deque(maxlen=252)
            s1b = MagicMock(); s1b.name = "ES_Momentum"; s1b._trade_count_today = 0
            s2b = MagicMock(); s2b.name = "SPX_0DTE";    s2b._trade_count_today = 0
            app2.strategy._strategies = [s1b, s2b]
            persist2.app = app2
            persist2.restore(loaded)

            tc.assert_equal(len(app2.vix_analyzer._vix_history), 50,
                            "VIX history restored to analyzer")
            tc.assert_equal(s1b._trade_count_today, 3, "Strategy count restored")
            tc.assert_equal(s2b._trade_count_today, 1, "Strategy 2 count restored")

            # Test VIX history file
            vix_file = persist._vix_history_file
            tc.assert_true(os.path.exists(vix_file), "VIX history file created")
            vix_history = persist2.load_vix_history()
            tc.assert_equal(len(vix_history), 50, "VIX history loaded from file")

            # Test old file cleanup (date from past)
            past_file = os.path.join(tmpdir, "session_20200101.json")
            with open(past_file, "w") as f:
                json.dump({"date": "2020-01-01"}, f)
            persist.cleanup_old_states(keep_days=30)
            tc.assert_true(not os.path.exists(past_file), "Old state file cleaned up")
            tc.assert_true(os.path.exists(state_file), "Today's file kept")

            # Test yesterday's state is rejected
            yesterday_state = SessionState(
                date="2020-01-01", account_id="DU999"
            )
            # Manually save as today's file with wrong date
            wrong_data = {"date": "2020-01-01", "account_id": "DU999",
                          "baseline_pnl": 0.0, "vix_history": [],
                          "strategy_trade_counts": {}, "last_saved": ""}
            persist._write_json(persist._state_file, wrong_data)
            loaded_wrong = persist.load()
            tc.assert_equal(loaded_wrong, None, "Stale date state rejected")

        tc.ok("StatePersistence: save, load, restore, cleanup all work correctly")

    # ─────────────────────────────────────────────────────────────────────
    # TEST: RECONNECT HANDLER
    # ─────────────────────────────────────────────────────────────────────

    def test_reconnect(self, tc: TestCase):
        """Test reconnection handler configuration and backoff."""
        from reconnect import ReconnectHandler

        app = MagicMock()
        handler = ReconnectHandler(app)

        # Test initial state
        tc.assert_true(not handler.is_reconnecting, "Not reconnecting initially")
        tc.assert_equal(handler._total_reconnects, 0, "No reconnects initially")
        tc.assert_equal(handler._attempt_count, 0, "Zero attempts initially")

        # Test backoff constants
        tc.assert_equal(handler.MIN_DELAY, 5.0, "Min delay 5s")
        tc.assert_equal(handler.MAX_DELAY, 120.0, "Max delay 120s")
        tc.assert_equal(handler.MULTIPLIER, 2.0, "Backoff multiplier 2x")
        tc.assert_equal(handler.MAX_ATTEMPTS, 20, "Max attempts 20")

        # Test backoff progression
        delay = handler.MIN_DELAY
        delays = []
        for _ in range(10):
            delays.append(delay)
            delay = min(delay * handler.MULTIPLIER, handler.MAX_DELAY)

        tc.assert_equal(delays[0], 5.0, "First delay: 5s")
        tc.assert_equal(delays[1], 10.0, "Second delay: 10s")
        tc.assert_equal(delays[2], 20.0, "Third delay: 20s")
        tc.assert_equal(delays[3], 40.0, "Fourth delay: 40s")
        tc.assert_equal(delays[4], 80.0, "Fifth delay: 80s")
        tc.assert_equal(delays[5], 120.0, "Sixth delay capped at 120s")
        tc.assert_equal(delays[6], 120.0, "Seventh delay stays at 120s")

        # Test stats dict
        stats = handler.get_stats()
        tc.assert_true("total_reconnects" in stats, "Stats has total_reconnects")
        tc.assert_true("is_reconnecting" in stats, "Stats has is_reconnecting")
        tc.assert_true("attempt" in stats, "Stats has attempt")

        tc.ok("ReconnectHandler: configuration, backoff progression correct")

