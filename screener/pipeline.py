# pipeline.py

import sys
import os
import yfinance as yf
import pandas as pd
from pytickersymbols import PyTickerSymbols

sys.path.append("./")

from breakout_engine import detect_breakout
from technical_filter import technical_score
from fundamental_filter import fundamental_score
from sentiment_filter import sentiment_score



TOP_N = 10


def get_sp500_list():
    stock_data = PyTickerSymbols()
    SP500_stocks = stock_data.get_stocks_by_index('S&P 500')
    SP500 = [stock['symbol'] for stock in SP500_stocks]
    return SP500

def run_pipeline():

    tickers = get_sp500_list()
    breakout_candidates = []

    for ticker in tickers:

        try:
            df = yf.download(
                ticker,
                period="6mo",
                interval="1d",
                progress=False
            )

            if len(df) < 100:
                continue

            signal, breakout_strength = detect_breakout(df)

            if not signal:
                continue

            tech = technical_score(df, signal)
            fund = fundamental_score(ticker)
            sent = sentiment_score(ticker)

            total_score = (
                breakout_strength * 40 +
                tech * 25 +
                fund * 20 +
                sent * 15
            )

            breakout_candidates.append({
                "ticker": ticker,
                "signal": signal,
                "score": round(total_score, 2)
            })

        except:
            continue

    ranked = sorted(
        breakout_candidates,
        key=lambda x: x["score"],
        reverse=True
    )

    return ranked[:TOP_N]


if __name__ == "__main__":
    results = run_pipeline()
    print(results)
