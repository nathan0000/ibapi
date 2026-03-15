"""
Debug Console
Interactive REPL for inspecting and controlling the live trading system.
Runs in a background thread; type commands in the terminal.

Commands:
  status          — System overview
  pnl             — Current P&L snapshot
  positions       — All open positions
  orders          — Active orders
  vix             — VIX analyzer state
  risk            — Risk manager state
  eod             — EOD manager state
  journal         — Today's trade journal stats
  bars [N]        — Last N ES price bars
  cancel all      — Cancel all open orders
  close all       — Emergency close all positions
  halt            — Disable all strategies
  resume          — Re-enable all strategies
  help            — Show this help
  exit / quit     — Stop the debug console
"""

import cmd
import logging
import sys
import threading
from typing import Optional

log = logging.getLogger("DebugConsole")


class TradingConsole(cmd.Cmd):
    """Interactive debug console for the live trading system."""

    intro  = "\n[Debug Console] Type 'help' for commands. Ctrl+C to exit.\n"
    prompt = "trading> "

    def __init__(self, app):
        super().__init__()
        self.app = app

    # ── STATUS ────────────────────────────────────────────────────────────

    def do_status(self, arg):
        """Print full system status."""
        app = self.app
        print("\n─── SYSTEM STATUS ─────────────────────────────────")
        print(f"  Connected:     {app.is_connected()}")
        print(f"  Mode:          {app.config.mode}")
        print(f"  Account:       {app.config.account_id}")
        if app.vix_analyzer:
            print(f"  VIX:           {app.vix_analyzer.get_summary()}")
        if app.risk:
            rs = app.risk.get_summary()
            print(f"  Risk State:    {rs['state']}")
            print(f"  Daily P&L:     ${rs['daily_pnl']:+,.2f}")
        if app.positions:
            print(f"  Open Positions:{app.positions.position_count()}")
        if app.orders:
            print(f"  Active Orders: {app.orders.get_open_order_count()}")
        if app.strategy:
            for name, s in app.strategy.get_status().items():
                print(f"  Strategy {name}: enabled={s['enabled']} "
                      f"trades={s['trades_today']} cooldown={s['in_cooldown']}")
        print("─────────────────────────────────────────────────\n")

    def do_pnl(self, arg):
        """Show P&L snapshot."""
        if not self.app.pnl:
            print("PnL monitor not running")
            return
        snap = self.app.pnl.get_snapshot()
        print("\n─── P&L SNAPSHOT ──────────────────────────────────")
        print(f"  Net Liquidation:  ${snap['net_liquidation']:>12,.2f}")
        print(f"  Total P&L:        ${snap['total_pnl']:>+12,.2f}")
        print(f"  Unrealized:       ${snap['unrealized_pnl']:>+12,.2f}")
        print(f"  Realized:         ${snap['realized_pnl']:>+12,.2f}")
        print(f"  Session Peak:     ${snap['session_peak']:>+12,.2f}")
        print(f"  Max Drawdown:     ${snap['max_drawdown']:>12,.2f}")
        print("─────────────────────────────────────────────────\n")

    def do_positions(self, arg):
        """List all open positions."""
        if not self.app.positions:
            print("Position manager not running")
            return
        positions = self.app.positions.get_all()
        if not positions:
            print("No open positions.")
            return
        print(f"\n{'Symbol':<12} {'Type':<6} {'Qty':>8} {'AvgCost':>10} "
              f"{'MktPx':>10} {'UnrPnL':>12}")
        print("─" * 65)
        for p in positions:
            print(f"{p.symbol:<12} {p.sec_type:<6} {p.quantity:>8.0f} "
                  f"{p.avg_cost:>10.2f} {p.market_price:>10.2f} "
                  f"{p.unrealized_pnl:>+12.2f}")
        print(f"\nTotal unrealized: ${self.app.positions.get_total_unrealized_pnl():+,.2f}\n")

    def do_orders(self, arg):
        """List active orders."""
        if not self.app.orders:
            print("Order manager not running")
            return
        orders = self.app.orders.get_active_orders()
        if not orders:
            print("No active orders.")
            return
        print(f"\n{'ID':<8} {'Action':<6} {'Qty':>5} {'Symbol':<10} "
              f"{'Type':<6} {'LmtPx':>8} {'Status'}")
        print("─" * 60)
        for o in orders:
            print(f"{o.order_id:<8} {o.action:<6} {o.quantity:>5.0f} "
                  f"{o.symbol:<10} {o.order_type:<6} "
                  f"{o.limit_price:>8.2f} {o.status.value}")
        print()

    def do_vix(self, arg):
        """Show VIX analyzer state."""
        if not self.app.vix_analyzer:
            print("VIX analyzer not running")
            return
        stats = self.app.vix_analyzer.get_statistics()
        print("\n─── VIX ANALYZER ─────────────────────────────────")
        for k, v in stats.items():
            if isinstance(v, float):
                print(f"  {k:<22}: {v:.4f}")
            else:
                print(f"  {k:<22}: {v}")
        print("─────────────────────────────────────────────────\n")

    def do_risk(self, arg):
        """Show risk manager state."""
        if not self.app.risk:
            print("Risk manager not running")
            return
        rs = self.app.risk.get_summary()
        print("\n─── RISK MANAGER ─────────────────────────────────")
        for k, v in rs.items():
            if isinstance(v, float):
                print(f"  {k:<22}: {v:+,.2f}")
            else:
                print(f"  {k:<22}: {v}")
        print("─────────────────────────────────────────────────\n")

    def do_eod(self, arg):
        """Show EOD manager state."""
        if not self.app.eod_manager:
            print("EOD manager not running")
            return
        status = self.app.eod_manager.get_status()
        print("\n─── EOD MANAGER ──────────────────────────────────")
        for k, v in status.items():
            print(f"  {k:<25}: {v}")
        print("─────────────────────────────────────────────────\n")

    def do_bars(self, arg):
        """Show last N ES price bars. Usage: bars [N]"""
        n = int(arg.strip()) if arg.strip().isdigit() else 10
        if not self.app.market_data:
            print("Market data not running")
            return
        bars = self.app.market_data.get_es_bars(n)
        if not bars:
            print("No ES bars available yet.")
            return
        print(f"\n{'#':<4} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Volume':>8}")
        print("─" * 50)
        for i, b in enumerate(bars[-n:], 1):
            print(f"{i:<4} {b['open']:>8.2f} {b['high']:>8.2f} "
                  f"{b['low']:>8.2f} {b['close']:>8.2f} {b['volume']:>8.0f}")
        print()

    def do_journal(self, arg):
        """Show today's trade journal statistics."""
        # Check if journal is available on app
        if hasattr(self.app, "journal") and self.app.journal:
            self.app.journal.print_session_report()
        else:
            print("Trade journal not attached to app.")

    # ── CONTROL ────────────────────────────────────────────────────────

    def do_cancel(self, arg):
        """Cancel orders. Usage: cancel all | cancel <order_id>"""
        if not self.app.orders:
            return
        if arg.strip().lower() == "all":
            self.app.orders.cancel_all_orders()
            print("Cancel all orders requested.")
        elif arg.strip().isdigit():
            oid = int(arg.strip())
            self.app.orders.cancel_order(oid)
            print(f"Cancel requested for order {oid}.")
        else:
            print("Usage: cancel all | cancel <order_id>")

    def do_close(self, arg):
        """Emergency close. Usage: close all"""
        if arg.strip().lower() == "all":
            confirm = input("Close ALL positions? This uses market orders. Type YES: ")
            if confirm.strip() == "YES":
                if self.app.eod_manager:
                    self.app.eod_manager.emergency_close_all()
                    print("Emergency close initiated.")
                else:
                    print("EOD manager not available.")
            else:
                print("Cancelled.")
        else:
            print("Usage: close all")

    def do_halt(self, arg):
        """Disable all strategies (existing positions unaffected)."""
        if self.app.strategy:
            for s in self.app.strategy._strategies:
                s._enabled = False
            print("All strategies HALTED. Existing positions will be managed normally.")

    def do_resume(self, arg):
        """Re-enable all strategies."""
        if self.app.strategy:
            for s in self.app.strategy._strategies:
                s._enabled = True
            print("All strategies RESUMED.")

    def do_prices(self, arg):
        """Show current market prices."""
        if not self.app.market_data:
            print("Market data not running")
            return
        es  = self.app.market_data.get_es_price()
        spx = self.app.market_data.get_spx_price()
        vix = self.app.market_data.get_vix()
        print(f"\n  ES:  {es:.2f}   SPX: {spx:.2f}   VIX: {vix:.2f}\n")

    def do_exit(self, arg):
        """Exit the debug console (system keeps running)."""
        print("Exiting debug console. System continues running.")
        return True

    def do_quit(self, arg):
        """Exit the debug console."""
        return self.do_exit(arg)

    def do_EOF(self, arg):
        return self.do_exit(arg)

    def emptyline(self):
        pass   # Don't repeat last command on empty enter


class DebugConsole:
    """Wrapper that runs TradingConsole in a background thread."""

    def __init__(self, app):
        self.app = app
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="DebugConsole", daemon=True
        )
        self._thread.start()
        log.info("Debug console started. Type 'help' for commands.")

    def _run(self):
        console = TradingConsole(self.app)
        try:
            console.cmdloop()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            log.debug(f"Debug console exited: {e}")
        self._running = False

    def stop(self):
        self._running = False
