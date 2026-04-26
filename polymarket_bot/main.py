#!/usr/bin/env python3
"""
Polymarket Paper Trading Bot – main entry point (v3.0 Short-Term).

Short-term trading: profit from price movements between cycles.
Uses real Polymarket API prices + realistic market microstructure
(bid-ask spread, slippage, micro-fluctuations).

No simulated event resolutions — all P&L comes from price changes.

Usage:
    python -m polymarket_bot.main                          # default: 20 cycles, 30s
    python -m polymarket_bot.main --cycles 50 --interval 60
    python -m polymarket_bot.main --strategy prob
"""

import argparse
import math
import random
import time
from datetime import datetime, timezone

from polymarket_bot.market_reader import fetch_markets, save_snapshot_csv
from polymarket_bot.strategy import (
    MeanReversionStrategy,
    ProbabilisticEdgeStrategy,
    Side,
    filter_volatile_markets,
)
from polymarket_bot.paper_trader import PaperTrader


# ── Market Microstructure ──────────────────────────────────────────
# In real trading the displayed "mid price" hides important details:
#  - You never buy at mid: you pay the ASK (higher) and sell at BID (lower)
#  - Between API snapshots, prices fluctuate within the spread
#  - Higher-volume markets have tighter spreads

def _spread_for(volume: float, mid_price: float) -> float:
    """Estimate realistic bid-ask spread based on volume and price level.
    Higher volume → tighter spread.  Prices near 0 or 1 → wider spread.
    Returns half-spread (one side).
    """
    base = 0.008  # 0.8% base half-spread (Polymarket is fairly liquid)
    # Volume dampening: high-vol markets have tighter spreads
    vol_factor = max(0.25, 1.0 - math.log10(max(volume, 1)) / 7)
    # Edge factor: prices near 0 or 1 have wider relative spreads
    edge_dist = min(mid_price, 1.0 - mid_price)
    edge_factor = 1.0 + max(0, 0.2 - edge_dist) * 0.5
    half = base * vol_factor * edge_factor
    return min(half, 0.025)  # cap at 2.5%


def _execution_price(mid_yes: float, side: str, volume: float) -> float:
    """Simulate realistic fill price with spread + slippage.
    Returns the fill price in the units of the traded side:
      YES → you pay the YES ask (mid + spread)
      NO  → you pay the NO ask  ((1-mid) + spread)
    """
    half_spread = _spread_for(volume, mid_yes)
    slippage = random.uniform(0.001, 0.003)
    if side == "YES":
        return mid_yes + half_spread + slippage
    else:
        mid_no = 1.0 - mid_yes
        return mid_no + half_spread + slippage


# Module-level momentum tracker for micro-price simulation
_momentum: dict[str, float] = {}


def _micro_price(mid: float, volume: float, market_id: str = "") -> float:
    """Simulate micro-fluctuation with momentum.
    Models real order-book dynamics: order flow creates short-term trends,
    then prices mean-revert.  Higher volume = smaller moves.
    """
    half_spread = _spread_for(volume, mid)
    prev_mom = _momentum.get(market_id, 0.0)
    # Order-flow shock scaled by spread
    shock_scale = half_spread * 2.5
    new_noise = random.gauss(0, shock_scale)
    # Strong momentum: 60% carry — trends persist across multiple ticks
    mom = prev_mom * 0.60 + new_noise * 0.40
    # Occasional large order block (8% chance — whale trade / news catalyst)
    if random.random() < 0.08:
        mom += random.choice([-1, 1]) * abs(random.gauss(0, half_spread * 4))
    # Light mean-reversion toward mid to prevent runaway drift
    mom *= 0.97
    _momentum[market_id] = mom
    return max(0.001, min(0.999, mid + mom))


class PriceTracker:
    """Track price history per market to detect real price swings."""

    def __init__(self):
        self._history: dict[str, list[tuple[str, float]]] = {}

    def record(self, market_id: str, yes_price: float):
        ts = datetime.now(timezone.utc).isoformat()
        hist = self._history.setdefault(market_id, [])
        hist.append((ts, yes_price))
        if len(hist) > 100:
            hist.pop(0)

    def current_price(self, market_id: str) -> float | None:
        hist = self._history.get(market_id, [])
        return hist[-1][1] if hist else None

    def price_delta(self, market_id: str) -> float:
        hist = self._history.get(market_id, [])
        if len(hist) < 2:
            return 0.0
        return hist[-1][1] - hist[0][1]

    def volatility(self, market_id: str) -> float:
        hist = self._history.get(market_id, [])
        if len(hist) < 2:
            return 0.0
        prices = [p for _, p in hist]
        return max(prices) - min(prices)

    def n_records(self, market_id: str) -> int:
        return len(self._history.get(market_id, []))


# Track recently traded markets for diversification
_recently_traded: list[str] = []
MAX_RECENT = 5


def run_bot(
    cycles: int = 20,
    interval: int = 30,
    strategy_name: str = "mean_reversion",
    capital: float = 200.0,
    market_limit: int = 200,
):
    """Main trading loop — short-term, price-movement based."""
    print("=" * 60)
    print("  POLYMARKET PAPER TRADING BOT  v3.0 (Short-Term)")
    print(f"  Strategy   : {strategy_name}")
    print(f"  Capital    : \u20ac{capital:.2f}")
    print(f"  Cycles     : {cycles}")
    print(f"  Interval   : {interval}s")
    print(f"  Mode       : Short-term (spread + micro-price simulation)")
    print("=" * 60)

    trader = PaperTrader(initial_capital=capital)
    tracker = PriceTracker()

    if strategy_name == "prob":
        strategy = ProbabilisticEdgeStrategy(min_edge=0.08)
    else:
        strategy = MeanReversionStrategy(low_threshold=0.40, high_threshold=0.60)

    # Exit thresholds
    take_profit_pct = 0.03   # close if >=3% in our favor
    stop_loss_pct = 0.05     # close if >=5% against us
    max_hold_cycles = 8      # force close after 8 cycles
    hold_counter = 0

    # Track volumes for spread calculation
    _volumes: dict[str, float] = {}

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

        # 2. Filter for tradeable markets
        markets = filter_volatile_markets(all_markets, min_price=0.10, max_price=0.90, min_volume=1_000)
        print(f"  Fetched {len(all_markets)} markets, {len(markets)} tradeable (price 0.10-0.90)")
        save_snapshot_csv(all_markets)

        if not markets:
            print("  No tradeable markets this cycle")
            if cycle < cycles:
                time.sleep(interval)
            continue

        # Record prices and volumes
        for m in markets:
            tracker.record(m["id"], m["yes_price"])
            _volumes[m["id"]] = m["volume"]

        # 3. Manage open position
        if trader.has_open_position:
            hold_counter += 1
            pos = trader.position
            current_market = next(
                (m for m in all_markets if m["id"] == pos.market_id), None
            )

            if current_market is None:
                last = tracker.current_price(pos.market_id)
                if last is not None:
                    print(f"  Market delisted \u2014 closing at {last:.4f}")
                    trader.close_position_at(last, reason="market-delisted")
                    hold_counter = 0
            else:
                api_mid = current_market["yes_price"]
                vol = current_market["volume"]

                # Simulate realistic current price (micro-fluctuation around API mid)
                sim_price = _micro_price(api_mid, vol, pos.market_id)
                tracker.record(pos.market_id, sim_price)

                # Calculate unrealized P&L against simulated price
                if pos.side == "YES":
                    exit_bid = sim_price - _spread_for(vol, sim_price)  # we'd sell at bid
                    change_pct = (exit_bid - pos.entry_price) / pos.entry_price
                else:
                    no_bid = (1.0 - sim_price) - _spread_for(vol, sim_price)
                    change_pct = (no_bid - pos.entry_price) / pos.entry_price

                print(f"  Position: {pos.side} {pos.question[:50]}...")
                print(f"    entry={pos.entry_price:.4f}  mid={api_mid:.4f}  sim={sim_price:.4f}  unrealized={change_pct:+.1%}  (hold {hold_counter})")

                # A) Take profit
                if change_pct >= take_profit_pct:
                    exit_p = sim_price if pos.side == "YES" else (1.0 - sim_price)
                    print(f"  \u2191 Take profit! {change_pct:+.1%}")
                    trader.close_position_at(sim_price, reason=f"take-profit {change_pct:+.1%}")
                    hold_counter = 0

                # B) Stop loss
                elif change_pct <= -stop_loss_pct:
                    print(f"  \u2193 Stop loss! {change_pct:+.1%}")
                    trader.close_position_at(sim_price, reason=f"stop-loss {change_pct:+.1%}")
                    hold_counter = 0

                # C) Max hold
                elif hold_counter >= max_hold_cycles:
                    print(f"  \u23f0 Max hold ({max_hold_cycles}) \u2014 closing at market ({change_pct:+.1%})")
                    trader.close_position_at(sim_price, reason=f"max-hold {change_pct:+.1%}")
                    hold_counter = 0

        # 4. Look for new trades
        if not trader.has_open_position:
            hold_counter = 0

            candidates = [m for m in markets if m["id"] not in _recently_traded]
            if not candidates:
                candidates = markets

            # Prefer markets showing recent volatility
            volatile = [
                m for m in candidates
                if tracker.volatility(m["id"]) > 0.003 or tracker.n_records(m["id"]) < 3
            ]
            if volatile:
                candidates = volatile

            # For prob-edge, generate estimates using price momentum
            if isinstance(strategy, ProbabilisticEdgeStrategy):
                for m in candidates:
                    delta = tracker.price_delta(m["id"])
                    trend = delta * 0.5
                    noise = random.gauss(0, 0.06)
                    est = max(0.01, min(0.99, m["yes_price"] + trend + noise))
                    strategy.set_estimate(m["id"], est)

            signals = []
            for m in candidates:
                sig = strategy.evaluate(m)
                if sig.side != Side.HOLD:
                    signals.append((sig, m))

            if signals:
                signals.sort(key=lambda x: x[0].confidence, reverse=True)
                best_sig, best_mkt = signals[0]

                # Apply realistic execution price (pay the spread)
                fill_price = _execution_price(
                    best_mkt["yes_price"],
                    "YES" if best_sig.side == Side.BUY_YES else "NO",
                    best_mkt["volume"],
                )
                half_sp = _spread_for(best_mkt["volume"], best_mkt["yes_price"])
                print(f"  Signal: {best_sig.side.value} | {best_sig.reason}")
                print(f"  ({len(signals)} signals, best confidence={best_sig.confidence:.2f})")
                print(f"    mid={best_mkt['yes_price']:.4f}  fill={fill_price:.4f}  spread={half_sp*2:.3f}")

                trader.process_signal(best_sig)
                # Adjust entry to the realistic fill price
                if trader.has_open_position:
                    trader.position.entry_price = fill_price

                _recently_traded.append(best_sig.market_id)
                if len(_recently_traded) > MAX_RECENT:
                    _recently_traded.pop(0)
            else:
                print("  No actionable signals this cycle")

        # Status
        s = trader.summary()
        open_str = ""
        if trader.has_open_position:
            pos = trader.position
            cm = next((m for m in all_markets if m["id"] == pos.market_id), None)
            if cm:
                if pos.side == "YES":
                    u = (cm["yes_price"] - pos.entry_price) / pos.entry_price * 100
                else:
                    u = ((1 - cm["yes_price"]) - pos.entry_price) / pos.entry_price * 100
                open_str = f" | Open: {u:+.1f}%"
        print(f"  Balance: \u20ac{s['balance']:.2f} | Trades: {s['total_trades']} | PnL: \u20ac{s['total_pnl']:+.2f}{open_str}")

        if cycle < cycles:
            time.sleep(interval)

    # Close remaining position at session end
    if trader.has_open_position:
        pos = trader.position
        last = tracker.current_price(pos.market_id)
        if last is not None:
            print(f"\n  Closing remaining position at market...")
            trader.close_position_at(last, reason="session-end")

    trader.print_summary()
    trader.save_trade_log()
    trader.save_equity_curve()
    return trader


def main():
    parser = argparse.ArgumentParser(description="Polymarket Paper Trading Bot (Short-Term)")
    parser.add_argument("--cycles", type=int, default=20, help="Trading cycles (default: 20)")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between cycles (default: 30)")
    parser.add_argument("--strategy", choices=["mean_reversion", "prob"], default="mean_reversion",
                        help="Strategy: mean_reversion or prob")
    parser.add_argument("--capital", type=float, default=200.0, help="Initial capital in \u20ac (default: 200)")
    parser.add_argument("--markets", type=int, default=200, help="Markets to fetch per cycle (default: 200)")
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
