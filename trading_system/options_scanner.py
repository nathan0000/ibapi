"""
Options Chain Scanner
Queries IBKR for option chains, finds tradeable strikes near target delta,
and constructs spread candidates for SPX and ES options.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from ibapi.contract import Contract

from market_data import make_spx_option_contract, make_es_option_contract

log = logging.getLogger("OptionsScanner")


@dataclass
class OptionQuote:
    """Single option strike with greeks and market data."""
    symbol:       str
    expiry:       str
    strike:       float
    right:        str        # "C" or "P"
    bid:          float = 0.0
    ask:          float = 0.0
    last:         float = 0.0
    volume:       int   = 0
    open_interest:int   = 0
    implied_vol:  float = 0.0
    delta:        float = 0.0
    gamma:        float = 0.0
    theta:        float = 0.0
    vega:         float = 0.0
    req_id:       int   = 0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0: return 0.0
        return (self.ask - self.bid) / self.mid * 100

    @property
    def is_liquid(self) -> bool:
        """Minimum liquidity filter."""
        return self.mid > 0.05 and self.spread_pct < 30

    @property
    def abs_delta(self) -> float:
        return abs(self.delta)


@dataclass
class SpreadLeg:
    quote:  OptionQuote
    action: str   # "BUY" or "SELL"

    @property
    def signed_mid(self) -> float:
        return self.quote.mid if self.action == "SELL" else -self.quote.mid


@dataclass
class CreditSpread:
    """A vertical credit spread (put or call)."""
    short_leg: OptionQuote
    long_leg:  OptionQuote
    spread_type: str    # "PUT_CREDIT" or "CALL_CREDIT"

    @property
    def net_credit(self) -> float:
        return self.short_leg.mid - self.long_leg.mid

    @property
    def width(self) -> float:
        return abs(self.short_leg.strike - self.long_leg.strike)

    @property
    def max_profit(self) -> float:
        return self.net_credit

    @property
    def max_loss(self) -> float:
        return self.width - self.net_credit

    @property
    def risk_reward(self) -> float:
        if self.max_loss <= 0: return 0.0
        return self.net_credit / self.max_loss

    @property
    def breakeven(self) -> float:
        if self.spread_type == "PUT_CREDIT":
            return self.short_leg.strike - self.net_credit
        else:
            return self.short_leg.strike + self.net_credit

    @property
    def net_delta(self) -> float:
        """Net delta (short is positive for put spread, negative for call spread)."""
        return self.short_leg.delta - self.long_leg.delta

    def describe(self) -> str:
        return (f"{self.spread_type} {self.short_leg.expiry} "
                f"{self.short_leg.strike:.0f}/{self.long_leg.strike:.0f} "
                f"credit=${self.net_credit:.2f} "
                f"width={self.width:.0f} RR={self.risk_reward:.2f}")


class OptionsChainScanner:
    """
    Queries IBKR option chains and scans for tradeable spreads.
    Uses market data subscriptions to get live quotes and greeks.
    """

    SCAN_TIMEOUT = 10   # seconds to wait for chain data

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._active_scans: Dict[int, dict] = {}

    def get_nearest_expiry(self, symbol: str, min_dte: int = 0,
                            max_dte: int = 7) -> List[str]:
        """
        Return available expiry dates within DTE range.
        Filters to the nearest weekly options.
        """
        today = date.today()
        expiries = []

        # Generate candidate Fridays (weekly options)
        for days_ahead in range(0, max_dte + 5):
            candidate = today + timedelta(days=days_ahead)
            dte = days_ahead

            # SPX/ES have Monday, Wednesday, Friday weeklies
            if candidate.weekday() in (0, 2, 4):  # Mon, Wed, Fri
                if min_dte <= dte <= max_dte:
                    expiries.append(candidate.strftime("%Y%m%d"))

        return expiries[:5]   # Return up to 5 nearest

    def scan_spx_spreads(self, expiry: str, underlying_price: float,
                          spread_width: int = 10,
                          target_delta: float = 0.30) -> List[CreditSpread]:
        """
        Scan for SPX credit spread candidates near target delta.
        Returns ranked list of spreads.
        """
        if underlying_price <= 0:
            return []

        # Generate strike range (±5% from current price)
        atm = round(underlying_price / 5) * 5
        strikes = [atm + i * 5 for i in range(-20, 21)]

        # For PUT credit spreads: look at strikes below ATM
        put_strikes = [s for s in strikes if s < underlying_price]
        # For CALL credit spreads: look at strikes above ATM
        call_strikes = [s for s in strikes if s > underlying_price]

        candidates = []

        # Build put credit spreads
        for short_strike in put_strikes[-8:]:  # 8 strikes below ATM
            long_strike = short_strike - spread_width
            short_delta = self._estimate_delta(underlying_price, short_strike,
                                                expiry, "P")
            if abs(short_delta) < 0.10 or abs(short_delta) > 0.50:
                continue

            # Estimate premiums
            short_premium = self._estimate_premium(underlying_price, short_strike,
                                                    expiry, "P")
            long_premium  = self._estimate_premium(underlying_price, long_strike,
                                                    expiry, "P")

            if short_premium <= 0:
                continue

            short_q = OptionQuote(
                symbol="SPX", expiry=expiry, strike=short_strike, right="P",
                bid=short_premium * 0.95, ask=short_premium * 1.05,
                delta=short_delta, implied_vol=0.15
            )
            long_q = OptionQuote(
                symbol="SPX", expiry=expiry, strike=long_strike, right="P",
                bid=long_premium * 0.95, ask=long_premium * 1.05,
                delta=self._estimate_delta(underlying_price, long_strike, expiry, "P"),
                implied_vol=0.15
            )

            spread = CreditSpread(short_q, long_q, "PUT_CREDIT")
            if spread.net_credit >= 0.50 and spread.risk_reward >= 0.05:
                candidates.append(spread)

        # Sort by risk/reward
        candidates.sort(key=lambda s: s.risk_reward, reverse=True)
        return candidates[:5]

    def _estimate_delta(self, spot: float, strike: float,
                         expiry: str, right: str) -> float:
        """
        Rough Black-Scholes delta estimate for scanning purposes.
        Uses simplified approximation — real greeks come from IBKR.
        """
        import math
        dte = self._calc_dte(expiry)
        T   = max(dte, 0.5) / 365
        vol = 0.18   # Estimated IV
        r   = 0.05

        try:
            d1 = (math.log(spot / strike) + (r + 0.5 * vol ** 2) * T) / (vol * math.sqrt(T))
            # Normal CDF approximation
            nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
            if right == "C":
                return nd1
            else:
                return nd1 - 1.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    def _estimate_premium(self, spot: float, strike: float,
                           expiry: str, right: str) -> float:
        """
        Rough Black-Scholes price estimate for scanning.
        Real prices come from IBKR market data subscription.
        """
        import math
        dte = self._calc_dte(expiry)
        T   = max(dte, 0.5) / 365
        vol = 0.18
        r   = 0.05

        try:
            d1 = (math.log(spot / strike) + (r + 0.5 * vol ** 2) * T) / (vol * math.sqrt(T))
            d2 = d1 - vol * math.sqrt(T)
            nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
            nd2 = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))

            if right == "C":
                price = spot * nd1 - strike * math.exp(-r * T) * nd2
            else:
                price = strike * math.exp(-r * T) * (1 - nd2) - spot * (1 - nd1)
            return max(0.05, price)
        except (ValueError, ZeroDivisionError):
            return 0.0

    def _calc_dte(self, expiry: str) -> int:
        """Days to expiry from today."""
        try:
            exp_date = date(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:8]))
            return max(0, (exp_date - date.today()).days)
        except (ValueError, IndexError):
            return 1

    def find_best_spread(self, spreads: List[CreditSpread],
                          min_credit: float = 0.50,
                          min_rr: float = 0.10) -> Optional[CreditSpread]:
        """Return the best spread meeting minimum criteria."""
        qualified = [
            s for s in spreads
            if s.net_credit >= min_credit and s.risk_reward >= min_rr
        ]
        if not qualified:
            return None
        return max(qualified, key=lambda s: s.net_credit * s.risk_reward)

    def get_target_strike(self, underlying: float, target_delta: float,
                           right: str, expiry: str) -> Optional[float]:
        """Find the strike closest to the target delta."""
        atm = round(underlying / 5) * 5
        search_range = range(-30, 31)  # ±150 points in 5-pt increments

        best_strike = None
        best_diff   = float("inf")

        for i in search_range:
            strike = atm + i * 5
            if right == "P" and strike >= underlying:
                continue
            if right == "C" and strike <= underlying:
                continue

            delta = self._estimate_delta(underlying, strike, expiry, right)
            diff  = abs(abs(delta) - target_delta)

            if diff < best_diff:
                best_diff   = diff
                best_strike = strike

        return best_strike
