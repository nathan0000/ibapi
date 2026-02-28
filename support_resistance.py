import numpy as np


def calculate_levels(df, max_lines=5):

    highs = df["High"].values
    lows = df["Low"].values

    resistance = sorted(list(set(np.round(highs, 0))))[-max_lines:]
    support = sorted(list(set(np.round(lows, 0))))[:max_lines]

    return support, resistance
