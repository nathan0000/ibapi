"""
Alert System
Sends alerts for critical trading events:
  - Daily loss limit approached / breached
  - VIX regime changes
  - Large fills
  - EOD close warnings
  - Connection drops / reconnections
  - Strategy errors

Output channels: console log (always), optional email via SMTP.
"""

import logging
import smtplib
import threading
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Callable, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("Alerts")
ET = ZoneInfo("America/New_York")


class AlertLevel(Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


class AlertType(Enum):
    RISK_LIMIT       = "risk_limit"
    VIX_REGIME       = "vix_regime"
    FILL             = "fill"
    EOD_WARNING      = "eod_warning"
    CONNECTION       = "connection"
    STRATEGY_ERROR   = "strategy_error"
    DAILY_PNL        = "daily_pnl"
    CUSTOM           = "custom"


@dataclass
class Alert:
    level:     AlertLevel
    type:      AlertType
    title:     str
    message:   str
    timestamp: datetime = field(default_factory=lambda: datetime.now(ET))
    data:      dict = field(default_factory=dict)

    def __str__(self) -> str:
        ts = self.timestamp.strftime("%H:%M:%S")
        return f"[{ts}] {self.level.value} | {self.title}: {self.message}"


@dataclass
class EmailConfig:
    """SMTP email configuration."""
    enabled:  bool  = False
    host:     str   = "smtp.gmail.com"
    port:     int   = 587
    user:     str   = ""
    password: str   = ""
    to:       List[str] = field(default_factory=list)
    min_level: AlertLevel = AlertLevel.WARNING


class AlertSystem:
    """
    Central alert dispatcher.
    Always logs to console; optionally sends email for WARNING+ alerts.
    Deduplicates: won't send the same alert type more than once per N seconds.
    """

    DEDUP_WINDOW = 60   # seconds between same-type alerts

    def __init__(self, email_cfg: Optional[EmailConfig] = None):
        self._email_cfg   = email_cfg or EmailConfig()
        self._lock        = threading.Lock()
        self._history:    List[Alert] = []
        self._last_sent:  dict = {}    # AlertType -> timestamp
        self._callbacks:  List[Callable] = []

    def on_alert(self, callback: Callable):
        """Register callback: cb(alert)  — runs on alert thread."""
        self._callbacks.append(callback)

    # ── PUBLIC ALERT METHODS ──────────────────────────────────────────────

    def risk_limit_warning(self, pnl: float, limit: float, pct: float):
        self._send(Alert(
            level=AlertLevel.WARNING,
            type=AlertType.RISK_LIMIT,
            title="Risk Limit Warning",
            message=f"Daily P&L ${pnl:+,.2f} is {pct:.0f}% of limit (${limit:,.2f})",
            data={"pnl": pnl, "limit": limit}
        ))

    def risk_limit_breached(self, pnl: float, limit: float):
        self._send(Alert(
            level=AlertLevel.CRITICAL,
            type=AlertType.RISK_LIMIT,
            title="RISK LIMIT BREACHED — TRADING HALTED",
            message=f"Daily P&L ${pnl:+,.2f} exceeded limit of ${limit:,.2f}. "
                    f"All positions being closed.",
            data={"pnl": pnl, "limit": limit}
        ))

    def vix_regime_change(self, old_regime: str, new_regime: str, vix: float):
        level = (AlertLevel.CRITICAL if new_regime == "extreme"
                 else AlertLevel.WARNING if new_regime in ("high", "extreme")
                 else AlertLevel.INFO)
        self._send(Alert(
            level=level,
            type=AlertType.VIX_REGIME,
            title=f"VIX Regime: {old_regime.upper()} → {new_regime.upper()}",
            message=f"VIX={vix:.2f}. Size multiplier adjusted accordingly.",
            data={"old": old_regime, "new": new_regime, "vix": vix}
        ))

    def large_fill(self, symbol: str, action: str, qty: float,
                   price: float, tag: str = ""):
        self._send(Alert(
            level=AlertLevel.INFO,
            type=AlertType.FILL,
            title=f"Fill: {action} {qty:.0f} {symbol}",
            message=f"@ {price:.4f}  tag={tag}",
            data={"symbol": symbol, "qty": qty, "price": price}
        ))

    def eod_warning(self, minutes_remaining: float, open_positions: int):
        level = AlertLevel.CRITICAL if minutes_remaining <= 10 else AlertLevel.WARNING
        self._send(Alert(
            level=level,
            type=AlertType.EOD_WARNING,
            title=f"EOD Warning — {minutes_remaining:.0f} min to close",
            message=f"{open_positions} positions remain open.",
            data={"minutes": minutes_remaining, "positions": open_positions}
        ))

    def connection_lost(self):
        self._send(Alert(
            level=AlertLevel.CRITICAL,
            type=AlertType.CONNECTION,
            title="Connection Lost",
            message="Disconnected from TWS/Gateway. Reconnecting...",
        ))

    def connection_restored(self, attempt: int, downtime_secs: float):
        self._send(Alert(
            level=AlertLevel.WARNING,
            type=AlertType.CONNECTION,
            title="Connection Restored",
            message=f"Reconnected on attempt {attempt} after {downtime_secs:.0f}s downtime.",
        ))

    def daily_pnl_target(self, pnl: float, target: float):
        self._send(Alert(
            level=AlertLevel.INFO,
            type=AlertType.DAILY_PNL,
            title="Daily P&L Target Reached",
            message=f"P&L ${pnl:+,.2f} reached target ${target:,.2f}",
            data={"pnl": pnl, "target": target}
        ))

    def custom(self, title: str, message: str,
               level: AlertLevel = AlertLevel.INFO):
        self._send(Alert(
            level=level, type=AlertType.CUSTOM,
            title=title, message=message
        ))

    # ── INTERNAL ─────────────────────────────────────────────────────────

    def _send(self, alert: Alert):
        # Deduplication check
        with self._lock:
            last = self._last_sent.get(alert.type)
            now  = alert.timestamp.timestamp()
            if last and (now - last) < self.DEDUP_WINDOW:
                return
            self._last_sent[alert.type] = now
            self._history.append(alert)

        # Always log
        level_map = {
            AlertLevel.INFO:     log.info,
            AlertLevel.WARNING:  log.warning,
            AlertLevel.CRITICAL: log.error,
        }
        level_map[alert.level](str(alert))

        # Fire callbacks
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception as e:
                log.debug(f"Alert callback error: {e}")

        # Email (non-blocking)
        if (self._email_cfg.enabled
                and self._alert_meets_email_threshold(alert)):
            t = threading.Thread(
                target=self._send_email, args=(alert,), daemon=True
            )
            t.start()

    def _alert_meets_email_threshold(self, alert: Alert) -> bool:
        levels = [AlertLevel.INFO, AlertLevel.WARNING, AlertLevel.CRITICAL]
        return (levels.index(alert.level)
                >= levels.index(self._email_cfg.min_level))

    def _send_email(self, alert: Alert):
        cfg = self._email_cfg
        if not cfg.user or not cfg.to:
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[TradingSystem] {alert.title}"
            msg["From"]    = cfg.user
            msg["To"]      = ", ".join(cfg.to)

            body = (f"{alert.level.value}: {alert.title}\n\n"
                    f"{alert.message}\n\n"
                    f"Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S ET')}\n"
                    f"Data: {alert.data}")
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(cfg.host, cfg.port) as smtp:
                smtp.starttls()
                smtp.login(cfg.user, cfg.password)
                smtp.sendmail(cfg.user, cfg.to, msg.as_string())
            log.debug(f"Email alert sent: {alert.title}")
        except Exception as e:
            log.warning(f"Email alert failed: {e}")

    def get_history(self, alert_type: Optional[AlertType] = None,
                    limit: int = 50) -> List[Alert]:
        with self._lock:
            history = list(self._history)
        if alert_type:
            history = [a for a in history if a.type == alert_type]
        return history[-limit:]

    def get_critical_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._history
                       if a.level == AlertLevel.CRITICAL)
