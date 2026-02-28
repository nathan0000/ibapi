import yfinance as yf
from support_resistance import calculate_levels

SP500 = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA"]  # replace with full list


def get_top_breakout():

    for ticker in SP500:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False, multi_level_index=False)

        if df.empty:
            continue

        support, resistance = calculate_levels(df)

        last_close = df["Close"].iloc[-1]

        if last_close > max(resistance):
            return {"ticker": ticker, "signal": "BREAK_UP"}

        if last_close < min(support):
            return {"ticker": ticker, "signal": "BREAK_DOWN"}

    return None
