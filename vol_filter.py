import yfinance as yf


def get_vol_data():
    vix = yf.download("^VIX", period="5d", interval="1d", progress=False, multi_level_index=False)
    vix3m = yf.download("^VIX3M", period="5d", interval="1d", progress=False, multi_level_index=False)

    return vix["Close"].iloc[-1], vix3m["Close"].iloc[-1]


def classify_vol_regime():

    vix, vix3m = get_vol_data()

    if vix > 30:
        return {"regime": "PANIC", "size_factor": 0.3}

    elif vix > 20:
        return {"regime": "ELEVATED", "size_factor": 0.6}

    elif vix > vix3m:
        return {"regime": "INVERSION", "size_factor": 0.5}

    else:
        return {"regime": "NORMAL", "size_factor": 1.0}
