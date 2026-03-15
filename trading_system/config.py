"""
Trading System Configuration
All tunable parameters in one place.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class VIXRegimeConfig:
    """VIX thresholds for regime classification."""
    low_max: float = 15.0       # VIX < 15  → Low volatility (aggressive)
    medium_max: float = 25.0    # VIX 15-25 → Medium volatility (normal)
    high_max: float = 35.0      # VIX 25-35 → High volatility (defensive)
    # VIX > 35 → Extreme volatility (no new positions)

    # Position size multipliers per regime
    size_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "low":     1.5,
        "medium":  1.0,
        "high":    0.5,
        "extreme": 0.0,   # No new trades
    })

    # Stop-loss multipliers per regime
    stop_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "low":     1.0,
        "medium":  1.2,
        "high":    1.5,
        "extreme": 2.0,
    })


@dataclass
class RiskConfig:
    """Risk management parameters."""
    max_daily_loss_usd: float = 2000.0       # Hard stop on daily losses
    max_daily_loss_pct: float = 2.0          # % of account equity
    max_position_size_es: int = 5            # Max ES contracts
    max_position_size_options: int = 20      # Max option contracts
    max_open_trades: int = 10                # Max concurrent positions
    profit_target_daily_usd: float = 1000.0  # Daily profit target (optional)

    # Per-trade risk
    es_stop_ticks: int = 8          # Stop loss in ES ticks (1 tick = $12.50)
    es_target_ticks: int = 16       # Profit target in ES ticks
    option_stop_pct: float = 50.0   # Exit options at 50% loss
    option_target_pct: float = 100.0 # Exit options at 100% gain


@dataclass
class ESFuturesConfig:
    """E-mini S&P 500 Futures configuration."""
    symbol: str = "ES"
    exchange: str = "CME"
    currency: str = "USD"
    multiplier: float = 50.0     # $50 per point
    tick_size: float = 0.25      # Minimum tick

    # Strategy parameters
    entry_ema_fast: int = 9
    entry_ema_slow: int = 21
    entry_rsi_period: int = 14
    entry_rsi_oversold: float = 30.0
    entry_rsi_overbought: float = 70.0
    atr_period: int = 14
    volume_ma_period: int = 20
    min_volume_ratio: float = 1.2  # Volume must be 1.2x average


@dataclass
class SPXOptionsConfig:
    """SPX Index Options configuration."""
    symbol: str = "SPX"
    exchange: str = "CBOE"
    currency: str = "USD"
    multiplier: float = 100.0

    # Strategy parameters
    target_delta: float = 0.30        # Target delta for short options
    min_dte: int = 0                   # Minimum days to expiry (0DTE allowed)
    max_dte: int = 7                   # Maximum days to expiry
    min_premium: float = 1.0          # Min premium to collect ($)
    credit_spread_width: int = 10     # Width in points for credit spreads
    iv_rank_entry_min: float = 30.0   # Enter when IV Rank >= 30


@dataclass
class ESOptionsConfig:
    """ES Futures Options configuration."""
    symbol: str = "ES"
    exchange: str = "CME"
    currency: str = "USD"
    multiplier: float = 50.0

    # Strategy parameters
    target_delta: float = 0.25
    min_dte: int = 1
    max_dte: int = 14
    min_premium: float = 2.0
    iv_rank_entry_min: float = 25.0


@dataclass
class EODConfig:
    """End-of-day close configuration."""
    # Times in HH:MM ET format
    warning_time: str = "15:45"     # Warn about open positions
    close_time: str = "15:50"       # Start closing positions
    hard_close_time: str = "15:55"  # Force close all remaining
    futures_close_time: str = "16:00"  # ES futures close

    use_market_orders_eod: bool = True   # Use market orders for EOD close
    cancel_open_orders_first: bool = True


@dataclass
class TradingConfig:
    """Master configuration for the trading system."""
    # Connection
    mode: str = "paper"
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 1

    # Sub-configurations
    vix: VIXRegimeConfig = field(default_factory=VIXRegimeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    es: ESFuturesConfig = field(default_factory=ESFuturesConfig)
    spx_options: SPXOptionsConfig = field(default_factory=SPXOptionsConfig)
    es_options: ESOptionsConfig = field(default_factory=ESOptionsConfig)
    eod: EODConfig = field(default_factory=EODConfig)

    # Account
    account_id: str = ""           # Set after connection

    # Data subscriptions
    bar_size_seconds: int = 5      # Real-time bar size

    # Misc
    reconnect_attempts: int = 3
    reconnect_delay_secs: int = 5
    heartbeat_interval_secs: int = 30

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"
