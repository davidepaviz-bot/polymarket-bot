#!/usr/bin/env python3
"""
Polymarket Paper Trading Bot – main entry point.

Orchestrates the full loop:
  1. Fetch live market data from Polymarket
  2. Generate signals via pluggable strategies
  3. Execute paper trades (simulated)
  4. Apply risk management (stop-loss, single-position rule)
  5. Log everything and print a summary

Usage:
    python -m polymarket_bot.main              # default: 5 cycles, mean-reversion
    python -m polymarket_bot.main --cycles 20  # run 20 cycles
    python -m polymarket_bot.main --strategy prob  # use probabilistic edge strategy
"""

import argparse
import random
import time

from polymarket_bot.market_reader import fetch_markets, save_snapshot_csv
from polymarket_bot.strategy import (
    MeanReversionStrategy,
    ProbabilisticEdgeStrategy,
    Side,
)
from polymarket_bot.paper_trader import PaperTrader


def run_bot(
    cycles: int = 5,
    interval: int = 30,
    strategy_name: str = "mean_reversion",
    capital: float = 200.0,
    market_limit: int = 20,
):
    """Main trading loop."""
    print("=" * 60)
    print("  POLYMARKET PAPER TRADING BOT")
    print(f"  Strategy   : {strategy_name}")
    print(f"  Capital    : €{capital:.2f}")
    print(f"  Cycles     : {cycles}")
    print(f"  Interval   : {interval}s")
    print("=" * 60)

    # Initialise components
    trader = PaperTrader(initial_capital=capital)

    if strategy_name == "prob":
        strategy = ProbabilisticEdgeStrategy(min_edge=0.10)
    else:
        strategy = MeanReversionStrategy(low_threshold=0.45, high_threshold=0.55)

    for cycle in range(1, cycles + 1):
        print(f"\n── Cycle {cycle}/{cycles} ──")

        # 1. Fetch market data
        try:
            markets = fetch_markets(limit=market_limit)
        except Exception as exc:
            print(f"  ⚠ API error: {exc}  — skipping cycle")
            time.sleep(interval)
            continue

        if not markets:
            print("  No markets available")
            time.sleep(interval)
            continue

        print(f"  Fetched {len(markets)} markets")
        save_snapshot_csv(markets)

        # 2. Check stop-loss on open position
        if trader.has_open_position:
            pos = trader.position
            current_market = next(
                (m for m in markets if m["id"] == pos.market_id), None
            )
            if current_market:
                trader.check_stop_loss(current_market["yes_price"])
            else:
                # Market no longer in active list → simulate random resolution
                outcome = random.choice([True, False])
                print(f"  Market disappeared — simulating resolution (YES={outcome})")
                trader.simulate_resolution(outcome)

        # 3. If no open position, scan for signals
        if not trader.has_open_position:
            # For probabilistic edge, generate mock estimates for demo purposes
            if isinstance(strategy, ProbabilisticEdgeStrategy):
                for m in markets:
                    noise = random.uniform(-0.15, 0.15)
                    strategy.set_estimate(m["id"], max(0, min(1, m["yes_price"] + noise)))

            best_signal = None
            for m in markets:
                sig = strategy.evaluate(m)
                if sig.side != Side.HOLD:
                    if best_signal is None or sig.confidence > best_signal.confidence:
                        best_signal = sig

            if best_signal:
                print(f"  Signal: {best_signal.side.value} | {best_signal.reason}")
                trader.process_signal(best_signal)
            else:
                print("  No actionable signals this cycle")

        # 4. Periodically close positions to simulate market movement
        if trader.has_open_position and cycle % 3 == 0:
            current_market = next(
                (m for m in markets if m["id"] == trader.position.market_id), None
            )
            if current_market:
                trader.close_position_at(current_market["yes_price"], reason="take-profit-check")

        # Status line
        s = trader.summary()
        print(f"  Balance: €{s['balance']:.2f} | Trades: {s['total_trades']} | PnL: €{s['total_pnl']:+.2f}")

        if cycle < cycles:
            time.sleep(interval)

    # Final report
    trader.print_summary()
    trader.save_trade_log()
    trader.save_equity_curve()
    return trader


def main():
    parser = argparse.ArgumentParser(description="Polymarket Paper Trading Bot")
    parser.add_argument("--cycles", type=int, default=5, help="Number of trading cycles (default: 5)")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between cycles (default: 30)")
    parser.add_argument("--strategy", choices=["mean_reversion", "prob"], default="mean_reversion",
                        help="Trading strategy to use")
    parser.add_argument("--capital", type=float, default=200.0, help="Initial paper capital in € (default: 200)")
    parser.add_argument("--markets", type=int, default=20, help="Number of markets to fetch per cycle (default: 20)")
    args = parser.parse_args()

    run_bot(
        cycles=args.cycles,
        interval=args.interval,
        strategy_name=args.strategy,
        capital=args.capital,
        market_limit=args.markets,
    )


if __name__ == "__main__":
    main()
