# breakout_engine.py

import numpy as np
from scipy.signal import argrelextrema

LOOKBACK_SR = 90


def calculate_support_resistance(df):

    highs = df['High'].values
    lows = df['Low'].values

    r_idx = argrelextrema(highs, np.greater_equal, order=5)[0]
    s_idx = argrelextrema(lows, np.less_equal, order=5)[0]

    resistance = sorted(highs[r_idx])[-5:]
    support = sorted(lows[s_idx])[:5]

    return support, resistance


def detect_breakout(df):

    support, resistance = calculate_support_resistance(
        df.tail(LOOKBACK_SR)
    )

    close = df['Close'].iloc[-1]
    volume = df['Volume'].iloc[-1]
    avg_vol = df['Volume'].rolling(20).mean().iloc[-1]

    breakout_strength = 0
    signal = None

    if resistance and close > max(resistance):
        signal = "BULLISH_BREAKOUT"
        breakout_strength = (close - max(resistance)) / close

    if support and close < min(support):
        signal = "BEARISH_BREAKDOWN"
        breakout_strength = (min(support) - close) / close

    if signal and volume > 1.3 * avg_vol:
        return signal, breakout_strength

    return None, 0
