from ibapi.contract import Contract
import datetime


def create_option_contract(symbol, expiry, strike, right):
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "OPT"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.lastTradeDateOrContractMonth = expiry
    contract.strike = strike
    contract.right = right
    contract.multiplier = "100"
    return contract


def select_option(symbol, last_price, signal):

    today = datetime.date.today()
    next_friday = today + datetime.timedelta((4 - today.weekday()) % 7 + 7)

    expiry = next_friday.strftime("%Y%m%d")
    strike = round(last_price)
    right = "C" if signal == "BREAK_UP" else "P"

    return create_option_contract(symbol, expiry, strike, right)
