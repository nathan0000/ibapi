from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.order import Order
import threading
import time


class IBKROptionsTrader(EWrapper, EClient):

    def __init__(self):
        EClient.__init__(self, self)
        self.nextOrderId = None

    def nextValidId(self, orderId):
        self.nextOrderId = orderId
        print("Connected to IBKR")

    def error(self, reqId, errorCode, errorString):
        if errorCode not in [2104, 2106, 2158]:
            print("Error:", errorCode, errorString)

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(2)

    def market_order(self, contract, quantity):

        order = Order()
        order.action = "BUY"
        order.orderType = "MKT"
        order.totalQuantity = quantity
        order.tif = "DAY"

        self.placeOrder(self.nextOrderId, contract, order)
        self.nextOrderId += 1
