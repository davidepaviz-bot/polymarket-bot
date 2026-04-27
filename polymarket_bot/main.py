#!/usr/bin/env python3
"""
Polymarket Paper Trading Bot – main entry point (v6.0 Multi-Timeframe).

Supports three timeframes:
  - short  : 30s cycles, tight exits, micro-movements
  - mid    : 5min cycles, variable TP, trailing stop
  - long   : 30min cycles, wide exits, trend-following

v6.0: timeframe modes, variable take-profit, trailing stop-loss.

Usage:
    python -m polymarket_bot.main --timeframe short         # default
    python -m polymarket_bot.main --timeframe mid --adaptive
    python -m polymarket_bot.main --timeframe long --strategy sentiment
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
    SentimentEdgeStrategy,
    EnsembleStrategy,
    Side,
    filter_volatile_markets,
)
from polymarket_bot.paper_trader import PaperTrader
from polymarket_bot.sentiment_engine import SentimentEngine
from polymarket_bot.trade_history import (
    TradeHistoryDB,
    TradeEntry,
    AdaptiveEngine,
    kelly_size,
    fixed_size,
)


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


def _micro_price(
    mid: float, volume: float, market_id: str = "",
    time_scale: float = 1.0,
) -> float:
    """Simulate micro-fluctuation with momentum.
    Models real order-book dynamics: order flow creates short-term trends,
    then prices mean-revert.  Higher volume = smaller moves.

    time_scale controls amplitude: longer intervals → bigger moves.
      short=1.0, mid=3.0, long=8.0
    """
    half_spread = _spread_for(volume, mid)
    prev_mom = _momentum.get(market_id, 0.0)
    # Order-flow shock scaled by spread and time horizon
    shock_scale = half_spread * 2.5 * math.sqrt(time_scale)
    new_noise = random.gauss(0, shock_scale)
    # Momentum carry: longer timeframes have stronger trends
    carry = min(0.75, 0.50 + time_scale * 0.03)
    mom = prev_mom * carry + new_noise * (1.0 - carry)
    # Occasional large order block (probability scales with time)
    whale_prob = min(0.20, 0.08 * math.sqrt(time_scale))
    if random.random() < whale_prob:
        mom += random.choice([-1, 1]) * abs(random.gauss(0, half_spread * 4 * math.sqrt(time_scale)))
    # Light mean-reversion toward mid to prevent runaway drift
    mom *= 0.97
    _momentum[market_id] = mom
    return max(0.001, min(0.999, mid + mom))


# ── Timeframe Presets ─────────────────────────────────────────────────

_TIMEFRAME_PRESETS = {
    "short": {
        "interval": 30,
        "cycles": 20,
        "take_profit_base": 0.03,   # 3% base TP
        "take_profit_max": 0.05,    # 5% max TP (with high edge)
        "stop_loss": 0.05,          # 5% SL
        "max_hold": 8,
        "trailing_stop": False,
        "trailing_activation": 0.0,
        "trailing_distance": 0.0,
        "time_scale": 1.0,          # micro-price amplitude
        "label": "Short-term (30s)",
    },
    "mid": {
        "interval": 300,            # 5 min
        "cycles": 30,
        "take_profit_base": 0.05,   # 5% base TP
        "take_profit_max": 0.12,    # 12% max TP
        "stop_loss": 0.08,          # 8% SL (wider)
        "max_hold": 20,             # hold up to 20 cycles (~100 min)
        "trailing_stop": True,
        "trailing_activation": 0.03, # activate after +3%
        "trailing_distance": 0.02,   # trail 2% below peak
        "time_scale": 3.0,
        "label": "Mid-term (5min)",
    },
    "long": {
        "interval": 1800,           # 30 min
        "cycles": 30,
        "take_profit_base": 0.08,   # 8% base TP
        "take_profit_max": 0.20,    # 20% max TP
        "stop_loss": 0.12,          # 12% SL (very wide)
        "max_hold": 30,             # hold up to 30 cycles (~15 hours)
        "trailing_stop": True,
        "trailing_activation": 0.05, # activate after +5%
        "trailing_distance": 0.03,   # trail 3% below peak
        "time_scale": 8.0,
        "label": "Long-term (30min)",
    },
}


def _variable_take_profit(
    base_tp: float,
    max_tp: float,
    edge: float,
    hold_cycles: int,
    max_hold: int,
) -> float:
    """Calculate variable take-profit threshold.

    Scales TP based on:
      1. Edge strength: higher edge → higher TP (let winners run)
      2. Hold time: TP decreases as we approach max_hold (take what you can)

    Returns the current TP threshold.
    """
    # Edge component: scale from base to max based on edge (0.08 → 0.30+)
    edge_factor = min(1.0, edge / 0.25)  # normalize edge to 0-1
    edge_tp = base_tp + (max_tp - base_tp) * edge_factor

    # Time decay: as hold time increases, lower TP to take profits sooner
    # After 75% of max_hold, start reducing TP back toward base
    hold_ratio = hold_cycles / max_hold if max_hold > 0 else 0.0
    if hold_ratio > 0.75:
        decay = (hold_ratio - 0.75) / 0.25  # 0 → 1 in last quarter
        edge_tp = edge_tp - (edge_tp - base_tp) * decay * 0.5

    return max(base_tp, edge_tp)


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
    cycles: int | None = None,
    interval: int | None = None,
    strategy_name: str = "mean_reversion",
    capital: float = 200.0,
    market_limit: int = 200,
    llm_provider: str | None = None,
    adaptive: bool = False,
    timeframe: str = "short",
):
    """Main trading loop — multi-timeframe, price-movement based."""
    # Apply timeframe preset, then override with explicit args
    preset = _TIMEFRAME_PRESETS[timeframe]
    cycles = cycles if cycles is not None else preset["cycles"]
    interval = interval if interval is not None else preset["interval"]

    print("=" * 60)
    print("  POLYMARKET PAPER TRADING BOT  v6.0 (Multi-Timeframe)")
    print(f"  Strategy   : {strategy_name}")
    print(f"  Capital    : \u20ac{capital:.2f}")
    print(f"  Timeframe  : {preset['label']}")
    print(f"  Cycles     : {cycles}")
    print(f"  Interval   : {interval}s")
    print(f"  Sizing     : {'Kelly (adaptive)' if adaptive else 'Fixed 10%'}")
    print(f"  Take Profit: {preset['take_profit_base']:.0%}-{preset['take_profit_max']:.0%} (variable)")
    print(f"  Stop Loss  : {preset['stop_loss']:.0%}")
    if preset['trailing_stop']:
        print(f"  Trailing   : ON (activate +{preset['trailing_activation']:.0%}, trail {preset['trailing_distance']:.0%})")
    print("=" * 60)

    # ── History & Adaptive Engine ──
    history_db = TradeHistoryDB()
    adaptive_engine = AdaptiveEngine(history_db)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if adaptive and history_db.count > 0:
        stats = adaptive_engine.train()
        adaptive_engine.print_training_report()
    elif adaptive:
        print("\n  No historical data yet — using default parameters.")
        print("  The bot will learn and adapt after this first run.")
        stats = adaptive_engine.stats
    else:
        stats = adaptive_engine.load_stats()

    trader = PaperTrader(initial_capital=capital)
    tracker = PriceTracker()

    # Use adaptive min-edge if enough data
    min_edge = stats.recommended_min_edge if adaptive and history_db.count >= 10 else 0.08

    sentiment_engine: SentimentEngine | None = None

    if strategy_name == "ensemble":
        sentiment_engine = SentimentEngine(
            llm_provider=llm_provider,
            sentiment_weight=0.30,
            max_articles=8,
        )
        mode_label = f"LLM ({llm_provider})" if llm_provider else "VADER"
        mean_rev = MeanReversionStrategy(low_threshold=0.40, high_threshold=0.60)
        prob_edge = ProbabilisticEdgeStrategy(min_edge=min_edge)
        sent_strat = SentimentEdgeStrategy(sentiment_engine, min_edge=min_edge)
        strategy = EnsembleStrategy(mean_rev, prob_edge, sent_strat, min_agree=2)
        print(f"  Ensemble   : MeanRev + ProbEdge + Sentiment({mode_label})")
        print(f"  Consensus  : ≥2/3 must agree to trade")
    elif strategy_name == "sentiment":
        sentiment_engine = SentimentEngine(
            llm_provider=llm_provider,
            sentiment_weight=0.30,
            max_articles=8,
        )
        mode_label = f"LLM ({llm_provider})" if llm_provider else "VADER"
        strategy = SentimentEdgeStrategy(sentiment_engine, min_edge=min_edge)
        print(f"  Sentiment  : {mode_label}")
    elif strategy_name == "prob":
        strategy = ProbabilisticEdgeStrategy(min_edge=min_edge)
    else:
        strategy = MeanReversionStrategy(low_threshold=0.40, high_threshold=0.60)

    if adaptive:
        print(f"  Min edge   : {min_edge:.0%} {'(learned)' if history_db.count >= 10 else '(default)'}")
        if stats.avoid_categories:
            print(f"  Avoiding   : {', '.join(stats.avoid_categories)}")

    # Exit thresholds from timeframe preset
    tp_base = preset["take_profit_base"]
    tp_max = preset["take_profit_max"]
    stop_loss_pct = preset["stop_loss"]
    max_hold_cycles = preset["max_hold"]
    use_trailing = preset["trailing_stop"]
    trailing_activation = preset["trailing_activation"]
    trailing_distance = preset["trailing_distance"]
    time_scale = preset["time_scale"]
    hold_counter = 0
    peak_pnl = 0.0  # track best unrealized PnL for trailing stop
    trade_edge = 0.0  # edge of current trade for variable TP

    # Track volumes and trade metadata for history
    _volumes: dict[str, float] = {}
    _trade_metadata: dict = {}  # metadata for current open trade

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
                    _save_trade_to_history(
                        trader, history_db, run_id, strategy_name,
                        _trade_metadata, "market-delisted", hold_counter,
                    )
                    hold_counter = 0
            else:
                api_mid = current_market["yes_price"]
                vol = current_market["volume"]

                # Simulate realistic current price (micro-fluctuation around API mid)
                sim_price = _micro_price(api_mid, vol, pos.market_id, time_scale=time_scale)
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

                # Update peak PnL for trailing stop
                if change_pct > peak_pnl:
                    peak_pnl = change_pct

                # Variable take-profit based on edge + hold time
                current_tp = _variable_take_profit(
                    tp_base, tp_max, trade_edge, hold_counter, max_hold_cycles,
                )

                # A) Take profit (variable)
                if change_pct >= current_tp:
                    print(f"  \u2191 Take profit! {change_pct:+.1%} (target was {current_tp:.1%})")
                    trader.close_position_at(sim_price, reason=f"take-profit {change_pct:+.1%}")
                    _save_trade_to_history(
                        trader, history_db, run_id, strategy_name,
                        _trade_metadata, "take-profit", hold_counter,
                    )
                    hold_counter = 0
                    peak_pnl = 0.0

                # B) Trailing stop: if we were up enough and now dropping back
                elif use_trailing and peak_pnl >= trailing_activation and change_pct <= peak_pnl - trailing_distance:
                    print(f"  \u21a9 Trailing stop! peak={peak_pnl:+.1%} \u2192 now={change_pct:+.1%} (trail={trailing_distance:.0%})")
                    trader.close_position_at(sim_price, reason=f"trailing-stop {change_pct:+.1%}")
                    _save_trade_to_history(
                        trader, history_db, run_id, strategy_name,
                        _trade_metadata, "trailing-stop", hold_counter,
                    )
                    hold_counter = 0
                    peak_pnl = 0.0

                # C) Stop loss
                elif change_pct <= -stop_loss_pct:
                    print(f"  \u2193 Stop loss! {change_pct:+.1%}")
                    trader.close_position_at(sim_price, reason=f"stop-loss {change_pct:+.1%}")
                    _save_trade_to_history(
                        trader, history_db, run_id, strategy_name,
                        _trade_metadata, "stop-loss", hold_counter,
                    )
                    hold_counter = 0
                    peak_pnl = 0.0

                # D) Max hold
                elif hold_counter >= max_hold_cycles:
                    print(f"  \u23f0 Max hold ({max_hold_cycles}) \u2014 closing at market ({change_pct:+.1%})")
                    trader.close_position_at(sim_price, reason=f"max-hold {change_pct:+.1%}")
                    _save_trade_to_history(
                        trader, history_db, run_id, strategy_name,
                        _trade_metadata, "max-hold", hold_counter,
                    )
                    hold_counter = 0
                    peak_pnl = 0.0

        # 4. Look for new trades
        if not trader.has_open_position:
            hold_counter = 0

            candidates = [m for m in markets if m["id"] not in _recently_traded]
            if not candidates:
                candidates = markets

            # Adaptive: skip categories that historically lose
            if adaptive and stats.avoid_categories:
                filtered = [m for m in candidates
                            if not adaptive_engine.should_skip_market(m["question"])]
                if filtered:
                    candidates = filtered

            # Prefer markets showing recent volatility
            volatile = [
                m for m in candidates
                if tracker.volatility(m["id"]) > 0.003 or tracker.n_records(m["id"]) < 3
            ]
            if volatile:
                candidates = volatile

            # For prob-edge, generate estimates using price momentum
            prob_strat = None
            if isinstance(strategy, ProbabilisticEdgeStrategy):
                prob_strat = strategy
            elif isinstance(strategy, EnsembleStrategy):
                prob_strat = strategy.prob_edge
            if prob_strat is not None:
                for m in candidates:
                    delta = tracker.price_delta(m["id"])
                    trend = delta * 0.5
                    noise = random.gauss(0, 0.06)
                    est = max(0.01, min(0.99, m["yes_price"] + trend + noise))
                    prob_strat.set_estimate(m["id"], est)

            # For sentiment/ensemble strategy, clear cache each cycle for fresh news
            if sentiment_engine is not None:
                sentiment_engine.clear_cache()

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

                # Calculate position size
                if adaptive:
                    trade_size = kelly_size(
                        edge=best_sig.confidence,
                        win_rate=stats.overall_win_rate if stats.total_trades > 0 else 0.35,
                        avg_win=stats.avg_win_pnl if stats.avg_win_pnl > 0 else 1.0,
                        avg_loss=stats.avg_loss_pnl if stats.avg_loss_pnl > 0 else 1.0,
                        balance=trader.balance,
                    )
                    pct = trade_size / trader.balance * 100 if trader.balance > 0 else 0.0
                    print(f"    Kelly size=\u20ac{trade_size:.2f} ({pct:.1f}% of balance)")
                else:
                    trade_size = None  # use default fixed 10%

                trader.process_signal(best_sig, size_override=trade_size)
                # Adjust entry to the realistic fill price
                if trader.has_open_position:
                    trader.position.entry_price = fill_price
                    trade_edge = best_sig.confidence
                    peak_pnl = 0.0
                    # Store metadata for history
                    _trade_metadata = {
                        "edge": best_sig.confidence,
                        "sentiment_score": 0.0,
                        "estimated_prob": 0.0,
                        "articles_found": 0,
                        "market_volume": best_mkt["volume"],
                    }
                    # Extract sentiment metadata from reason string
                    reason = best_sig.reason
                    if "sent=" in reason:
                        try:
                            _trade_metadata["sentiment_score"] = float(
                                reason.split("sent=")[1].split(",")[0]
                            )
                        except (IndexError, ValueError):
                            pass
                    if "est=" in reason:
                        try:
                            _trade_metadata["estimated_prob"] = float(
                                reason.split("est=")[1].split(",")[0]
                            )
                        except (IndexError, ValueError):
                            pass
                    if "news=" in reason:
                        try:
                            _trade_metadata["articles_found"] = int(
                                reason.split("news=")[1].split(",")[0].split(")")[0]
                            )
                        except (IndexError, ValueError):
                            pass

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
            _save_trade_to_history(
                trader, history_db, run_id, strategy_name,
                _trade_metadata, "session-end", hold_counter,
            )

    trader.print_summary()
    trader.save_trade_log()
    trader.save_equity_curve()

    # Retrain and show report if we have trades
    if history_db.count > 0:
        stats = adaptive_engine.train()
        adaptive_engine.print_training_report()
        print(f"  History: {history_db.count} total trades saved to history/trade_history.json")

    return trader


def _save_trade_to_history(
    trader: PaperTrader,
    db: TradeHistoryDB,
    run_id: str,
    strategy_name: str,
    metadata: dict,
    exit_reason: str,
    hold_cycles: int,
):
    """Save the most recent closed trade to the persistent history."""
    if not trader.trade_log:
        return
    t = trader.trade_log[-1]
    entry = TradeEntry(
        trade_id=t.trade_id,
        run_id=run_id,
        timestamp=t.closed_at,
        market_id=t.market_id,
        question=t.question,
        side=t.side,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        size=t.size,
        pnl=t.pnl,
        balance_after=t.balance_after,
        strategy=strategy_name,
        edge=metadata.get("edge", 0.0),
        sentiment_score=metadata.get("sentiment_score", 0.0),
        estimated_prob=metadata.get("estimated_prob", 0.0),
        articles_found=metadata.get("articles_found", 0),
        exit_reason=exit_reason,
        hold_cycles=hold_cycles,
        market_volume=metadata.get("market_volume", 0.0),
        win=t.pnl > 0,
    )
    db.add(entry)


def main():
    parser = argparse.ArgumentParser(description="Polymarket Paper Trading Bot (Multi-Timeframe)")
    parser.add_argument("--timeframe", choices=["short", "mid", "long"], default="short",
                        help="Timeframe: short (30s), mid (5min), long (30min)")
    parser.add_argument("--cycles", type=int, default=None,
                        help="Trading cycles (default: from timeframe preset)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Seconds between cycles (default: from timeframe preset)")
    parser.add_argument("--strategy", choices=["mean_reversion", "prob", "sentiment", "ensemble"], default="mean_reversion",
                        help="Strategy: mean_reversion, prob, sentiment, or ensemble (all 3 combined)")
    parser.add_argument("--llm", choices=["openai", "groq"], default=None,
                        help="LLM provider for sentiment strategy (default: VADER, no API key needed)")
    parser.add_argument("--capital", type=float, default=200.0, help="Initial capital in \u20ac (default: 200)")
    parser.add_argument("--markets", type=int, default=200, help="Markets to fetch per cycle (default: 200)")
    parser.add_argument("--adaptive", action="store_true", default=False,
                        help="Enable adaptive mode: Kelly sizing + learned parameters")
    args = parser.parse_args()

    run_bot(
        cycles=args.cycles,
        interval=args.interval,
        strategy_name=args.strategy,
        capital=args.capital,
        market_limit=args.markets,
        llm_provider=args.llm,
        adaptive=args.adaptive,
        timeframe=args.timeframe,
    )


if __name__ == "__main__":
    main()
