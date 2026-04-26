#!/usr/bin/env python3
"""
Polymarket Paper Trading Bot – main entry point.

Orchestrates the full loop:
  1. Fetch live market data from Polymarket
  2. Filter for volatile/interesting markets
  3. Generate signals via pluggable strategies
  4. Execute paper trades (simulated)
  5. Apply risk management (stop-loss, price-movement exits, resolution)
  6. Log everything and print a summary

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
    filter_volatile_markets,
)
from polymarket_bot.paper_trader import PaperTrader

# Track price history across cycles to detect real movement
_price_history: dict[str, list[float]] = {}
# Track recently traded markets to force diversification
_recently_traded: list[str] = []
MAX_RECENT = 5


def _record_price(market_id: str, yes_price: float):
    hist = _price_history.setdefault(market_id, [])
    hist.append(yes_price)
    if len(hist) > 20:
        hist.pop(0)


def _price_moved(market_id: str, threshold: float = 0.03) -> tuple[bool, float]:
    """Check if price moved significantly since we started tracking."""
    hist = _price_history.get(market_id, [])
    if len(hist) < 2:
        return False, 0.0
    delta = hist[-1] - hist[0]
    return abs(delta) >= threshold, delta


def run_bot(
    cycles: int = 5,
    interval: int = 30,
    strategy_name: str = "mean_reversion",
    capital: float = 200.0,
    market_limit: int = 50,
):
    """Main trading loop."""
    print("=" * 60)
    print("  POLYMARKET PAPER TRADING BOT  v2.0")
    print(f"  Strategy   : {strategy_name}")
    print(f"  Capital    : \u20ac{capital:.2f}")
    print(f"  Cycles     : {cycles}")
    print(f"  Interval   : {interval}s")
    print("=" * 60)

    trader = PaperTrader(initial_capital=capital)

    if strategy_name == "prob":
        strategy = ProbabilisticEdgeStrategy(min_edge=0.08)
    else:
        strategy = MeanReversionStrategy(low_threshold=0.40, high_threshold=0.60)

    for cycle in range(1, cycles + 1):
        print(f"\n\u2500\u2500 Cycle {cycle}/{cycles} \u2500\u2500")

        # 1. Fetch market data
        try:
            all_markets = fetch_markets(limit=market_limit)
        except Exception as exc:
            print(f"  \u26a0 API error: {exc}  \u2014 skipping cycle")
            time.sleep(interval)
            continue

        if not all_markets:
            print("  No markets available")
            time.sleep(interval)
            continue

        # 2. Filter for volatile markets with real opportunity
        markets = filter_volatile_markets(all_markets, min_price=0.15, max_price=0.85)
        print(f"  Fetched {len(all_markets)} markets, {len(markets)} tradeable (price 0.15-0.85)")
        save_snapshot_csv(all_markets)

        if not markets:
            print("  No tradeable markets this cycle")
            if cycle < cycles:
                time.sleep(interval)
            continue

        # Record price history for all markets
        for m in markets:
            _record_price(m["id"], m["yes_price"])

        # 3. Check open position: stop-loss, price-movement exit, resolution
        if trader.has_open_position:
            pos = trader.position
            current_market = next(
                (m for m in all_markets if m["id"] == pos.market_id), None
            )
            if current_market:
                # Check stop-loss first
                sl_result = trader.check_stop_loss(current_market["yes_price"])
                if sl_result:
                    print(f"  \u26a0 Stop-loss triggered!")

                # Check if price moved enough for a profitable exit
                if trader.has_open_position:
                    moved, delta = _price_moved(pos.market_id, threshold=0.02)
                    if moved:
                        exit_price = current_market["yes_price"]
                        if pos.side == "YES" and delta > 0:
                            trader.close_position_at(exit_price, reason=f"price-up {delta:+.3f}")
                        elif pos.side == "NO" and delta < 0:
                            trader.close_position_at(exit_price, reason=f"price-down {delta:+.3f}")
                        elif abs(delta) > 0.05:
                            trader.close_position_at(exit_price, reason=f"price-shift {delta:+.3f}")
            else:
                # Market no longer in active list -> simulate resolution
                win_prob = 0.55 if pos.entry_price < 0.50 else 0.45
                outcome = random.random() < win_prob
                side_matches = (pos.side == "YES" and outcome) or (pos.side == "NO" and not outcome)
                result_str = "WIN" if side_matches else "LOSS"
                print(f"  Market resolved! outcome=YES={outcome} -> {result_str}")
                trader.simulate_resolution(outcome)

        # 4. If no open position, scan for new signals
        if not trader.has_open_position:
            # Exclude recently traded markets for diversification
            candidate_markets = [
                m for m in markets if m["id"] not in _recently_traded
            ]
            if not candidate_markets:
                candidate_markets = markets  # fallback if all recently traded

            # For probabilistic edge, generate estimates with realistic noise
            if isinstance(strategy, ProbabilisticEdgeStrategy):
                for m in candidate_markets:
                    noise = random.gauss(0, 0.08)
                    strategy.set_estimate(m["id"], max(0.01, min(0.99, m["yes_price"] + noise)))

            # Collect all actionable signals and pick the best one
            signals = []
            for m in candidate_markets:
                sig = strategy.evaluate(m)
                if sig.side != Side.HOLD:
                    signals.append(sig)

            if signals:
                # Sort by confidence and pick the best
                signals.sort(key=lambda s: s.confidence, reverse=True)
                best = signals[0]
                print(f"  Signal: {best.side.value} | {best.reason}")
                print(f"  ({len(signals)} signals found, picked best confidence={best.confidence:.2f})")
                trader.process_signal(best)
                _recently_traded.append(best.market_id)
                if len(_recently_traded) > MAX_RECENT:
                    _recently_traded.pop(0)
            else:
                print("  No actionable signals this cycle")

        # 5. Simulate random resolution events (some markets resolve over time)
        if trader.has_open_position and cycle % 5 == 0:
            pos = trader.position
            # Simulate a realistic resolution based on entry price
            # If we bought YES at 0.30, there's ~30% chance it resolves YES
            if pos.side == "YES":
                resolve_prob = pos.entry_price + random.gauss(0, 0.10)
            else:
                resolve_prob = (1.0 - pos.entry_price) + random.gauss(0, 0.10)
            resolve_prob = max(0.1, min(0.9, resolve_prob))
            outcome_yes = random.random() < resolve_prob
            side_matches = (pos.side == "YES" and outcome_yes) or (pos.side == "NO" and not outcome_yes)
            result_str = "WIN" if side_matches else "LOSS"
            print(f"  Event resolved! YES={outcome_yes} -> {result_str} (prob was {resolve_prob:.0%})")
            trader.simulate_resolution(outcome_yes)

        # Status line
        s = trader.summary()
        print(f"  Balance: \u20ac{s['balance']:.2f} | Trades: {s['total_trades']} | PnL: \u20ac{s['total_pnl']:+.2f}")

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
    parser.add_argument("--capital", type=float, default=200.0, help="Initial paper capital in \u20ac (default: 200)")
    parser.add_argument("--markets", type=int, default=50, help="Number of markets to fetch per cycle (default: 50)")
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
