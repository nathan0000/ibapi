"""
Live Dashboard
Prints a formatted status panel to the terminal every N seconds.
Shows: connection, VIX regime, account P&L, open positions, active orders,
strategy status, EOD countdown.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("Dashboard")
ET = ZoneInfo("America/New_York")

# ANSI colour codes
_C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "red":     "\033[31m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
    "cyan":    "\033[36m",
    "white":   "\033[37m",
    "bg_red":  "\033[41m",
    "bg_green":"\033[42m",
}

def _col(text: str, *codes) -> str:
    prefix = "".join(_C.get(c, "") for c in codes)
    return f"{prefix}{text}{_C['reset']}"


REGIME_COLOURS = {
    "low":     ("green",  "bold"),
    "medium":  ("yellow", "bold"),
    "high":    ("magenta","bold"),
    "extreme": ("red",    "bold", "bg_red"),
}

RISK_COLOURS = {
    "normal":  ("green",),
    "warning": ("yellow", "bold"),
    "halted":  ("red",    "bold"),
    "shutdown":("red",    "bg_red", "bold"),
}


class Dashboard:
    """
    Terminal dashboard for the trading system.
    Runs on its own thread, clears and redraws on each interval.
    """

    REFRESH_INTERVAL = 5    # seconds between redraws
    WIDTH = 72              # panel width

    def __init__(self, app):
        self.app = app
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._use_colour = os.isatty(1)   # Only colour when attached to TTY

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="Dashboard", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        time.sleep(3)   # Let subsystems warm up
        while self._running:
            try:
                self._render()
            except Exception as e:
                log.debug(f"Dashboard render error: {e}")
            time.sleep(self.REFRESH_INTERVAL)

    def _render(self):
        lines = self._build_panel()
        # Move cursor to top of panel (use log output for non-TTY)
        if self._use_colour:
            print("\n".join(lines), flush=True)
        else:
            for line in lines:
                log.info(line)

    def _build_panel(self) -> list:
        W = self.WIDTH
        sep  = "─" * W
        dsep = "═" * W
        now_et = datetime.now(ET)

        lines = [
            "",
            _col(f"{'  IBKR DAY TRADING SYSTEM':^{W}}", "bold", "cyan"),
            _col(dsep, "cyan"),
        ]

        # ── Connection & time ────────────────────────────────────────────
        connected = self.app.is_connected()
        conn_str  = (_col("● CONNECTED", "green") if connected
                     else _col("○ DISCONNECTED", "red"))
        mode_str  = _col(self.app.config.mode.upper(), "bold")
        time_str  = now_et.strftime("%Y-%m-%d  %H:%M:%S ET")

        lines.append(f"  {conn_str}   Mode: {mode_str}   {time_str}")
        lines.append(_col(sep, "cyan"))

        # ── VIX regime ───────────────────────────────────────────────────
        if self.app.vix_analyzer:
            snap = self.app.vix_analyzer.current
            if snap:
                regime_cols = REGIME_COLOURS.get(snap.regime, ("white",))
                regime_str  = _col(f"  {snap.regime.upper():^10}", *regime_cols)
                lines.append(
                    f"  VIX: {_col(f'{snap.value:.2f}', 'bold'):<8}"
                    f"Regime:{regime_str}"
                    f"IVRank: {snap.iv_rank:>5.0f}%   "
                    f"SizeMult: {_col(f'{snap.size_mult:.1f}x', 'bold')}"
                )
            else:
                lines.append("  VIX: waiting for data...")
        lines.append(_col(sep, "cyan"))

        # ── Account P&L ──────────────────────────────────────────────────
        if self.app.pnl and self.app.risk:
            pnl_snap = self.app.pnl.get_snapshot()
            risk_snap = self.app.risk.get_summary()
            total_pnl = pnl_snap["total_pnl"]
            nlv       = pnl_snap["net_liquidation"]
            drawdown  = pnl_snap["max_drawdown"]
            pnl_col   = "green" if total_pnl >= 0 else "red"
            risk_state = risk_snap["state"]
            risk_cols  = RISK_COLOURS.get(risk_state, ("white",))

            lines.append(
                f"  NLV: {_col(f'${nlv:>12,.2f}', 'bold')}"
                f"   P&L: {_col(f'${total_pnl:>+10,.2f}', pnl_col, 'bold')}"
                f"   DD: ${drawdown:>8,.2f}"
            )
            lines.append(
                f"  Realized: ${pnl_snap['realized_pnl']:>+10,.2f}   "
                f"Unrealized: ${pnl_snap['unrealized_pnl']:>+10,.2f}   "
                f"Risk: {_col(risk_state.upper(), *risk_cols)}"
            )
        lines.append(_col(sep, "cyan"))

        # ── Open positions ───────────────────────────────────────────────
        if self.app.positions:
            positions = self.app.positions.get_all()
            lines.append(f"  {_col('POSITIONS', 'bold')}  ({len(positions)} open)")
            if positions:
                lines.append(
                    f"  {'Symbol':<10} {'Type':<5} {'Qty':>6} "
                    f"{'AvgCost':>9} {'MktPx':>9} {'UnrPnL':>10}"
                )
                for p in positions[:8]:   # Show max 8
                    pnl_col = "green" if p.unrealized_pnl >= 0 else "red"
                    lines.append(
                        f"  {p.symbol:<10} {p.sec_type:<5} {p.quantity:>6.0f} "
                        f"{p.avg_cost:>9.2f} {p.market_price:>9.2f} "
                        f"{_col(f'{p.unrealized_pnl:>+10.2f}', pnl_col)}"
                    )
                if len(positions) > 8:
                    lines.append(f"  ... and {len(positions) - 8} more")
            else:
                lines.append("  (no open positions)")
        lines.append(_col(sep, "cyan"))

        # ── Active orders ────────────────────────────────────────────────
        if self.app.orders:
            active = self.app.orders.get_active_orders()
            lines.append(f"  {_col('ORDERS', 'bold')}  ({len(active)} active)")
            for o in active[:5]:
                lines.append(
                    f"  #{o.order_id:<6} {o.action:<5} {o.quantity:>4.0f} "
                    f"{o.symbol:<8} {o.order_type:<5} "
                    f"{'@'+str(o.limit_price) if o.limit_price else '':<10} "
                    f"{o.status.value}"
                )
        lines.append(_col(sep, "cyan"))

        # ── Strategy status ──────────────────────────────────────────────
        if self.app.strategy:
            status = self.app.strategy.get_status()
            strategy_parts = []
            for name, s in status.items():
                state = _col("ON", "green") if s["enabled"] else _col("OFF", "red")
                cooldown = " ⏸" if s["in_cooldown"] else ""
                strategy_parts.append(
                    f"{name}: {state} ({s['trades_today']}T{cooldown})"
                )
            lines.append("  " + "   ".join(strategy_parts))
        lines.append(_col(sep, "cyan"))

        # ── Market prices ────────────────────────────────────────────────
        if self.app.market_data:
            es  = self.app.market_data.get_es_price()
            spx = self.app.market_data.get_spx_price()
            vix = self.app.market_data.get_vix()
            lines.append(
                f"  {_col('ES', 'bold')} {es:>8.2f}   "
                f"{_col('SPX', 'bold')} {spx:>8.2f}   "
                f"{_col('VIX', 'bold')} {vix:>6.2f}"
            )
        lines.append(_col(sep, "cyan"))

        # ── EOD countdown ────────────────────────────────────────────────
        if self.app.eod_manager:
            eod_status = self.app.eod_manager.get_status()
            close_time = datetime.now(ET).replace(hour=15, minute=50,
                                                   second=0, microsecond=0)
            remaining  = (close_time - datetime.now(ET)).total_seconds()
            if remaining > 0:
                mins, secs = divmod(int(remaining), 60)
                countdown  = f"{mins:02d}:{secs:02d}"
                eod_col    = "red" if mins < 15 else "yellow" if mins < 30 else "green"
                lines.append(
                    f"  EOD close in: {_col(countdown, eod_col, 'bold')}"
                    f"   Closed: {'Yes' if eod_status['positions_closed'] else 'No'}"
                )
            else:
                lines.append(f"  {_col('MARKET CLOSED / EOD COMPLETE', 'yellow')}")

        lines.append(_col(dsep, "cyan"))
        return lines
