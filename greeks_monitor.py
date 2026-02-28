from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import threading
import time


class IBKRGreeksMonitor(EWrapper, EClient):

    def __init__(self):
        EClient.__init__(self, self)
        self.reqId = 1000

    def tickOptionComputation(self, reqId, tickType, impliedVol, delta,
                              optPrice, pvDividend, gamma, vega, theta,
                              undPrice):

        print(f"""
IV: {impliedVol}
Delta: {delta}
Gamma: {gamma}
Theta: {theta}
Vega: {vega}
Price: {optPrice}
Underlying: {undPrice}
""")

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(2)

    def subscribe_option(self, contract):
        self.reqId += 1
        self.reqMktData(self.reqId, contract, "", False, False, [])
