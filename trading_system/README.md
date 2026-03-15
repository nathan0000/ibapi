# IBKR Day Trading System

A production-ready day trading system using the **Interactive Brokers Native Python API** targeting:
- **ES E-mini S&P 500 Futures**
- **ES Futures Options (FOP)**  
- **SPX Index Options (0DTE credit spreads)**

## Architecture

```
main.py                  ← Entry point, CLI, signal handling
├── trading_app.py       ← EWrapper+EClient, dispatches all IBKR callbacks
├── config.py            ← All parameters in dataclasses
├── market_data.py       ← Price caching, bar buffers, option greeks
├── order_manager.py     ← Order lifecycle: create, submit, track, cancel
├── position_manager.py  ← Open position tracking and queries
├── vix_analyzer.py      ← VIX regime classification, IV rank, sizing
├── risk_manager.py      ← Daily loss limits, circuit breakers, position caps
├── eod_manager.py       ← Auto-close all positions before market close
├── strategy_engine.py   ← Three strategies + indicator calculations
├── pnl_monitor.py       ← Real-time P&L tracking and daily reports
├── logger_setup.py      ← Rotating file + console logging
└── tests/
    └── __init__.py      ← Full test suite (no live connection needed)
```

## Prerequisites

1. **TWS or IB Gateway** running with API enabled
2. **Python 3.10+**
3. Install IBKR Python API:
   ```bash
   pip install ibapi pytz
   # OR download from https://interactivebrokers.github.io/
   ```

## Quick Start

```bash
# Paper trading (default port 7497)
python main.py

# Live trading (port 7496)  
python main.py --mode live

# Custom connection
python main.py --host 192.168.1.10 --port 7497 --client-id 2

# Run all tests (no TWS needed)
python main.py --run-tests

# Run specific test module
python main.py --run-tests --test-module vix
python main.py --run-tests --test-module risk
python main.py --run-tests --test-module orders
```

## Test Modules

| Module | Tests |
|--------|-------|
| `connection` | Config defaults, app initialization, order ID management |
| `market_data` | Contract factories, quote caching, bar buffers, tick callbacks |
| `orders` | Order factories, submission, status tracking, fill callbacks |
| `positions` | Portfolio updates, position queries, EOD close list |
| `vix` | Regime classification, IV rank, size multipliers, bias |
| `eod` | Time parsing, warning/close/force logic, emergency close |
| `risk` | Loss limits, circuit breakers, position caps, can_trade |
| `strategy` | EMA, RSI, ATR, volume ratio, expiry date helpers |
| `pnl` | Account value tracking, drawdown, daily stats |

## VIX Regime System

| VIX Range | Regime | Size Mult | Stop Mult | Strategy |
|-----------|--------|-----------|-----------|----------|
| < 15 | LOW | 1.5× | 1.0× | Buy premium, directional |
| 15–25 | MEDIUM | 1.0× | 1.2× | Balanced |
| 25–35 | HIGH | 0.5× | 1.5× | Sell premium, reduce size |
| > 35 | EXTREME | 0× | 2.0× | **No new positions** |

## Strategies

### 1. ES Momentum (ES E-mini Futures)
- Entry: EMA(9/21) crossover + RSI confirmation + volume > 1.2× average
- Exit: Bracket order (8-tick stop, 16-tick target), widened in high VIX
- Max: 6 trades/day, 1-min cooldown

### 2. SPX 0DTE Credit Spreads
- Entry: IV Rank ≥ 30% + RSI extreme (>70 → call spread, <30 → put spread)
- Structure: 10-point wide credit spread, day-of expiry
- Max: 4 trades/day, 5-min cooldown

### 3. ES Directional Options
- Entry: Strong EMA trend + IV Rank < 40% (buy cheap options)
- Buys near-ATM calls (uptrend) or puts (downtrend) on next Friday expiry
- Max: 3 trades/day, 5-min cooldown

## EOD Auto-Close Timeline (ET)

| Time | Action |
|------|--------|
| 15:45 | Log all open positions (warning) |
| 15:50 | Cancel all orders → close with limit orders |
| 15:55 | Force close remaining with **market orders** |
| 16:00 | Final sweep — any remaining positions |

## Risk Management

- **Daily loss hard stop**: $2,000 (configurable)
- **Daily loss % stop**: 2% of equity
- **Max ES contracts**: 5
- **Max option contracts**: 20
- **Max open trades**: 10
- **Extreme VIX**: No new positions (all strategies blocked)
- **On limit breach**: Immediately cancel all orders + force close all positions

## Configuration

All parameters in `config.py`:
```python
TradingConfig(
    mode="paper",           # paper | live
    host="127.0.0.1",
    port=7497,             # 7497=paper, 7496=live
    client_id=1,
)
```

Key sections: `VIXRegimeConfig`, `RiskConfig`, `ESFuturesConfig`, 
`SPXOptionsConfig`, `ESOptionsConfig`, `EODConfig`

## Logging

- Console: configurable level (INFO by default)
- File: `logs/trading_YYYYMMDD.log` (rotating, 50MB max)
- Set level: `python main.py --log-level DEBUG`

## TWS Configuration

In TWS: Edit → Global Configuration → API → Settings:
- ✅ Enable ActiveX and Socket Clients
- Socket port: 7497 (paper) or 7496 (live)  
- ✅ Allow connections from localhost
- ✅ Read-Only API: **OFF** (needed for trading)
