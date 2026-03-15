import yfinance as yf
from support_resistance import calculate_levels
from pytickersymbols import PyTickerSymbols


def get_top_breakout():




#    print(f"Loaded {list(SP500)} SP500 tickers.")
#SP500 = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","GLW","MU","LITE"]  # replace with full list

    for ticker in SP500:
        print(f"Checking {ticker} for breakout...")
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
