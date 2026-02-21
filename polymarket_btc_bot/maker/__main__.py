"""
Entry point for the Polymarket Maker-Rebate Bot.

Usage
-----
    # Simulation mode (no real orders)
    python -m polymarket_btc_bot.maker

    # Live mode with custom parameters
    python -m polymarket_btc_bot.maker --mode live --quote-size 50 --spread 0.008

    # Via environment variables
    MAKER_SIMULATION=false MAKER_QUOTE_SIZE=50 python -m polymarket_btc_bot.maker

Environment variables
---------------------
Required for live trading:
    POLY_PRIVATE_KEY      Ethereum private key (hex, 0x-prefixed)
    POLY_API_KEY          Polymarket CLOB API key
    POLY_API_SECRET       Polymarket CLOB API secret
    POLY_API_PASSPHRASE   Polymarket CLOB API passphrase
    POLY_PROXY_WALLET     Proxy wallet address (if using proxy auth)

Optional (all have defaults):
    MAKER_SIMULATION      "true" | "false"   (default: true)
    MAKER_QUOTE_SIZE      float USDC         (default: 20.0)
    MAKER_SPREAD          float half-spread  (default: 0.01)
    MAKER_MAX_EXPOSURE    float USDC         (default: 40.0)
    MAKER_MAX_LOSS        float USDC         (default: -50.0)
"""

import argparse
import asyncio
import logging
import os
import signal
import sys

from polymarket_btc_bot.maker.bot import MakerBot
from polymarket_btc_bot.maker.config import MakerBotConfig


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(level: str, log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(os.path.join(log_dir, "maker_bot.log")))
    except OSError as exc:
        print(f"Warning: could not open log file – {exc}", file=sys.stderr)
    logging.basicConfig(level=numeric_level, format=fmt, handlers=handlers)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Polymarket Maker-Rebate / Market-Making Bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--mode",
        choices=["simulation", "live"],
        default="simulation",
        help="Trading mode.  Use 'simulation' to run without placing real orders.",
    )
    p.add_argument(
        "--quote-size",
        type=float,
        default=20.0,
        metavar="USDC",
        help="USDC placed per quote side (bid and ask separately).",
    )
    p.add_argument(
        "--spread",
        type=float,
        default=0.01,
        metavar="PROB",
        help="Half-spread in probability units (e.g. 0.01 = 1 cent).",
    )
    p.add_argument(
        "--max-exposure",
        type=float,
        default=40.0,
        metavar="USDC",
        help="Maximum net |Yes − No| inventory before auto-hedging triggers.",
    )
    p.add_argument(
        "--max-loss",
        type=float,
        default=-50.0,
        metavar="USDC",
        help="Session loss threshold that halts the bot (must be negative).",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level.",
    )
    p.add_argument(
        "--log-dir",
        default="maker_logs",
        help="Directory for log files.",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level, args.log_dir)

    # Build config (env vars first, then CLI overrides)
    config = MakerBotConfig.from_env()
    config.simulation_mode = args.mode == "simulation"
    config.quote.quote_size_usdc = args.quote_size
    config.quote.default_half_spread = args.spread
    config.inventory.max_net_exposure_usdc = args.max_exposure
    config.risk.max_daily_loss_usdc = args.max_loss

    # Warn on missing live credentials
    if not config.simulation_mode:
        missing = [k for k in ("private_key", "api_key", "api_secret", "api_passphrase")
                   if not getattr(config, k)]
        if missing:
            logging.critical(
                "Live mode requires: %s.  Set the corresponding POLY_* env vars.",
                ", ".join(missing),
            )
            sys.exit(1)

    bot = MakerBot(config)

    # Graceful shutdown on Ctrl-C / SIGTERM
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logging.info("Shutdown signal received – cancelling tasks")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await bot.run()


if __name__ == "__main__":
    asyncio.run(_main())
