"""
IBKR Day Trading System - Main Entry Point
Targets: ES E-mini Futures, ES Future Options, SPX Index Options
Features: VIX regime analysis, P&L monitoring, auto EOD close
"""

import sys
import time
import signal
import logging
import argparse
import threading
from datetime import datetime

from ibapi.client import EClient
from ibapi.wrapper import EWrapper

from config import TradingConfig
from trading_app import TradingApp
from logger_setup import setup_logging


def parse_args():
    parser = argparse.ArgumentParser(description="IBKR Day Trading System")
    parser.add_argument("--mode", choices=["live", "paper", "test"], default="paper",
                        help="Trading mode (default: paper)")
    parser.add_argument("--host", default="127.0.0.1", help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=None,
                        help="TWS port (default: 4002 paper, 4001 live)")
    parser.add_argument("--client-id", type=int, default=1, help="Client ID")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--run-tests", action="store_true",
                        help="Run test suite and exit")
    parser.add_argument("--test-module", default="all",
                        choices=["all", "connection", "market_data", "orders",
                                 "positions", "vix", "eod", "risk"],
                        help="Specific test module to run")
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging(args.log_level)
    log = logging.getLogger("Main")

    # Handle test mode
    if args.run_tests:
        from tests.test_runner import TestRunner
        runner = TestRunner()
        success = runner.run(args.test_module)
        sys.exit(0 if success else 1)

    # Determine port
    if args.port is None:
        args.port = 4001 if args.mode == "live" else 4002

    log.info("=" * 60)
    log.info(f"  IBKR DAY TRADING SYSTEM")
    log.info(f"  Mode: {args.mode.upper()}")
    log.info(f"  Connect: {args.host}:{args.port} (clientId={args.client_id})")
    log.info(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    config = TradingConfig(
        mode=args.mode,
        host=args.host,
        port=args.port,
        client_id=args.client_id,
    )

    app = TradingApp(config)

    # Graceful shutdown
    def handle_shutdown(signum, frame):
        log.warning(f"Shutdown signal received (signal {signum})")
        app.initiate_shutdown()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Connect and run
    log.info(f"Connecting to TWS/Gateway at {args.host}:{args.port}...")
    app.connect(args.host, args.port, args.client_id)

    api_thread = threading.Thread(target=app.run, name="IBKRApiThread", daemon=True)
    api_thread.start()

    # Wait for connection
    timeout = 15
    start = time.time()
    while not app.is_connected() and time.time() - start < timeout:
        time.sleep(0.1)

    if not app.is_connected():
        log.error("Failed to connect within timeout. Check TWS/Gateway is running.")
        sys.exit(1)

    log.info("Connected successfully. Starting trading system...")
    app.on_connected()

    # Main loop — keep alive until shutdown
    try:
        while not app.shutdown_requested:
            time.sleep(1)
            app.heartbeat()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
    finally:
        log.info("Shutting down...")
        app.cleanup_and_disconnect()

    log.info("System stopped.")


if __name__ == "__main__":
    main()
