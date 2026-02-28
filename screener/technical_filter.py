# technical_filter.py

import numpy as np


def technical_score(df, signal):

    df['EMA20'] = df['Close'].ewm(span=20).mean()
    df['EMA50'] = df['Close'].ewm(span=50).mean()

    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs = gain.rolling(14).mean() / loss.rolling(14).mean()
    df['RSI'] = 100 - (100 / (1 + rs))

    latest = df.iloc[-1]

    score = 0

    if signal == "BULLISH_BREAKOUT":
        if latest['EMA20'] > latest['EMA50']:
            score += 1
        if latest['RSI'] > 55:
            score += 1

    if signal == "BEARISH_BREAKDOWN":
        if latest['EMA20'] < latest['EMA50']:
            score += 1
        if latest['RSI'] < 45:
            score += 1

    return score / 2
