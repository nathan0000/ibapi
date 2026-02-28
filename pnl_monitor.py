from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import threading
import time


class IBKRPnLMonitor(EWrapper, EClient):

    def __init__(self):
        EClient.__init__(self, self)

    def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):

        print(f"Unrealized PnL: {unrealizedPnL}")

        if unrealizedPnL < -2000:
            print("STOP LOSS TRIGGERED - Liquidate positions")
            self.reqGlobalCancel()

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(2)
