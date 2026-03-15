"""Logging configuration for the trading system."""

import logging
import logging.handlers
import os
from datetime import date


def setup_logging(level: str = "INFO"):
    """Configure logging with console and file handlers."""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Capture all, filter per handler

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S"
    )

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(fmt)
    root.addHandler(console)

    # File handler - daily rotating
    today = date.today().strftime("%Y%m%d")
    fh = logging.handlers.RotatingFileHandler(
        f"{log_dir}/trading_{today}.log",
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=5
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Suppress noisy ibapi logs
    logging.getLogger("ibapi").setLevel(logging.WARNING)
    logging.getLogger("ibapi.client").setLevel(logging.WARNING)

    logging.getLogger("Main").info(f"Logging initialized at level {level}")
