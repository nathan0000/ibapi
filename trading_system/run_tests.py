"""
Standalone test runner — stubs ibapi so tests run without IBKR SDK.
Usage: python3 run_tests.py [module]
"""
import sys, types, logging

def _stub_ibapi():
    def make_mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    for n in ['ibapi', 'ibapi.client', 'ibapi.wrapper', 'ibapi.contract',
              'ibapi.order', 'ibapi.execution', 'ibapi.commission_report',
              'ibapi.common']:
        make_mod(n)

    class EClient:
        def __init__(self, wrapper=None): pass
        def connect(self, *a): pass
        def disconnect(self): pass
        def run(self): pass
        def reqMktData(self, *a): pass
        def cancelMktData(self, *a): pass
        def reqRealTimeBars(self, *a): pass
        def reqHistoricalData(self, *a): pass
        def reqAccountUpdates(self, *a): pass
        def reqCurrentTime(self): pass
        def placeOrder(self, *a): pass
        def cancelOrder(self, *a): pass
        def reqSecDefOptParams(self, *a): pass
        def isConnected(self): return False

    class EWrapper: pass

    class Contract:
        def __init__(self):
            self.symbol = ''
            self.secType = ''
            self.exchange = ''
            self.currency = ''
            self.lastTradeDateOrContractMonth = ''
            self.strike = 0.0
            self.right = ''
            self.multiplier = ''

    class Order:
        def __init__(self):
            self.action = ''
            self.totalQuantity = 0
            self.orderType = ''
            self.lmtPrice = 0.0
            self.auxPrice = 0.0
            self.tif = 'DAY'
            self.transmit = True
            self.parentId = 0
            self.ocaGroup = ''
            self.ocaType = 0

    class Execution:
        def __init__(self):
            self.execId = ''
            self.orderId = 0
            self.side = ''
            self.shares = 0.0
            self.price = 0.0

    class CommissionReport:
        def __init__(self):
            self.execId = ''
            self.commission = 0.0
            self.realizedPNL = 0.0

    class BarData: pass

    sys.modules['ibapi.client'].EClient = EClient
    sys.modules['ibapi.wrapper'].EWrapper = EWrapper
    sys.modules['ibapi.contract'].Contract = Contract
    sys.modules['ibapi.order'].Order = Order
    sys.modules['ibapi.execution'].Execution = Execution
    sys.modules['ibapi.commission_report'].CommissionReport = CommissionReport
    sys.modules['ibapi.common'].TickerId = int
    sys.modules['ibapi.common'].OrderId = int
    sys.modules['ibapi.common'].BarData = BarData


if __name__ == "__main__":
    _stub_ibapi()

    module = sys.argv[1] if len(sys.argv) > 1 else "all"

    fmt = "%(levelname)-8s %(name)-22s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    # Quiet noisy subsystem logs during tests
    for noisy in ("VIXAnalyzer", "EODManager", "RiskManager",
                  "OrderManager", "Alerts", "StatePersistence",
                  "ContractResolver", "TradeJournal"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    sys.path.insert(0, '.')
    from tests import TestRunner
    ok = TestRunner().run(module)
    sys.exit(0 if ok else 1)
