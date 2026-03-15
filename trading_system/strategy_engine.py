"""
Strategy Engine
Implements three trading strategies:
1. ES Futures Momentum - trend following with EMA/RSI
2. SPX 0DTE Options   - credit spreads on extreme VIX
3. ES Options         - directional options plays

Each strategy runs on its own schedule, respects risk limits,
and adjusts based on VIX regime.
"""

import logging
import threading
import time
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Tuple

from market_data import make_es_contract, make_spx_option_contract, make_es_option_contract

log = logging.getLogger("Strategy")


# ─────────────────────────────────────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def calc_ema(values: List[float], period: int) -> Optional[float]:
    """Calculate EMA of last N values."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def calc_rsi(values: List[float], period: int = 14) -> Optional[float]:
    """Calculate RSI."""
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_atr(bars: List[Dict], period: int = 14) -> Optional[float]:
    """Calculate Average True Range."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period


def calc_volume_ratio(bars: List[Dict], period: int = 20) -> float:
    """Current volume vs average volume ratio."""
    if len(bars) < period + 1:
        return 1.0
    avg = sum(b["volume"] for b in bars[-period - 1:-1]) / period
    current = bars[-1]["volume"]
    return current / avg if avg > 0 else 1.0


def get_nearest_friday(ref_date: date = None) -> str:
    """Get nearest upcoming Friday for options expiry (YYYYMMDD)."""
    if ref_date is None:
        ref_date = date.today()
    days_ahead = 4 - ref_date.weekday()  # 4 = Friday
    if days_ahead <= 0:
        days_ahead += 7
    friday = ref_date + timedelta(days=days_ahead)
    return friday.strftime("%Y%m%d")


def get_today_expiry() -> str:
    """Today's date as expiry string (for 0DTE)."""
    return date.today().strftime("%Y%m%d")


# ─────────────────────────────────────────────────────────────────────────────
# BASE STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

class BaseStrategy:
    """Base class for all strategies."""

    def __init__(self, app, name: str):
        self.app = app
        self.name = name
        self._enabled = True
        self._last_entry_time: Optional[datetime] = None
        self._cooldown_seconds = 60
        self._trade_count_today = 0
        self._max_trades_per_day = 10

    def is_enabled(self) -> bool:
        return self._enabled

    def is_in_cooldown(self) -> bool:
        if not self._last_entry_time:
            return False
        elapsed = (datetime.now() - self._last_entry_time).total_seconds()
        return elapsed < self._cooldown_seconds

    def can_trade(self) -> bool:
        if not self._enabled:
            return False
        if self.is_in_cooldown():
            return False
        if self._trade_count_today >= self._max_trades_per_day:
            log.warning(f"{self.name}: Max trades per day reached")
            return False
        if not self.app.risk.can_trade():
            return False
        if not self.app.vix_analyzer.allow_new_positions():
            log.info(f"{self.name}: Skipping - VIX EXTREME")
            return False
        return True

    def record_entry(self):
        self._last_entry_time = datetime.now()
        self._trade_count_today += 1


# ─────────────────────────────────────────────────────────────────────────────
# ES FUTURES MOMENTUM STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

class ESMomentumStrategy(BaseStrategy):
    """
    E-mini S&P 500 Futures Momentum Strategy.
    
    Entry: EMA crossover + RSI confirmation + volume filter
    Exit:  Bracket order (stop + target), adjusted for VIX regime
    """

    def __init__(self, app):
        super().__init__(app, "ES_Momentum")
        self._cooldown_seconds = 120
        self._max_trades_per_day = 6

    def evaluate(self) -> Optional[Dict]:
        """
        Evaluate entry conditions. Returns signal dict or None.
        Signal: {action, quantity, stop_ticks, target_ticks, reason}
        """
        if not self.can_trade():
            return None

        bars = self.app.market_data.get_es_bars(50)
        if len(bars) < 30:
            log.debug("ES: Insufficient bar data")
            return None

        closes = [b["close"] for b in bars]
        cfg = self.app.config.es

        ema_fast = calc_ema(closes, cfg.entry_ema_fast)
        ema_slow = calc_ema(closes, cfg.entry_ema_slow)
        rsi_val = calc_rsi(closes, cfg.entry_rsi_period)
        vol_ratio = calc_volume_ratio(bars, cfg.volume_ma_period)

        if None in (ema_fast, ema_slow, rsi_val):
            return None

        current_price = closes[-1]
        vix = self.app.vix_analyzer

        # Determine position size based on VIX regime
        base_qty = 1
        qty = vix.adjust_size(base_qty)
        qty = self.app.risk.check_es_size(qty)

        # Stop and target, widened in high vol
        base_stop = self.app.config.risk.es_stop_ticks
        base_target = self.app.config.risk.es_target_ticks
        stop_ticks = vix.adjust_stop(base_stop)
        target_ticks = base_target  # Keep target fixed

        tick = 0.25
        atr_val = calc_atr(bars, cfg.atr_period) or (stop_ticks * tick)

        # LONG signal: fast EMA crosses above slow EMA + RSI not overbought
        if (ema_fast > ema_slow
                and rsi_val < cfg.entry_rsi_overbought
                and rsi_val > 40
                and vol_ratio >= cfg.min_volume_ratio):
            
            # Confirm with recent price action (close above EMA)
            if current_price > ema_fast:
                log.info(f"ES LONG signal: EMA({cfg.entry_ema_fast})={ema_fast:.2f} "
                         f"EMA({cfg.entry_ema_slow})={ema_slow:.2f} "
                         f"RSI={rsi_val:.1f} Vol={vol_ratio:.2f}x")
                return {
                    "action": "BUY",
                    "quantity": qty,
                    "stop_price": current_price - stop_ticks * tick,
                    "target_price": current_price + target_ticks * tick,
                    "reason": f"EMA_CROSS_LONG RSI={rsi_val:.0f}",
                    "entry_price": current_price,
                }

        # SHORT signal: fast EMA crosses below slow EMA + RSI not oversold  
        elif (ema_fast < ema_slow
              and rsi_val > cfg.entry_rsi_oversold
              and rsi_val < 60
              and vol_ratio >= cfg.min_volume_ratio):
            
            if current_price < ema_fast:
                log.info(f"ES SHORT signal: EMA({cfg.entry_ema_fast})={ema_fast:.2f} "
                         f"EMA({cfg.entry_ema_slow})={ema_slow:.2f} "
                         f"RSI={rsi_val:.1f} Vol={vol_ratio:.2f}x")
                return {
                    "action": "SELL",
                    "quantity": qty,
                    "stop_price": current_price + stop_ticks * tick,
                    "target_price": current_price - target_ticks * tick,
                    "reason": f"EMA_CROSS_SHORT RSI={rsi_val:.0f}",
                    "entry_price": current_price,
                }

        return None

    def execute(self, signal: Dict):
        """Submit bracket order for ES trade."""
        contract = make_es_contract()
        ids = self.app.orders.submit_bracket(
            contract=contract,
            action=signal["action"],
            quantity=signal["quantity"],
            entry_price=None,  # Market entry
            stop_price=signal["stop_price"],
            target_price=signal["target_price"],
            tag=f"ES_MOM_{signal['action']}"
        )
        self.record_entry()
        log.info(f"ES Bracket submitted: {signal['action']} {signal['quantity']} "
                 f"stop={signal['stop_price']:.2f} target={signal['target_price']:.2f} "
                 f"orders={ids}")


# ─────────────────────────────────────────────────────────────────────────────
# SPX 0DTE OPTIONS STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

class SPXOptionsStrategy(BaseStrategy):
    """
    SPX 0-1 DTE Credit Spread Strategy.
    
    Entry: High VIX rank + mean reversion setup
    Structure: Put credit spread or Call credit spread
    Exit: 50% profit or 100% loss, or EOD
    """

    def __init__(self, app):
        super().__init__(app, "SPX_0DTE")
        self._cooldown_seconds = 300  # 5 min between entries
        self._max_trades_per_day = 4

    def evaluate(self) -> Optional[Dict]:
        """Evaluate SPX options entry conditions."""
        if not self.can_trade():
            return None

        vix_snap = self.app.vix_analyzer.current
        if not vix_snap:
            return None

        cfg = self.app.config.spx_options

        # Need elevated IV rank for premium selling
        if vix_snap.iv_rank < cfg.iv_rank_entry_min:
            log.debug(f"SPX: IV rank too low ({vix_snap.iv_rank:.0f}% < {cfg.iv_rank_entry_min}%)")
            return None

        spx_price = self.app.market_data.get_spx_price()
        if spx_price <= 0:
            return None

        # Determine spread direction based on market bias
        bars = self.app.market_data.get_es_bars(20)
        if len(bars) < 10:
            return None

        closes = [b["close"] for b in bars]
        rsi_val = calc_rsi(closes, 14)
        if rsi_val is None:
            return None

        expiry = get_today_expiry()  # 0DTE
        width = cfg.credit_spread_width
        qty = 1
        qty = self.app.risk.check_option_size(qty)

        # Bearish: RSI overbought → sell call spread
        if rsi_val > 70:
            short_strike = round(spx_price / 5) * 5 + 5  # Just OTM call
            long_strike = short_strike + width
            return {
                "spread_type": "CALL_CREDIT",
                "expiry": expiry,
                "short_strike": short_strike,
                "long_strike": long_strike,
                "quantity": qty,
                "reason": f"RSI_OB={rsi_val:.0f} IVR={vix_snap.iv_rank:.0f}%",
            }

        # Bullish: RSI oversold → sell put spread
        elif rsi_val < 30:
            short_strike = round(spx_price / 5) * 5 - 5  # Just OTM put
            long_strike = short_strike - width
            return {
                "spread_type": "PUT_CREDIT",
                "expiry": expiry,
                "short_strike": short_strike,
                "long_strike": long_strike,
                "quantity": qty,
                "reason": f"RSI_OS={rsi_val:.0f} IVR={vix_snap.iv_rank:.0f}%",
            }

        return None

    def execute(self, signal: Dict):
        """Submit the credit spread orders."""
        expiry = signal["expiry"]
        qty = signal["quantity"]

        if signal["spread_type"] == "CALL_CREDIT":
            # Sell the lower call, buy the higher call
            short_contract = make_spx_option_contract(
                expiry, signal["short_strike"], "C"
            )
            long_contract = make_spx_option_contract(
                expiry, signal["long_strike"], "C"
            )
            self.app.orders.submit_market(short_contract, "SELL", qty,
                                           tag="SPX_CALL_SHORT")
            time.sleep(0.1)
            self.app.orders.submit_market(long_contract, "BUY", qty,
                                           tag="SPX_CALL_LONG")

        else:  # PUT_CREDIT
            short_contract = make_spx_option_contract(
                expiry, signal["short_strike"], "P"
            )
            long_contract = make_spx_option_contract(
                expiry, signal["long_strike"], "P"
            )
            self.app.orders.submit_market(short_contract, "SELL", qty,
                                           tag="SPX_PUT_SHORT")
            time.sleep(0.1)
            self.app.orders.submit_market(long_contract, "BUY", qty,
                                           tag="SPX_PUT_LONG")

        self.record_entry()
        log.info(f"SPX spread submitted: {signal['spread_type']} "
                 f"{signal['short_strike']}/{signal['long_strike']} "
                 f"expiry={expiry} qty={qty}")


# ─────────────────────────────────────────────────────────────────────────────
# ES OPTIONS STRATEGY (Directional)
# ─────────────────────────────────────────────────────────────────────────────

class ESOptionsStrategy(BaseStrategy):
    """
    ES Futures Options - Directional Plays.
    Buy calls/puts on strong momentum signals with low IV rank.
    """

    def __init__(self, app):
        super().__init__(app, "ES_Options")
        self._cooldown_seconds = 300
        self._max_trades_per_day = 3

    def evaluate(self) -> Optional[Dict]:
        if not self.can_trade():
            return None

        vix_snap = self.app.vix_analyzer.current
        if not vix_snap:
            return None

        cfg = self.app.config.es_options
        bars = self.app.market_data.get_es_bars(30)
        if len(bars) < 20:
            return None

        closes = [b["close"] for b in bars]
        es_price = self.app.market_data.get_es_price()

        rsi_val = calc_rsi(closes)
        ema9 = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)

        if None in (rsi_val, ema9, ema21):
            return None

        expiry = get_nearest_friday()
        qty = 1
        qty = self.app.risk.check_option_size(qty)

        # Strong uptrend: buy call
        if (ema9 > ema21 * 1.001 and rsi_val > 60
                and vix_snap.iv_rank < 40):  # Lower IV = cheaper options
            
            strike = round(es_price / 5) * 5 + 5  # Slightly OTM
            return {
                "option_type": "CALL",
                "expiry": expiry,
                "strike": strike,
                "quantity": qty,
                "reason": f"STRONG_TREND_CALL EMA={ema9:.0f}>{ema21:.0f} RSI={rsi_val:.0f}",
            }

        # Strong downtrend: buy put
        elif (ema9 < ema21 * 0.999 and rsi_val < 40
              and vix_snap.iv_rank < 40):
            
            strike = round(es_price / 5) * 5 - 5  # Slightly OTM
            return {
                "option_type": "PUT",
                "expiry": expiry,
                "strike": strike,
                "quantity": qty,
                "reason": f"STRONG_TREND_PUT EMA={ema9:.0f}<{ema21:.0f} RSI={rsi_val:.0f}",
            }

        return None

    def execute(self, signal: Dict):
        contract = make_es_option_contract(
            signal["expiry"], signal["strike"], signal["option_type"][0]
        )
        self.app.orders.submit_market(
            contract, "BUY", signal["quantity"],
            tag=f"ES_OPT_{signal['option_type']}"
        )
        self.record_entry()
        log.info(f"ES option bought: {signal['option_type']} "
                 f"{signal['strike']} expiry={signal['expiry']}")


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class StrategyEngine:
    """
    Coordinates all strategies. Runs evaluation loop on background thread.
    """

    EVAL_INTERVAL = 30    # Evaluate strategies every 30 seconds

    def __init__(self, app):
        self.app = app
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Initialize strategies
        self.es_momentum = ESMomentumStrategy(app)
        self.spx_options = SPXOptionsStrategy(app)
        self.es_options = ESOptionsStrategy(app)

        self._strategies = [
            self.es_momentum,
            self.spx_options,
            self.es_options,
        ]

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="StrategyEngine",
            daemon=True
        )
        self._thread.start()
        log.info("Strategy Engine started.")

    def stop(self):
        self._running = False

    def _run_loop(self):
        """Main strategy evaluation loop."""
        # Initial delay to let data populate
        time.sleep(10)

        while self._running:
            try:
                self._evaluate_all()
            except Exception as e:
                log.error(f"Strategy eval error: {e}", exc_info=True)
            time.sleep(self.EVAL_INTERVAL)

    def _evaluate_all(self):
        """Evaluate all strategies and execute signals."""
        # Don't trade in EOD window
        if self.app.eod_manager.close_started:
            return

        # Log VIX regime periodically
        vix_state = self.app.vix_analyzer.get_summary()
        log.debug(f"Strategy eval | {vix_state}")

        for strategy in self._strategies:
            if not strategy.is_enabled():
                continue
            try:
                signal = strategy.evaluate()
                if signal:
                    log.info(f"Signal from {strategy.name}: {signal}")
                    strategy.execute(signal)
            except Exception as e:
                log.error(f"Strategy {strategy.name} error: {e}", exc_info=True)

    def enable_strategy(self, name: str):
        for s in self._strategies:
            if s.name == name:
                s._enabled = True
                log.info(f"Strategy {name} enabled")

    def disable_strategy(self, name: str):
        for s in self._strategies:
            if s.name == name:
                s._enabled = False
                log.info(f"Strategy {name} disabled")

    def get_status(self) -> Dict:
        return {
            s.name: {
                "enabled": s.is_enabled(),
                "trades_today": s._trade_count_today,
                "in_cooldown": s.is_in_cooldown(),
            }
            for s in self._strategies
        }


# Patch EODManager to expose close_started
def _patch_eod(eod):
    @property
    def close_started(self):
        return self._close_started
    import types
    eod.__class__.close_started = close_started
