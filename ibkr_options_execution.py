# ibkr_options_execution.py

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.order import Order
from ibapi.contract import Contract
import threading
import time
import datetime


class IBKROptionsTrader(EWrapper, EClient):

    def __init__(self):
        EClient.__init__(self, self)

        self.nextOrderId = None
        self.reqId = 1

        self.underlying_conid = None
        self.expirations = []
        self.strikes = []

        self.resolved_contract = None
        self.contract_details_received = False

    # ----------------------------
    # Connection
    # ----------------------------
    def nextValidId(self, orderId):
        self.nextOrderId = orderId
        print("Connected to IBKR")

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(2)

    # ----------------------------
    # Error handling
    # ----------------------------
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson='', arg5=''):
        if errorCode not in [2104, 2106, 2158]:
            print(f"IB Error {errorCode}: {errorString}")

    # ----------------------------
    # Step 1: Get underlying conId
    # ----------------------------
    def contractDetails(self, reqId, contractDetails):

        if contractDetails.contract.secType == "STK":
            self.underlying_conid = contractDetails.contract.conId

        else:
            self.resolved_contract = contractDetails.contract

    def contractDetailsEnd(self, reqId):
        self.contract_details_received = True

    # ----------------------------
    # Step 2: Option chain
    # ----------------------------
    def securityDefinitionOptionParameter(self, reqId, exchange,
                                           underlyingConId,
                                           tradingClass,
                                           multiplier,
                                           expirations,
                                           strikes):

        self.expirations = sorted(list(expirations))
        self.strikes = sorted(list(strikes))

    # ----------------------------
    # Helper: Wait loop
    # ----------------------------
    def wait_until(self, condition_func, timeout=10):
        start = time.time()
        while not condition_func():
            if time.time() - start > timeout:
                return False
            time.sleep(0.2)
        return True

    # ----------------------------
    # Main Order Method
    # ----------------------------
    def market_option_order(self, symbol, last_price, signal, quantity):

        # 1️⃣ Resolve underlying stock
        stock = Contract()
        stock.symbol = symbol
        stock.secType = "STK"
        stock.exchange = "SMART"
        stock.currency = "USD"

        self.contract_details_received = False
        self.reqContractDetails(self.reqId, stock)

        if not self.wait_until(lambda: self.contract_details_received):
            print("Underlying resolution timeout")
            return

        if not self.underlying_conid:
            print("Could not get underlying conId")
            return

        # 2️⃣ Request option chain
        self.reqId += 1
        self.reqSecDefOptParams(
            self.reqId,
            symbol,
            "",
            "STK",
            self.underlying_conid
        )

        if not self.wait_until(lambda: len(self.expirations) > 0):
            print("Option chain timeout")
            return

        # 3️⃣ Select nearest valid expiry > 7 days
        today = datetime.date.today()

        valid_expiry = None
        for exp in self.expirations:
            exp_date = datetime.datetime.strptime(exp, "%Y%m%d").date()
            if (exp_date - today).days > 7:
                valid_expiry = exp
                break

        if not valid_expiry:
            print("No suitable expiry found")
            return

        # 4️⃣ Select ATM strike from real strikes
        atm_strike = min(self.strikes, key=lambda x: abs(x - last_price))

        right = "C" if signal == "BREAK_UP" else "P"

        # 5️⃣ Build option contract
        option = Contract()
        option.symbol = symbol
        option.secType = "OPT"
        option.exchange = "SMART"
        option.currency = "USD"
        option.lastTradeDateOrContractMonth = valid_expiry
        option.strike = float(atm_strike)
        option.right = right
        option.multiplier = "100"

        # 6️⃣ Resolve option contract properly
        self.contract_details_received = False
        self.resolved_contract = None
        self.reqId += 1
        self.reqContractDetails(self.reqId, option)

        if not self.wait_until(lambda: self.contract_details_received):
            print("Option resolution timeout")
            return

        if not self.resolved_contract:
            print("Failed to resolve option contract")
            return

        # 7️⃣ Place order
        order = Order()
        order.action = "BUY"
        order.orderType = "MKT"
        order.totalQuantity = quantity
        order.tif = "DAY"

        print(f"Placing order {symbol} {valid_expiry} {atm_strike}{right}")
        print(f"Contract details: {self.resolved_contract}")

        self.placeOrder(self.nextOrderId, self.resolved_contract, order)
        self.nextOrderId += 1
