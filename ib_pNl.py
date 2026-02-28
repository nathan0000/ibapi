"""
IBKR Continuous PnL Monitor
Works with TWS or IB Gateway
"""

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import *
import threading
import time


class IBKRMonitor(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.account = None
        self.positions = {}
        self.pnl_data = {}

    # -------------------------
    # Connection
    # -------------------------
    def nextValidId(self, orderId: int):
        print("Connected to IBKR.")
        self.reqAccountSummary(9001, "All", "NetLiquidation,UnrealizedPNL,RealizedPNL")
        self.reqPositions()
        self.reqPnL(7001, self.account, "")

    # -------------------------
    # Account Summary
    # -------------------------
    def accountSummary(self, reqId, account, tag, value, currency):
        self.account = account
        print(f"[ACCOUNT] {tag}: {value} {currency}")

    def accountSummaryEnd(self, reqId):
        print("Account summary loaded.\n")

    # -------------------------
    # Positions
    # -------------------------
    def position(self, account, contract, position, avgCost):
        self.positions[contract.symbol] = {
            "position": position,
            "avgCost": avgCost,
            "secType": contract.secType
        }
        print(f"[POSITION] {contract.symbol} | Qty: {position} | AvgCost: {avgCost}")

        # Subscribe to individual PnL
        self.reqPnLSingle(
            reqId=1000 + len(self.positions),
            account=account,
            modelCode="",
            conId=contract.conId
        )

    def positionEnd(self):
        print("\nAll positions received.\n")

    # -------------------------
    # Account PnL Stream
    # -------------------------
    def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):
        print(f"[ACCOUNT PnL] Daily: {dailyPnL:.2f} | Unreal: {unrealizedPnL:.2f} | Real: {realizedPnL:.2f}")

    # -------------------------
    # Per Position PnL Stream
    # -------------------------
    def pnlSingle(self, reqId, pos, dailyPnL, unrealizedPnL, realizedPnL, value):
        print(f"[POSITION PnL] Pos: {pos} | Daily: {dailyPnL:.2f} | Unreal: {unrealizedPnL:.2f} | Real: {realizedPnL:.2f}")

    # -------------------------
    # Errors
    # -------------------------
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson = "", arg5 = ""):
        print(f"[ERROR] {errorCode}: {errorString}")


def run_loop(app):
    app.run()


if __name__ == "__main__":
    app = IBKRMonitor()

    # Connect to:
    # TWS: 7497 (paper) / 7496 (live)
    # Gateway: 4002 (paper) / 4001 (live)   
    app.connect("127.0.0.1", 4002, clientId=2)

    api_thread = threading.Thread(target=run_loop, args=(app,), daemon=True)
    api_thread.start()

    time.sleep(2)

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        print("Disconnecting...")
        app.disconnect()
