# sentiment_filter.py

import yfinance as yf


def sentiment_score(ticker):

    try:
        t = yf.Ticker(ticker)
        rec = t.recommendations

        if rec is None or len(rec) < 5:
            return 0.5

        recent = rec.tail(5)
        bullish = (recent['To Grade'].str.contains("Buy")).sum()

        return bullish / 5

    except:
        return 0.5
