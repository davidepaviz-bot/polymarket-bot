"""
Trade History & Adaptive Engine – v5.0

Persistent storage of all trade data with metadata (sentiment, edge,
market category).  Provides:

  1. **TradeHistoryDB** — append-only JSON store that accumulates across runs.
  2. **AdaptiveEngine** — analyses historical performance and adjusts:
       • minimum-edge thresholds per sentiment band
       • market category preferences (avoid consistently losing categories)
  3. **kelly_size** — Kelly Criterion position sizing: higher edge / win-rate
     → larger position, lower → smaller.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional


# ── Persistent trade history ──────────────────────────────────────

HISTORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "history")
HISTORY_FILE = os.path.join(HISTORY_DIR, "trade_history.json")
TRAINING_FILE = os.path.join(HISTORY_DIR, "training_stats.json")


@dataclass
class TradeEntry:
    """Rich trade record with all metadata for training."""
    trade_id: int
    run_id: str
    timestamp: str
    market_id: str
    question: str
    side: str                  # YES / NO
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    balance_after: float
    strategy: str              # mean_reversion / prob / sentiment
    edge: float                # estimated edge at entry
    sentiment_score: float     # VADER score (-1..+1), 0 if not sentiment
    estimated_prob: float      # model's probability estimate
    articles_found: int        # news articles found (0 if not sentiment)
    exit_reason: str           # stop-loss / take-profit / max-hold / session-end
    hold_cycles: int           # how many cycles was the position held
    market_volume: float       # volume at entry
    win: bool                  # pnl > 0


class TradeHistoryDB:
    """Append-only JSON store for trade history.

    Each run appends new trades; the file accumulates across sessions.
    Thread-safe for single-process use (we hold a list in memory and
    flush after every trade).
    """

    def __init__(self, filepath: str = HISTORY_FILE):
        self.filepath = filepath
        self._trades: list[dict] = []
        self._load()

    def _load(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    self._trades = data if isinstance(data, list) else []
            except (json.JSONDecodeError, IOError):
                self._trades = []

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as fh:
            json.dump(self._trades, fh, indent=2, ensure_ascii=False)

    def add(self, entry: TradeEntry):
        self._trades.append(asdict(entry))
        self._save()

    @property
    def trades(self) -> list[dict]:
        return list(self._trades)

    @property
    def count(self) -> int:
        return len(self._trades)

    def recent(self, n: int = 50) -> list[dict]:
        return self._trades[-n:]

    def wins(self) -> list[dict]:
        return [t for t in self._trades if t.get("win")]

    def losses(self) -> list[dict]:
        return [t for t in self._trades if not t.get("win")]


# ── Adaptive Engine ───────────────────────────────────────────────

@dataclass
class TrainingStats:
    """Learned parameters from historical trades."""
    total_trades: int = 0
    total_wins: int = 0
    overall_win_rate: float = 0.0

    # Win rate by edge band: "low" (8-12%), "mid" (12-18%), "high" (>18%)
    edge_band_stats: dict = field(default_factory=lambda: {
        "low": {"trades": 0, "wins": 0, "win_rate": 0.0},
        "mid": {"trades": 0, "wins": 0, "win_rate": 0.0},
        "high": {"trades": 0, "wins": 0, "win_rate": 0.0},
    })

    # Win rate by sentiment band: "negative" (<-0.1), "neutral", "positive" (>0.1)
    sentiment_band_stats: dict = field(default_factory=lambda: {
        "negative": {"trades": 0, "wins": 0, "win_rate": 0.0},
        "neutral": {"trades": 0, "wins": 0, "win_rate": 0.0},
        "positive": {"trades": 0, "wins": 0, "win_rate": 0.0},
    })

    # Per-category tracking (extracted from question keywords)
    category_stats: dict = field(default_factory=dict)

    # Recommended adjustments
    recommended_min_edge: float = 0.08
    avoid_categories: list = field(default_factory=list)
    best_edge_band: str = "high"

    # Average PnL per trade for Kelly
    avg_win_pnl: float = 0.0
    avg_loss_pnl: float = 0.0


def _edge_band(edge: float) -> str:
    """Classify edge into bands."""
    e = abs(edge)
    if e >= 0.18:
        return "high"
    if e >= 0.12:
        return "mid"
    return "low"


def _sentiment_band(score: float) -> str:
    """Classify sentiment score into bands."""
    if score > 0.10:
        return "positive"
    if score < -0.10:
        return "negative"
    return "neutral"


_CATEGORY_KEYWORDS = {
    "nba": ["nba", "celtics", "lakers", "pistons", "knicks", "basketball", "finals"],
    "nfl": ["nfl", "super bowl", "chiefs", "eagles", "football"],
    "nhl": ["nhl", "stanley cup", "hurricanes", "avalanche", "hockey"],
    "soccer": ["fifa", "world cup", "premier league", "champions league",
               "soccer", "football", "goal scorer", "greenwood"],
    "politics": ["president", "election", "trump", "biden", "governor",
                 "senator", "congress", "vance", "rubio", "putin"],
    "crypto": ["bitcoin", "ethereum", "crypto", "token", "airdrop", "megaeth"],
    "entertainment": ["oscar", "grammy", "james bond", "movie", "film", "album"],
}


def _detect_category(question: str) -> str:
    """Detect market category from question text."""
    q = question.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return cat
    return "other"


class AdaptiveEngine:
    """Analyse historical trades and produce training stats / recommendations."""

    def __init__(self, db: TradeHistoryDB, stats_file: str = TRAINING_FILE):
        self.db = db
        self.stats_file = stats_file
        self.stats = TrainingStats()

    def train(self) -> TrainingStats:
        """Recompute training stats from all historical trades."""
        trades = self.db.trades
        if not trades:
            return self.stats

        total = len(trades)
        wins = [t for t in trades if t.get("win")]
        losses = [t for t in trades if not t.get("win")]

        self.stats.total_trades = total
        self.stats.total_wins = len(wins)
        self.stats.overall_win_rate = len(wins) / total if total else 0.0

        # Edge band stats
        for band in ["low", "mid", "high"]:
            band_trades = [t for t in trades if _edge_band(t.get("edge", 0)) == band]
            band_wins = [t for t in band_trades if t.get("win")]
            n = len(band_trades)
            self.stats.edge_band_stats[band] = {
                "trades": n,
                "wins": len(band_wins),
                "win_rate": len(band_wins) / n if n else 0.0,
            }

        # Sentiment band stats
        for band in ["negative", "neutral", "positive"]:
            band_trades = [t for t in trades
                           if _sentiment_band(t.get("sentiment_score", 0)) == band]
            band_wins = [t for t in band_trades if t.get("win")]
            n = len(band_trades)
            self.stats.sentiment_band_stats[band] = {
                "trades": n,
                "wins": len(band_wins),
                "win_rate": len(band_wins) / n if n else 0.0,
            }

        # Category stats
        cat_data: dict[str, dict] = {}
        for t in trades:
            cat = _detect_category(t.get("question", ""))
            if cat not in cat_data:
                cat_data[cat] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            cat_data[cat]["trades"] += 1
            if t.get("win"):
                cat_data[cat]["wins"] += 1
            cat_data[cat]["total_pnl"] += t.get("pnl", 0)

        for cat in cat_data:
            n = cat_data[cat]["trades"]
            cat_data[cat]["win_rate"] = cat_data[cat]["wins"] / n if n else 0.0
        self.stats.category_stats = cat_data

        # Average PnL for Kelly
        if wins:
            self.stats.avg_win_pnl = sum(t.get("pnl", 0) for t in wins) / len(wins)
        if losses:
            self.stats.avg_loss_pnl = abs(sum(t.get("pnl", 0) for t in losses) / len(losses))

        # Recommendations
        self._compute_recommendations()
        self._save_stats()
        return self.stats

    def _compute_recommendations(self):
        """Derive actionable parameters from stats."""
        # 1. Recommended min-edge: if low-edge trades lose a lot, raise threshold
        low = self.stats.edge_band_stats["low"]
        mid = self.stats.edge_band_stats["mid"]
        high = self.stats.edge_band_stats["high"]

        if low["trades"] >= 5 and low["win_rate"] < 0.25:
            # Low-edge trades are losing badly → raise minimum to mid
            self.stats.recommended_min_edge = 0.12
        elif mid["trades"] >= 5 and mid["win_rate"] < 0.25:
            # Even mid-edge is bad → raise to high
            self.stats.recommended_min_edge = 0.18
        else:
            self.stats.recommended_min_edge = 0.08

        # 2. Best edge band
        best_band = "high"
        best_wr = 0.0
        for band in ["low", "mid", "high"]:
            bs = self.stats.edge_band_stats[band]
            if bs["trades"] >= 3 and bs["win_rate"] > best_wr:
                best_wr = bs["win_rate"]
                best_band = band
        self.stats.best_edge_band = best_band

        # 3. Categories to avoid: <20% win rate with 5+ trades
        avoid = []
        for cat, data in self.stats.category_stats.items():
            if data["trades"] >= 5 and data["win_rate"] < 0.20:
                avoid.append(cat)
        self.stats.avoid_categories = avoid

    def _save_stats(self):
        os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
        with open(self.stats_file, "w", encoding="utf-8") as fh:
            json.dump(asdict(self.stats), fh, indent=2, ensure_ascii=False)

    def load_stats(self) -> TrainingStats:
        """Load previously computed stats (fast, no recomputation)."""
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                s = TrainingStats()
                for k, v in data.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                self.stats = s
            except (json.JSONDecodeError, IOError):
                pass
        return self.stats

    def should_skip_market(self, question: str) -> bool:
        """Return True if this market's category should be avoided."""
        cat = _detect_category(question)
        return cat in self.stats.avoid_categories

    def print_training_report(self):
        """Print a human-readable training report."""
        s = self.stats
        print("\n" + "=" * 60)
        print("  ADAPTIVE TRAINING REPORT")
        print("=" * 60)
        print(f"  Historical trades  : {s.total_trades}")
        print(f"  Overall win rate   : {s.overall_win_rate:.1%}")
        print(f"  Avg win PnL        : \u20ac{s.avg_win_pnl:+.2f}")
        print(f"  Avg loss PnL       : \u20ac{-s.avg_loss_pnl:.2f}")

        print("\n  Edge Band Performance:")
        for band in ["low", "mid", "high"]:
            bs = s.edge_band_stats[band]
            wr = f"{bs['win_rate']:.0%}" if bs["trades"] else "n/a"
            print(f"    {band:>5s}: {bs['trades']:3d} trades, win rate {wr}")

        print("\n  Sentiment Band Performance:")
        for band in ["negative", "neutral", "positive"]:
            bs = s.sentiment_band_stats[band]
            wr = f"{bs['win_rate']:.0%}" if bs["trades"] else "n/a"
            print(f"    {band:>9s}: {bs['trades']:3d} trades, win rate {wr}")

        if s.category_stats:
            print("\n  Market Category Performance:")
            for cat, data in sorted(s.category_stats.items(),
                                    key=lambda x: x[1]["total_pnl"]):
                wr = f"{data['win_rate']:.0%}"
                flag = " \u26a0 AVOID" if cat in s.avoid_categories else ""
                print(f"    {cat:>15s}: {data['trades']:3d} trades, "
                      f"win rate {wr}, PnL \u20ac{data['total_pnl']:+.2f}{flag}")

        print(f"\n  \u2192 Recommended min-edge : {s.recommended_min_edge:.0%}")
        print(f"  \u2192 Best edge band       : {s.best_edge_band}")
        if s.avoid_categories:
            print(f"  \u2192 Avoid categories     : {', '.join(s.avoid_categories)}")
        print("=" * 60 + "\n")


# ── Kelly Criterion Position Sizing ───────────────────────────────

def kelly_size(
    edge: float,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    balance: float,
    min_pct: float = 0.03,
    max_pct: float = 0.25,
    fraction: float = 0.5,
) -> float:
    """Calculate position size using fractional Kelly Criterion.

    Kelly formula: f* = (p * b - q) / b
    where:
      p = probability of winning (historical win rate adjusted by edge)
      q = 1 - p
      b = ratio of avg_win / avg_loss (odds)

    We use *half-Kelly* by default (fraction=0.5) for safety, and clamp
    the result between min_pct and max_pct of current balance.

    Parameters
    ----------
    edge : float
        Absolute edge of this trade (e.g. 0.15 = 15%)
    win_rate : float
        Historical overall win rate (0-1)
    avg_win : float
        Average winning trade PnL (positive)
    avg_loss : float
        Average losing trade PnL (positive, absolute value)
    balance : float
        Current account balance
    min_pct : float
        Minimum position as % of balance (default 3%)
    max_pct : float
        Maximum position as % of balance (default 25%)
    fraction : float
        Kelly fraction (0.5 = half-Kelly, safer)

    Returns
    -------
    float
        Position size in currency
    """
    if avg_loss <= 0 or balance <= 0:
        return balance * min_pct

    # Adjust win rate by edge strength: higher edge → slightly higher expected p
    p = min(0.95, win_rate + edge * 0.3)
    q = 1.0 - p
    b = avg_win / avg_loss if avg_loss > 0 else 1.0

    # Kelly fraction
    kelly_f = (p * b - q) / b if b > 0 else 0.0
    kelly_f = max(0.0, kelly_f) * fraction

    # Clamp
    pct = max(min_pct, min(max_pct, kelly_f))
    size = round(balance * pct, 2)
    return max(0.01, size)


def fixed_size(balance: float, pct: float = 0.10) -> float:
    """Original fixed position sizing (fallback)."""
    return round(balance * pct, 2)
