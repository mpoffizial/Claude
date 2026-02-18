"""
Parameter Optimizer: Grid search and walk-forward analysis
over strategy parameters to maximize Sharpe ratio.
"""

import itertools
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from polymarket_btc_bot.config import BotConfig, OPTIMIZATION_GRID
from polymarket_btc_bot.backtesting.backtester import Backtester, BacktestResult
from polymarket_btc_bot.backtesting.historical_loader import HistoricalLoader

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    best_params: dict
    best_sharpe: float
    best_win_rate: float
    best_pnl: float
    all_results: list[dict]
    walk_forward_results: Optional[list[dict]] = None


class ParameterOptimizer:
    """
    Grid search over strategy parameters with walk-forward validation.

    Optimization target: Sharpe Ratio (not raw PnL).
    Uses out-of-sample testing to prevent overfitting.
    """

    def __init__(self, config: BotConfig, loader: HistoricalLoader):
        self.config = config
        self.loader = loader

    def grid_search(
        self,
        start_time_ms: int,
        end_time_ms: int,
        grid: Optional[dict] = None,
        trade_size: float = 10.0,
    ) -> OptimizationResult:
        """
        Run grid search over parameter combinations.

        Args:
            start_time_ms: Start of data period
            end_time_ms: End of data period
            grid: Parameter grid (defaults to OPTIMIZATION_GRID)
            trade_size: USDC per trade

        Returns:
            OptimizationResult with best parameters
        """
        if grid is None:
            grid = OPTIMIZATION_GRID

        # Split data: train on first 67%, test on last 33%
        split_point = int(start_time_ms + (end_time_ms - start_time_ms) * self.config.backtest.train_ratio)

        param_names = list(grid.keys())
        param_values = list(grid.values())
        combinations = list(itertools.product(*param_values))

        logger.info(
            "Running grid search: %d parameter combinations",
            len(combinations),
        )

        all_results = []
        best_sharpe = -float("inf")
        best_params = {}
        best_result = None

        for i, combo in enumerate(combinations):
            params = dict(zip(param_names, combo))

            # Apply parameters to config
            test_config = self._apply_params(params)

            # Run backtest on TRAINING data only
            backtester = Backtester(test_config, self.loader)
            result = backtester.run(start_time_ms, split_point, trade_size)

            entry = {
                "params": params,
                "train_sharpe": result.sharpe_ratio,
                "train_win_rate": result.win_rate,
                "train_pnl": result.total_pnl_after_fee,
                "train_trades": result.total_trades,
            }

            # Run on TEST data for validation
            test_result = backtester.run(split_point, end_time_ms, trade_size)
            entry["test_sharpe"] = test_result.sharpe_ratio
            entry["test_win_rate"] = test_result.win_rate
            entry["test_pnl"] = test_result.total_pnl_after_fee
            entry["test_trades"] = test_result.total_trades

            all_results.append(entry)

            # Track best by TRAINING sharpe (we validate with test later)
            if result.sharpe_ratio > best_sharpe and result.total_trades >= 10:
                best_sharpe = result.sharpe_ratio
                best_params = params
                best_result = result

            if (i + 1) % 10 == 0:
                logger.info("Grid search progress: %d/%d", i + 1, len(combinations))

        # Sort by train sharpe
        all_results.sort(key=lambda x: x["train_sharpe"], reverse=True)

        # Log top 5
        logger.info("=== Top 5 Parameter Sets ===")
        for entry in all_results[:5]:
            logger.info(
                "Sharpe: %.2f (test: %.2f) | WR: %.1f%% | PnL: $%.2f | %s",
                entry["train_sharpe"],
                entry["test_sharpe"],
                entry["train_win_rate"] * 100,
                entry["train_pnl"],
                entry["params"],
            )

        return OptimizationResult(
            best_params=best_params,
            best_sharpe=best_sharpe,
            best_win_rate=best_result.win_rate if best_result else 0.0,
            best_pnl=best_result.total_pnl_after_fee if best_result else 0.0,
            all_results=all_results,
        )

    def walk_forward(
        self,
        start_time_ms: int,
        end_time_ms: int,
        window_days: int = 3,
        trade_size: float = 10.0,
    ) -> list[dict]:
        """
        Walk-forward analysis: train on window N, test on window N+1.

        Args:
            start_time_ms: Start of full data period
            end_time_ms: End of full data period
            window_days: Size of each window in days
            trade_size: USDC per trade

        Returns:
            List of per-window results
        """
        window_ms = window_days * 24 * 60 * 60 * 1000
        results = []

        current_start = start_time_ms

        while current_start + 2 * window_ms <= end_time_ms:
            train_start = current_start
            train_end = current_start + window_ms
            test_start = train_end
            test_end = test_start + window_ms

            logger.info(
                "Walk-forward window: train [%d-%d] test [%d-%d]",
                train_start, train_end, test_start, test_end,
            )

            # Optimize on training window (simplified: use defaults)
            opt = self.grid_search(
                train_start, train_end,
                grid={
                    "momentum_threshold": [0.001, 0.002, 0.003],
                    "min_edge_threshold": [0.02, 0.03, 0.04],
                },
                trade_size=trade_size,
            )

            # Test best params on next window
            best_config = self._apply_params(opt.best_params)
            backtester = Backtester(best_config, self.loader)
            test_result = backtester.run(test_start, test_end, trade_size)

            window_result = {
                "train_period": (train_start, train_end),
                "test_period": (test_start, test_end),
                "best_params": opt.best_params,
                "train_sharpe": opt.best_sharpe,
                "test_sharpe": test_result.sharpe_ratio,
                "test_win_rate": test_result.win_rate,
                "test_pnl": test_result.total_pnl_after_fee,
                "test_trades": test_result.total_trades,
                "performance_ratio": (
                    test_result.sharpe_ratio / opt.best_sharpe
                    if opt.best_sharpe > 0 else 0.0
                ),
            }

            results.append(window_result)
            logger.info(
                "Window result: train_sharpe=%.2f test_sharpe=%.2f ratio=%.2f",
                opt.best_sharpe,
                test_result.sharpe_ratio,
                window_result["performance_ratio"],
            )

            current_start = test_start

        # Summary
        if results:
            avg_ratio = np.mean([r["performance_ratio"] for r in results])
            logger.info(
                "Walk-forward complete: %d windows, avg performance ratio: %.2f",
                len(results),
                avg_ratio,
            )
            if avg_ratio > 0.7:
                logger.info("Strategy is ROBUST (ratio > 0.7)")
            else:
                logger.warning("Strategy may be OVERFIT (ratio < 0.7)")

        return results

    def _apply_params(self, params: dict) -> BotConfig:
        """Create a new config with the given parameters applied."""
        import copy
        config = copy.deepcopy(self.config)

        for key, value in params.items():
            if hasattr(config.strategy, key):
                setattr(config.strategy, key, value)
            elif hasattr(config.risk, key):
                setattr(config.risk, key, value)

        return config

    def save_results(self, result: OptimizationResult, filepath: str):
        """Save optimization results to JSON."""
        data = {
            "best_params": result.best_params,
            "best_sharpe": result.best_sharpe,
            "best_win_rate": result.best_win_rate,
            "best_pnl": result.best_pnl,
            "top_10": result.all_results[:10],
            "timestamp": time.time(),
        }

        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info("Optimization results saved to %s", filepath)
