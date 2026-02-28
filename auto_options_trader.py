from screener_engine import get_top_breakout
from options_selector import select_option
from ibkr_options_execution import IBKROptionsTrader
from greeks_monitor import IBKRGreeksMonitor
from pnl_monitor import IBKRPnLMonitor
from vol_filter import classify_vol_regime
import yfinance as yf
import time

RISK_PER_TRADE = 3000


def get_last_price(symbol):
    df = yf.download(symbol, period="1d", interval="1m", progress=False, multi_level_index=False)
    return df["Close"].iloc[-1]


if __name__ == "__main__":

    vol = classify_vol_regime()
    print(f"Current volatility regime: {vol['regime']} with size factor {vol['size_factor']}")

    if vol["regime"] == "PANIC":
        print("Skipping due to high volatility")
        exit()

    signal = get_top_breakout()

    if not signal:
        print("No breakout signal")
        exit()

    ticker = signal["ticker"]
    direction = signal["signal"]

    last_price = get_last_price(ticker)
    print(f"Selected {ticker} with signal {direction} at price {last_price}")

    contract = select_option(ticker, last_price, direction)
    print(f"Selected option: {contract.symbol} {contract.lastTradeDateOrContractMonth} {contract.strike} {contract.right}")

    assumed_premium = last_price * 0.05
    adjusted_risk = RISK_PER_TRADE * vol["size_factor"]

    contracts = max(1, int(adjusted_risk / (assumed_premium * 100)))

    trader = IBKROptionsTrader()
    trader.connect("127.0.0.1", 4002, clientId=21)
    trader.start()
    time.sleep(3)

    trader.market_option_order(contract.symbol, last_price=last_price, signal=direction, quantity=contracts)

    # Start monitoring
    pnl = IBKRPnLMonitor()
    pnl.connect("127.0.0.1", 4002, clientId=22)
    pnl.start()

    greeks = IBKRGreeksMonitor()
    greeks.connect("127.0.0.1", 4002, clientId=23)
    greeks.start()
    time.sleep(3)
    greeks.subscribe_option(contract)

    print("System running...")
