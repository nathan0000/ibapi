"""
Performance Analytics
Post-session and intraday analytics:
- Sharpe ratio, Sortino ratio, Calmar ratio
- Maximum drawdown and recovery analysis
- Win/loss streaks
- By-instrument and by-strategy breakdowns
- HTML report generation
"""

import logging
import math
import os
from datetime import date, datetime
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

log = logging.getLogger("Analytics")
ET = ZoneInfo("America/New_York")


def sharpe_ratio(returns: List[float], risk_free: float = 0.0,
                 periods_per_year: int = 252) -> float:
    """Annualised Sharpe ratio from a list of periodic returns."""
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free / periods_per_year for r in returns]
    mean   = sum(excess) / len(excess)
    var    = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    std    = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def sortino_ratio(returns: List[float], risk_free: float = 0.0,
                  periods_per_year: int = 252) -> float:
    """Sortino ratio — penalises only downside volatility."""
    if len(returns) < 2:
        return 0.0
    excess  = [r - risk_free / periods_per_year for r in returns]
    mean    = sum(excess) / len(excess)
    neg     = [r for r in excess if r < 0]
    if not neg:
        return float("inf")
    down_var = sum(r ** 2 for r in neg) / len(neg)
    down_std = math.sqrt(down_var)
    if down_std == 0:
        return 0.0
    return (mean / down_std) * math.sqrt(periods_per_year)


def max_drawdown(equity_curve: List[float]) -> Tuple[float, int, int]:
    """
    Compute maximum drawdown from equity curve.
    Returns (max_dd_pct, peak_idx, trough_idx).
    """
    if len(equity_curve) < 2:
        return 0.0, 0, 0
    peak = equity_curve[0]
    peak_idx = trough_idx = 0
    max_dd   = 0.0
    cur_peak_idx = 0

    for i, val in enumerate(equity_curve):
        if val > peak:
            peak = val
            cur_peak_idx = i
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            peak_idx   = cur_peak_idx
            trough_idx = i

    return max_dd * 100, peak_idx, trough_idx


def win_loss_streaks(pnls: List[float]) -> Dict:
    """Calculate winning and losing streaks."""
    if not pnls:
        return {"max_win_streak": 0, "max_loss_streak": 0,
                "current_streak": 0, "current_type": "none"}

    max_win = max_loss = cur = 0
    cur_type = "win" if pnls[-1] > 0 else "loss"

    streak = 0
    win_streak = loss_streak = 0

    for p in pnls:
        if p > 0:
            streak = streak + 1 if streak > 0 else 1
            win_streak  = max(win_streak, streak)
        elif p < 0:
            streak = streak - 1 if streak < 0 else -1
            loss_streak = max(loss_streak, abs(streak))
        else:
            streak = 0

    return {
        "max_win_streak":  win_streak,
        "max_loss_streak": loss_streak,
        "current_streak":  abs(streak),
        "current_type":    cur_type,
    }


def calmar_ratio(total_return_pct: float, max_dd_pct: float) -> float:
    """Calmar ratio = annualised return / max drawdown."""
    if max_dd_pct == 0:
        return float("inf")
    return total_return_pct / max_dd_pct


def expectancy(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Mathematical expectancy per trade.
    Positive = edge exists. E = WR × AvgWin − LR × AvgLoss
    """
    loss_rate = 1.0 - win_rate / 100.0
    win_rate_dec = win_rate / 100.0
    return win_rate_dec * avg_win - loss_rate * avg_loss


class PerformanceReport:
    """
    Builds a comprehensive performance report from session data.
    Can output to log or HTML file.
    """

    def __init__(self, session_stats, entries: List, equity_curve: List[float] = None):
        self.stats = session_stats
        self.entries = entries
        self.equity_curve = equity_curve or []

    def build(self) -> Dict:
        """Calculate all metrics and return as dict."""
        pnls = [e.realized_pnl for e in self.entries if e.realized_pnl != 0]

        result = {
            "date":          self.stats.date,
            "total_trades":  self.stats.total_trades,
            "win_rate":      self.stats.win_rate,
            "profit_factor": self.stats.profit_factor,
            "net_pnl":       self.stats.net_pnl,
            "gross_pnl":     self.stats.total_realized,
            "commission":    self.stats.total_commission,
            "max_win":       self.stats.max_win,
            "max_loss":      self.stats.max_loss,
            "avg_win":       self.stats.average_win,
            "avg_loss":      self.stats.average_loss,
            "expectancy":    expectancy(
                self.stats.win_rate,
                self.stats.average_win,
                abs(self.stats.average_loss)
            ),
        }

        # Drawdown from equity curve
        if self.equity_curve:
            dd_pct, pi, ti = max_drawdown(self.equity_curve)
            result["max_drawdown_pct"] = dd_pct
            result["max_drawdown_peak_idx"] = pi
            result["max_drawdown_trough_idx"] = ti

        # Streaks
        if pnls:
            streaks = win_loss_streaks(pnls)
            result.update(streaks)

        # Sharpe (using per-trade returns)
        if len(pnls) >= 5:
            result["sharpe_ratio"]  = sharpe_ratio(pnls)
            result["sortino_ratio"] = sortino_ratio(pnls)

        # By-symbol breakdown
        by_sym: Dict[str, Dict] = {}
        for e in self.entries:
            sym = f"{e.symbol}_{e.sec_type}"
            if sym not in by_sym:
                by_sym[sym] = {"trades": 0, "pnl": 0.0, "commission": 0.0}
            by_sym[sym]["trades"]     += 1
            by_sym[sym]["pnl"]        += e.realized_pnl
            by_sym[sym]["commission"] += e.commission
        result["by_symbol"] = by_sym

        # By-regime breakdown
        by_regime: Dict[str, Dict] = {}
        for e in self.entries:
            regime = e.vix_regime or "unknown"
            if regime not in by_regime:
                by_regime[regime] = {"trades": 0, "pnl": 0.0}
            by_regime[regime]["trades"] += 1
            by_regime[regime]["pnl"]    += e.realized_pnl
        result["by_regime"] = by_regime

        return result

    def print_report(self):
        metrics = self.build()
        log.info("=" * 60)
        log.info(f"  PERFORMANCE REPORT — {metrics['date']}")
        log.info("=" * 60)
        log.info(f"  {'Trades':<25} {metrics['total_trades']:>10}")
        log.info(f"  {'Win Rate':<25} {metrics['win_rate']:>9.1f}%")
        log.info(f"  {'Profit Factor':<25} {metrics['profit_factor']:>10.2f}")
        log.info(f"  {'Expectancy/trade':<25} ${metrics.get('expectancy',0):>+9.2f}")
        log.info(f"  {'Net P&L':<25} ${metrics['net_pnl']:>+9.2f}")
        log.info(f"  {'Gross P&L':<25} ${metrics['gross_pnl']:>+9.2f}")
        log.info(f"  {'Commission':<25} ${metrics['commission']:>9.2f}")
        log.info(f"  {'Avg Win':<25} ${metrics['avg_win']:>+9.2f}")
        log.info(f"  {'Avg Loss':<25} ${metrics['avg_loss']:>+9.2f}")
        if "max_drawdown_pct" in metrics:
            log.info(f"  {'Max Drawdown':<25} {metrics['max_drawdown_pct']:>9.1f}%")
        if "sharpe_ratio" in metrics:
            log.info(f"  {'Sharpe Ratio':<25} {metrics['sharpe_ratio']:>10.2f}")
            log.info(f"  {'Sortino Ratio':<25} {metrics['sortino_ratio']:>10.2f}")
        if "max_win_streak" in metrics:
            log.info(f"  {'Max Win Streak':<25} {metrics['max_win_streak']:>10}")
            log.info(f"  {'Max Loss Streak':<25} {metrics['max_loss_streak']:>10}")
        log.info("\n  By Symbol:")
        for sym, d in metrics.get("by_symbol", {}).items():
            log.info(f"    {sym:<20} trades={d['trades']}  pnl=${d['pnl']:+,.2f}")
        log.info("\n  By VIX Regime:")
        for regime, d in metrics.get("by_regime", {}).items():
            log.info(f"    {regime:<12} trades={d['trades']}  pnl=${d['pnl']:+,.2f}")
        log.info("=" * 60)

    def save_html(self, path: str):
        """Save an HTML performance report."""
        metrics = self.build()
        rows = ""
        for e in self.entries:
            colour = "#d4edda" if e.realized_pnl >= 0 else "#f8d7da"
            rows += (
                f"<tr style='background:{colour}'>"
                f"<td>{e.timestamp}</td>"
                f"<td>{e.symbol}</td><td>{e.sec_type}</td>"
                f"<td>{e.action}</td><td>{e.quantity:.0f}</td>"
                f"<td>{e.fill_price:.4f}</td>"
                f"<td>${e.realized_pnl:+.2f}</td>"
                f"<td>{e.vix_regime}</td><td>{e.tag}</td>"
                "</tr>"
            )

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Trade Report {metrics['date']}</title>
<style>
  body{{font-family:Arial,sans-serif;margin:20px;background:#f9f9f9;}}
  h1{{color:#2c3e50;}}
  .metric{{display:inline-block;background:white;border:1px solid #ddd;
            border-radius:6px;padding:12px 20px;margin:8px;min-width:140px;
            box-shadow:2px 2px 4px rgba(0,0,0,.1);}}
  .metric .label{{font-size:11px;color:#777;text-transform:uppercase;}}
  .metric .value{{font-size:22px;font-weight:bold;color:#2c3e50;}}
  .pos{{color:#27ae60;}}.neg{{color:#e74c3c;}}
  table{{width:100%;border-collapse:collapse;background:white;
         box-shadow:2px 2px 4px rgba(0,0,0,.1);border-radius:6px;overflow:hidden;}}
  th{{background:#2c3e50;color:white;padding:8px 12px;text-align:left;font-size:12px;}}
  td{{padding:6px 12px;border-bottom:1px solid #eee;font-size:12px;}}
</style></head><body>
<h1>📊 Trade Report — {metrics['date']}</h1>
<div>
  <div class="metric"><div class="label">Trades</div>
    <div class="value">{metrics['total_trades']}</div></div>
  <div class="metric"><div class="label">Win Rate</div>
    <div class="value">{metrics['win_rate']:.1f}%</div></div>
  <div class="metric"><div class="label">Profit Factor</div>
    <div class="value">{metrics['profit_factor']:.2f}</div></div>
  <div class="metric"><div class="label">Net P&amp;L</div>
    <div class="value {'pos' if metrics['net_pnl'] >= 0 else 'neg'}">
    ${metrics['net_pnl']:+,.2f}</div></div>
  <div class="metric"><div class="label">Avg Win</div>
    <div class="value pos">${metrics['avg_win']:+,.2f}</div></div>
  <div class="metric"><div class="label">Avg Loss</div>
    <div class="value neg">${metrics['avg_loss']:+,.2f}</div></div>
  {'<div class="metric"><div class="label">Sharpe</div><div class="value">'+str(round(metrics.get("sharpe_ratio",0),2))+'</div></div>' if "sharpe_ratio" in metrics else ''}
</div>
<h2>Trades</h2>
<table><thead><tr>
  <th>Time</th><th>Symbol</th><th>Type</th>
  <th>Action</th><th>Qty</th><th>Price</th>
  <th>P&amp;L</th><th>VIX Regime</th><th>Tag</th>
</tr></thead><tbody>{rows}</tbody></table>
</body></html>"""

        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(html)
        log.info(f"HTML report saved: {path}")
