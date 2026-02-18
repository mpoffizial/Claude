"""
Structured JSON Logger: Provides structured logging for the trading bot.
Outputs JSON-formatted log entries for easy parsing and monitoring.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from polymarket_btc_bot.config import MonitoringConfig


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }

        return json.dumps(log_entry)


class TradeLogger:
    """Specialized logger for trade events and PnL tracking."""

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._trade_log_path = os.path.join(config.log_dir, "trades.jsonl")
        self._pnl_log_path = os.path.join(config.log_dir, "pnl.csv")
        os.makedirs(config.log_dir, exist_ok=True)

        # Initialize PnL CSV header if new file
        if not os.path.exists(self._pnl_log_path):
            with open(self._pnl_log_path, "w") as f:
                f.write("timestamp,market,strategy,direction,entry_price,size,won,pnl,pnl_after_fee,cumulative_pnl\n")

        self._cumulative_pnl = 0.0

    def log_trade(self, trade_data: dict):
        """Log a completed trade to the trades JSONL file."""
        trade_data["logged_at"] = datetime.utcnow().isoformat() + "Z"

        with open(self._trade_log_path, "a") as f:
            f.write(json.dumps(trade_data) + "\n")

    def log_pnl(
        self,
        market: str,
        strategy: str,
        direction: str,
        entry_price: float,
        size: float,
        won: bool,
        pnl: float,
        pnl_after_fee: float,
    ):
        """Log PnL entry to CSV."""
        self._cumulative_pnl += pnl_after_fee

        with open(self._pnl_log_path, "a") as f:
            f.write(
                f"{datetime.utcnow().isoformat()},{market},{strategy},"
                f"{direction},{entry_price:.4f},{size:.2f},"
                f"{'1' if won else '0'},{pnl:.4f},{pnl_after_fee:.4f},"
                f"{self._cumulative_pnl:.4f}\n"
            )

    def log_signal(self, signal_data: dict):
        """Log a trading signal (whether acted upon or not)."""
        signal_log = os.path.join(self.config.log_dir, "signals.jsonl")
        signal_data["logged_at"] = datetime.utcnow().isoformat() + "Z"

        with open(signal_log, "a") as f:
            f.write(json.dumps(signal_data) + "\n")


def setup_logging(config: MonitoringConfig) -> TradeLogger:
    """
    Configure the logging system.

    Returns:
        TradeLogger instance for trade-specific logging
    """
    os.makedirs(config.log_dir, exist_ok=True)

    # Root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    # Console handler (human-readable)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)-25s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    # File handler (JSON structured)
    log_file = os.path.join(config.log_dir, "bot.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JSONFormatter())
    root.addHandler(file_handler)

    # Reduce noise from external libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.info("Logging initialized: console=%s file=%s", config.log_level, log_file)

    return TradeLogger(config)
