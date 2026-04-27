"""
Strategy Engine – signal generation for Polymarket paper trading.

Implements four strategies:
  A) Mean Reversion: buy YES when cheap, buy NO when expensive.
  B) Probabilistic Edge: compare market price against an estimated
     probability and trade when the edge exceeds a threshold.
  C) Sentiment Edge: use news sentiment to estimate true probability
     and trade when the edge exceeds a threshold.
  D) Ensemble: combines all three — trades only when 2/3 agree.

Includes volatility filter to focus on markets with real opportunity.
"""

from __future__ import annotations

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


def filter_volatile_markets(
    markets: list[dict],
    min_price: float = 0.10,
    max_price: float = 0.90,
    min_volume: float = 1_000,
) -> list[dict]:
    """
    Filter markets to those with real trading opportunity:
      - YES price in [min_price, max_price] (not near 0 or 1)
      - Minimum volume threshold for liquidity
    """
    return [
        m for m in markets
        if min_price <= m["yes_price"] <= max_price
        and m["volume"] >= min_volume
    ]


# ---------------------------------------------------------------------------
# Strategy A – Mean Reversion
# ---------------------------------------------------------------------------

class MeanReversionStrategy:
    """
    Mean-reversion logic with volatility awareness:
      • If YES price < low_threshold  → BUY YES  (market undervalues outcome)
      • If YES price > high_threshold → BUY NO   (market overvalues outcome)
      • Otherwise                     → HOLD
    """

    def __init__(self, low_threshold: float = 0.40, high_threshold: float = 0.60):
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
                reason=f"Mean-reversion: YES {yes_price:.2f} < {self.low}",
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
                reason=f"Mean-reversion: YES {yes_price:.2f} > {self.high}",
                confidence=min(confidence, 1.0),
            )
        return Signal(
            side=Side.HOLD,
            market_id=market["id"],
            question=market["question"],
            yes_price=yes_price,
            no_price=market["no_price"],
            reason=f"Mean-reversion: YES {yes_price:.2f} within [{self.low}, {self.high}]",
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
        edge = est - yes_price
        if edge > self.min_edge:
            return Signal(
                side=Side.BUY_YES,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Prob-edge: est={est:.2f}, mkt={yes_price:.2f}, edge={edge:+.2f}",
                confidence=min(abs(edge), 1.0),
            )
        if edge < -self.min_edge:
            return Signal(
                side=Side.BUY_NO,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Prob-edge: est={est:.2f}, mkt={yes_price:.2f}, edge={edge:+.2f}",
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


# ---------------------------------------------------------------------------
# Strategy C – Sentiment Edge (news-driven)
# ---------------------------------------------------------------------------

class SentimentEdgeStrategy:
    """Trade when news sentiment suggests the market is mispriced.

    Requires a ``SentimentEngine`` instance (passed at init) that fetches
    news and computes probability estimates.  The strategy trades when
    the edge (estimated_prob - market_price) exceeds *min_edge*.

    Parameters
    ----------
    sentiment_engine : SentimentEngine
        Engine that provides ``analyze(market)`` → ``SentimentResult``.
    min_edge : float
        Minimum |edge| required to generate a signal (default 0.08).
    min_articles : int
        Ignore markets where fewer than this many articles were found.
    """

    def __init__(
        self,
        sentiment_engine: "SentimentEngine",
        min_edge: float = 0.08,
        min_articles: int = 1,
    ):
        from polymarket_bot.sentiment_engine import SentimentEngine
        self.engine: SentimentEngine = sentiment_engine
        self.min_edge = min_edge
        self.min_articles = min_articles

    def evaluate(self, market: dict) -> Signal:
        market_id = market["id"]
        yes_price = market["yes_price"]

        result = self.engine.analyze(market)

        if result.articles_found < self.min_articles:
            return Signal(
                side=Side.HOLD,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Sentiment: only {result.articles_found} articles (need {self.min_articles})",
                confidence=0.0,
            )

        edge = result.edge
        mode = "LLM" if result.llm_used else "VADER"

        if edge > self.min_edge:
            return Signal(
                side=Side.BUY_YES,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=(
                    f"Sentiment({mode}): est={result.estimated_probability:.2f}, "
                    f"mkt={yes_price:.2f}, edge={edge:+.2f}, "
                    f"sent={result.avg_sentiment:+.2f}, news={result.articles_found}"
                ),
                confidence=min(abs(edge), 1.0),
            )
        if edge < -self.min_edge:
            return Signal(
                side=Side.BUY_NO,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=(
                    f"Sentiment({mode}): est={result.estimated_probability:.2f}, "
                    f"mkt={yes_price:.2f}, edge={edge:+.2f}, "
                    f"sent={result.avg_sentiment:+.2f}, news={result.articles_found}"
                ),
                confidence=min(abs(edge), 1.0),
            )
        return Signal(
            side=Side.HOLD,
            market_id=market_id,
            question=market["question"],
            yes_price=yes_price,
            no_price=market["no_price"],
            reason=(
                f"Sentiment({mode}): edge {edge:+.2f} within ±{self.min_edge}, "
                f"sent={result.avg_sentiment:+.2f}, news={result.articles_found}"
            ),
            confidence=0.0,
        )


# ---------------------------------------------------------------------------
# Strategy D – Ensemble (combines all three)
# ---------------------------------------------------------------------------

class EnsembleStrategy:
    """Combine mean-reversion, prob-edge, and sentiment into one signal.

    Trades only when at least ``min_agree`` strategies agree on the same
    side (BUY_YES or BUY_NO).  Confidence is the average of the agreeing
    strategies.  The reason string lists each strategy's vote.

    Parameters
    ----------
    mean_rev : MeanReversionStrategy
    prob_edge : ProbabilisticEdgeStrategy
    sentiment : SentimentEdgeStrategy
    min_agree : int
        Minimum number of strategies that must agree (default 2 out of 3).
    """

    def __init__(
        self,
        mean_rev: MeanReversionStrategy,
        prob_edge: ProbabilisticEdgeStrategy,
        sentiment: SentimentEdgeStrategy,
        min_agree: int = 2,
    ):
        self.strategies = [
            ("MeanRev", mean_rev),
            ("ProbEdge", prob_edge),
            ("Sentiment", sentiment),
        ]
        self.min_agree = min_agree

    @property
    def prob_edge(self) -> ProbabilisticEdgeStrategy:
        return self.strategies[1][1]

    @property
    def sentiment_engine(self) -> "SentimentEngine":
        return self.strategies[2][1].engine

    def evaluate(self, market: dict) -> Signal:
        market_id = market["id"]
        yes_price = market["yes_price"]

        votes: dict[Side, list[tuple[str, float]]] = {
            Side.BUY_YES: [],
            Side.BUY_NO: [],
        }
        vote_reasons: list[str] = []
        sentiment_reason = ""

        for name, strat in self.strategies:
            sig = strat.evaluate(market)
            short = sig.side.value if sig.side != Side.HOLD else "HOLD"
            vote_reasons.append(f"{name}={short}")
            if sig.side in votes:
                votes[sig.side].append((name, sig.confidence))
            if name == "Sentiment":
                sentiment_reason = sig.reason

        # Find the side with most agreement
        best_side = Side.HOLD
        best_voters: list[tuple[str, float]] = []
        for side, voters in votes.items():
            if len(voters) >= self.min_agree and len(voters) > len(best_voters):
                best_side = side
                best_voters = voters

        votes_str = ", ".join(vote_reasons)

        if best_side == Side.HOLD:
            return Signal(
                side=Side.HOLD,
                market_id=market_id,
                question=market["question"],
                yes_price=yes_price,
                no_price=market["no_price"],
                reason=f"Ensemble: no consensus ({votes_str})",
                confidence=0.0,
            )

        avg_conf = sum(c for _, c in best_voters) / len(best_voters)
        agree_names = "+".join(n for n, _ in best_voters)

        # Include sentiment metadata so adaptive engine can parse sent=/est=/news=
        reason = f"Ensemble({agree_names}): {len(best_voters)}/3 agree | {votes_str}"
        if sentiment_reason:
            reason = f"{reason} | {sentiment_reason}"

        return Signal(
            side=best_side,
            market_id=market_id,
            question=market["question"],
            yes_price=yes_price,
            no_price=market["no_price"],
            reason=reason,
            confidence=min(avg_conf, 1.0),
        )
