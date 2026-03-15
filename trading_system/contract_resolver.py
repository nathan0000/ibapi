"""
Contract Resolver
Resolves front-month futures contracts and validates option contracts.
Caches resolved contracts to avoid repeated IBKR requests.
"""

import logging
import threading
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

from ibapi.contract import Contract

from market_data import make_es_contract

log = logging.getLogger("ContractResolver")


def _next_es_expiry() -> str:
    """
    Return the front-month ES futures expiry (YYYYMMDD).
    ES futures expire on the 3rd Friday of March, June, September, December.
    Rolls to next quarter if within 5 days of expiry.
    """
    today = date.today()
    
    # ES quarterly months
    quarters = [3, 6, 9, 12]

    for year in [today.year, today.year + 1]:
        for month in quarters:
            if date(year, month, 1) < date(today.year, today.month, 1):
                continue

            # Find third Friday
            first_day = date(year, month, 1)
            first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
            third_friday = first_friday + timedelta(weeks=2)

            # Roll if within 5 days
            if third_friday >= today + timedelta(days=5):
                return third_friday.strftime("%Y%m%d")

    # Fallback: next December
    return date(today.year + 1, 12, 20).strftime("%Y%m%d")


def _is_0dte_available() -> bool:
    """Return True if today is a trading day (Mon-Fri, not holiday)."""
    return date.today().weekday() < 5


def _get_spx_expirations_near(min_dte: int = 0, max_dte: int = 7) -> list:
    """
    Generate candidate SPX expiry dates.
    SPX has M/W/F weekly expirations.
    """
    today = date.today()
    expiries = []

    for days in range(0, max_dte + 1):
        d = today + timedelta(days=days)
        dte = days
        # SPX weekly expirations: Mon(0), Wed(2), Fri(4)
        if d.weekday() in (0, 2, 4) and min_dte <= dte <= max_dte:
            expiries.append((d.strftime("%Y%m%d"), dte))

    return expiries


class ContractResolver:
    """
    Resolves and caches contract details.
    Provides validated Contract objects ready for order submission.
    """

    CACHE_TTL = 3600   # 1 hour cache validity

    def __init__(self, app):
        self.app = app
        self._lock  = threading.Lock()
        self._cache: Dict[str, Tuple[Contract, float]] = {}  # key -> (contract, timestamp)

    def _cache_key(self, **kwargs) -> str:
        return "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()))

    def _cached(self, key: str) -> Optional[Contract]:
        import time
        with self._lock:
            entry = self._cache.get(key)
            if entry and (time.time() - entry[1]) < self.CACHE_TTL:
                return entry[0]
        return None

    def _store(self, key: str, contract: Contract):
        import time
        with self._lock:
            self._cache[key] = (contract, time.time())

    def get_es_front_month(self) -> Contract:
        """Get the front-month ES futures contract."""
        key = "ES_FRONT"
        cached = self._cached(key)
        if cached:
            return cached

        expiry = _next_es_expiry()
        contract = make_es_contract(expiry)
        log.info(f"ES front month: {expiry}")
        self._store(key, contract)
        return contract

    def get_spx_option(self, expiry: str, strike: float,
                        right: str) -> Contract:
        """Get an SPX option contract."""
        from market_data import make_spx_option_contract
        key = self._cache_key(sym="SPX", exp=expiry, k=strike, r=right)
        cached = self._cached(key)
        if cached:
            return cached

        contract = make_spx_option_contract(expiry, strike, right)
        self._store(key, contract)
        return contract

    def get_es_option(self, expiry: str, strike: float,
                       right: str) -> Contract:
        """Get an ES futures option contract."""
        from market_data import make_es_option_contract
        key = self._cache_key(sym="ES_OPT", exp=expiry, k=strike, r=right)
        cached = self._cached(key)
        if cached:
            return cached

        contract = make_es_option_contract(expiry, strike, right)
        self._store(key, contract)
        return contract

    def get_spx_expiries(self, min_dte: int = 0, max_dte: int = 7) -> list:
        """Return list of (expiry_string, dte) tuples."""
        return _get_spx_expirations_near(min_dte, max_dte)

    def get_0dte_expiry(self) -> Optional[str]:
        """Return today's expiry if available (M/W/F only)."""
        if not _is_0dte_available():
            return None
        today = date.today()
        if today.weekday() in (0, 2, 4):   # Mon, Wed, Fri
            return today.strftime("%Y%m%d")
        return None

    def get_next_friday(self) -> str:
        """Return next Friday's date as YYYYMMDD."""
        today = date.today()
        days  = (4 - today.weekday()) % 7
        if days == 0:
            days = 7
        return (today + timedelta(days=days)).strftime("%Y%m%d")

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
        log.debug("Contract cache cleared")

    def cache_size(self) -> int:
        with self._lock:
            return len(self._cache)
