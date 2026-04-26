"""
Strategy Engine – signal generation for Polymarket paper trading.

Implements two strategies:
  A) Mean Reversion: buy YES when cheap, buy NO when expensive.
  B) Probabilistic Edge: compare market price against an estimated
     probability and trade when the edge exceeds a threshold.
"""

from dataclasses import dataclass
from enum import Enum


class Side(Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    HOLD = "HOLD"


@dataclass
class Signal:
    side: Side
    market_id: str
    question: str
    yes_price: float
    no_price: float
    reason: str
    confidence: float  # 0-1


# ---------------------------------------------------------------------------
# Strategy A – Mean Reversion
# ---------------------------------------------------------------------------

class MeanReversionStrategy:
    """
    Simple mean-reversion logic:
      • If YES price < low_threshold  → BUY YES  (market undervalues outcome)
      • If YES price > high_threshold → BUY NO   (market overvalues outcome)
      • Otherwise                     → HOLD
    """

    def __init__(self, low_threshold: float = 0.45, high_threshold: float = 0.55):
        self.low = low_threshold
        self.high = high_threshold

    def evaluate(self, market: dict) -> Signal:
        yes_price = market["yes_price"]
        if yes_price < self.low:
            confidence = (self.low - yes_price) / self.low
            return Signal(
                side=Side.BUY_YES,
                market_id=market["id"],
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Mean-reversion: YES price {yes_price:.2f} < {self.low}",
                confidence=min(confidence, 1.0),
            )
        if yes_price > self.high:
            confidence = (yes_price - self.high) / (1.0 - self.high)
            return Signal(
                side=Side.BUY_NO,
                market_id=market["id"],
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Mean-reversion: YES price {yes_price:.2f} > {self.high}",
                confidence=min(confidence, 1.0),
            )
        return Signal(
            side=Side.HOLD,
            market_id=market["id"],
            question=market["question"],
            yes_price=yes_price,
            no_price=market["no_price"],
            reason=f"Mean-reversion: YES price {yes_price:.2f} within [{self.low}, {self.high}]",
            confidence=0.0,
        )


# ---------------------------------------------------------------------------
# Strategy B – Probabilistic Edge
# ---------------------------------------------------------------------------

class ProbabilisticEdgeStrategy:
    """
    Compare the market-implied probability (YES price) against an external
    estimated probability. Trade when the absolute edge exceeds *min_edge*.

    The estimated probability can be supplied per-market via a lookup dict;
    if not available the strategy defaults to HOLD.
    """

    def __init__(self, estimates: dict[str, float] | None = None, min_edge: float = 0.10):
        self.estimates = estimates or {}
        self.min_edge = min_edge

    def set_estimate(self, market_id: str, prob: float):
        self.estimates[market_id] = prob

    def evaluate(self, market: dict) -> Signal:
        market_id = market["id"]
        yes_price = market["yes_price"]
        est = self.estimates.get(market_id)
        if est is None:
            return Signal(
                side=Side.HOLD,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason="Prob-edge: no estimate available",
                confidence=0.0,
            )
        edge = est - yes_price  # positive → YES is undervalued
        if edge > self.min_edge:
            return Signal(
                side=Side.BUY_YES,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Prob-edge: est={est:.2f}, market={yes_price:.2f}, edge={edge:+.2f}",
                confidence=min(abs(edge), 1.0),
            )
        if edge < -self.min_edge:
            return Signal(
                side=Side.BUY_NO,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Prob-edge: est={est:.2f}, market={yes_price:.2f}, edge={edge:+.2f}",
                confidence=min(abs(edge), 1.0),
            )
        return Signal(
            side=Side.HOLD,
            market_id=market_id,
            question=market["question"],
            yes_price=yes_price,
            no_price=market["no_price"],
            reason=f"Prob-edge: edge {edge:+.2f} within ±{self.min_edge}",
            confidence=0.0,
        )
