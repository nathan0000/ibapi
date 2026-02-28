# fundamental_filter.py

import yfinance as yf


def fundamental_score(ticker):

    try:
        info = yf.Ticker(ticker).info

        revenue_growth = info.get('revenueGrowth', 0) or 0
        roe = info.get('returnOnEquity', 0) or 0
        eps_growth = info.get('earningsQuarterlyGrowth', 0) or 0

        score = 0

        if revenue_growth > 0.05:
            score += 1
        if roe > 0.10:
            score += 1
        if eps_growth > 0:
            score += 1

        return score / 3

    except:
        return 0
