"""
Sentiment Engine – news-driven probability estimation for Polymarket.

Two modes:
  A) Lightweight (default): Google News RSS + VADER sentiment analysis.
     Zero config, no API keys needed.
  B) LLM-powered (optional): sends article context to an OpenAI-compatible
     API for structured probability estimation.  Requires OPENAI_API_KEY
     or GROQ_API_KEY environment variable.

Usage:
    engine = SentimentEngine()                       # lightweight
    engine = SentimentEngine(llm_provider="openai")  # LLM mode
    result = engine.analyze(market)
"""

from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ── Data structures ───────────────────────────────────────────────


@dataclass
class Article:
    title: str
    source: str
    link: str
    published: str = ""


@dataclass
class SentimentResult:
    market_id: str
    question: str
    articles_found: int
    avg_sentiment: float          # -1 to +1  (VADER compound)
    estimated_probability: float  # 0 to 1
    market_price: float           # current YES price
    edge: float                   # estimated_probability - market_price
    headlines: list[str] = field(default_factory=list)
    llm_used: bool = False


# ── Keyword extraction ────────────────────────────────────────────

# Common stop-words to strip from search queries
_STOP = {
    "will", "the", "a", "an", "be", "by", "in", "on", "of", "to", "for",
    "is", "it", "at", "or", "and", "from", "with", "before", "after",
    "than", "this", "that", "its", "as", "if", "do", "does", "did",
    "has", "have", "had", "not", "no", "yes", "what", "which", "who",
    "whom", "when", "where", "how", "any", "all", "each", "every",
    "more", "most", "other", "some", "such", "only", "own", "out",
    "up", "over", "into", "through", "during", "between", "above",
    "below", "again", "further", "then", "once", "can", "could",
    "would", "should", "may", "might", "must", "shall", "about",
}


def extract_keywords(question: str, max_keywords: int = 6) -> list[str]:
    """Pull meaningful keywords from a market question for news search."""
    # Remove punctuation except hyphens and apostrophes
    cleaned = re.sub(r"[^\w\s\-']", " ", question)
    words = cleaned.split()

    keywords: list[str] = []
    for w in words:
        low = w.lower().strip("'")
        # Keep capitalised words (proper nouns), numbers, and long words
        if low in _STOP or len(low) < 2:
            continue
        keywords.append(w)

    # Prioritise capitalised/proper-noun tokens
    proper = [k for k in keywords if k[0].isupper()]
    rest = [k for k in keywords if not k[0].isupper()]
    ordered = proper + rest
    return ordered[:max_keywords]


# ── News fetching ─────────────────────────────────────────────────

def fetch_news_rss(keywords: list[str], max_articles: int = 8) -> list[Article]:
    """Fetch recent news from Google News RSS (no API key needed)."""
    query = quote_plus(" ".join(keywords))
    url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"

    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PolymarketBot/1.0)"
        })
        resp.raise_for_status()
    except requests.RequestException:
        return []

    articles: list[Article] = []
    # Lightweight XML parsing — no lxml dependency needed
    items = re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL)
    for item_xml in items[:max_articles]:
        title_m = re.search(r"<title>(.*?)</title>", item_xml)
        link_m = re.search(r"<link/>\s*(.*?)[\s<]", item_xml)
        source_m = re.search(r"<source[^>]*>(.*?)</source>", item_xml)
        pub_m = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)
        if title_m:
            articles.append(Article(
                title=_clean_html(title_m.group(1)),
                source=source_m.group(1) if source_m else "Unknown",
                link=link_m.group(1).strip() if link_m else "",
                published=pub_m.group(1) if pub_m else "",
            ))
    return articles


def _clean_html(text: str) -> str:
    """Strip HTML tags and entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#39;", "'").replace("&quot;", '"')
    return text.strip()


# ── VADER sentiment ───────────────────────────────────────────────

_vader = SentimentIntensityAnalyzer()


def vader_sentiment(text: str) -> float:
    """Return VADER compound score (-1 to +1) for *text*."""
    return _vader.polarity_scores(text)["compound"]


def sentiment_to_probability(
    avg_sentiment: float,
    market_price: float,
    sentiment_weight: float = 0.30,
) -> float:
    """Convert average sentiment + market price into a probability estimate.

    We blend the market price (crowd wisdom) with a sentiment-derived
    adjustment.  This avoids overriding the market entirely — we only
    *nudge* the probability based on news tone.

    sentiment_weight controls how much the news moves the estimate:
      0.0 → estimate equals market price (no effect)
      1.0 → estimate derived purely from sentiment
    """
    # Map sentiment (-1..+1) to a probability-like value (0.1..0.9)
    # Positive news → higher probability, negative → lower
    sentiment_prob = 0.5 + avg_sentiment * 0.4  # clamp later
    sentiment_prob = max(0.05, min(0.95, sentiment_prob))

    # Blend with market price
    estimated = market_price * (1 - sentiment_weight) + sentiment_prob * sentiment_weight
    return max(0.01, min(0.99, estimated))


# ── LLM-based estimation ─────────────────────────────────────────

_LLM_PROVIDERS = {
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "env_key": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "env_key": "GROQ_API_KEY",
        "model": "llama-3.1-8b-instant",
    },
}


def llm_estimate_probability(
    question: str,
    headlines: list[str],
    market_price: float,
    provider: str = "openai",
) -> float | None:
    """Ask an LLM to estimate the probability of the event.

    Returns a float 0-1 or None if the call fails.
    """
    cfg = _LLM_PROVIDERS.get(provider)
    if cfg is None:
        return None

    api_key = os.environ.get(cfg["env_key"], "")
    if not api_key:
        return None

    news_block = "\n".join(f"- {h}" for h in headlines[:10])
    prompt = (
        f"You are a prediction market analyst. A market asks:\n"
        f'"{question}"\n\n'
        f"Current market price (probability): {market_price:.2f}\n\n"
        f"Recent news headlines:\n{news_block}\n\n"
        f"Based on the news sentiment and your knowledge, estimate the "
        f"true probability (0.00 to 1.00) that this event will happen. "
        f"Reply with ONLY a number between 0.00 and 1.00, nothing else."
    )

    try:
        resp = requests.post(
            cfg["url"],
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
                "temperature": 0.2,
            },
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        # Extract first float from response
        match = re.search(r"(\d+\.?\d*)", text)
        if match:
            val = float(match.group(1))
            if val > 1.0:
                val /= 100.0  # handle "35" → 0.35
            return max(0.01, min(0.99, val))
    except (requests.RequestException, KeyError, ValueError, IndexError):
        pass
    return None


# ── Main engine class ─────────────────────────────────────────────

class SentimentEngine:
    """Orchestrates news fetch → sentiment analysis → probability estimation.

    Parameters
    ----------
    llm_provider : str or None
        If set ("openai" or "groq"), use LLM for probability estimation
        instead of VADER.  Falls back to VADER if API key is missing.
    sentiment_weight : float
        How much sentiment nudges the probability estimate (0-1).
    max_articles : int
        Maximum news articles to fetch per market.
    """

    def __init__(
        self,
        llm_provider: str | None = None,
        sentiment_weight: float = 0.30,
        max_articles: int = 8,
    ):
        self.llm_provider = llm_provider
        self.sentiment_weight = sentiment_weight
        self.max_articles = max_articles
        # Cache results to avoid re-fetching within same cycle
        self._cache: dict[str, SentimentResult] = {}

    def clear_cache(self):
        """Clear per-cycle cache."""
        self._cache.clear()

    def analyze(self, market: dict) -> SentimentResult:
        """Analyze a market: fetch news, compute sentiment, estimate probability."""
        market_id = market["id"]
        if market_id in self._cache:
            return self._cache[market_id]

        question = market["question"]
        yes_price = market["yes_price"]

        # 1. Extract keywords and fetch news
        keywords = extract_keywords(question)
        articles = fetch_news_rss(keywords, max_articles=self.max_articles)
        headlines = [a.title for a in articles]

        # 2. Estimate probability
        llm_used = False
        if not articles:
            # No news found — estimate equals market price (no edge)
            estimated = yes_price
            avg_sent = 0.0
        elif self.llm_provider:
            # Try LLM first
            llm_est = llm_estimate_probability(
                question, headlines, yes_price, provider=self.llm_provider
            )
            if llm_est is not None:
                estimated = llm_est
                avg_sent = 0.0  # LLM subsumes sentiment
                llm_used = True
            else:
                # Fallback to VADER
                scores = [vader_sentiment(h) for h in headlines]
                avg_sent = statistics.mean(scores) if scores else 0.0
                estimated = sentiment_to_probability(avg_sent, yes_price, self.sentiment_weight)
        else:
            # VADER mode
            scores = [vader_sentiment(h) for h in headlines]
            avg_sent = statistics.mean(scores) if scores else 0.0
            estimated = sentiment_to_probability(avg_sent, yes_price, self.sentiment_weight)

        result = SentimentResult(
            market_id=market_id,
            question=question,
            articles_found=len(articles),
            avg_sentiment=avg_sent,
            estimated_probability=estimated,
            market_price=yes_price,
            edge=estimated - yes_price,
            headlines=headlines[:5],
            llm_used=llm_used,
        )
        self._cache[market_id] = result
        return result

    def analyze_batch(self, markets: list[dict]) -> list[SentimentResult]:
        """Analyze multiple markets."""
        return [self.analyze(m) for m in markets]


# ── CLI quick test ────────────────────────────────────────────────

if __name__ == "__main__":
    from polymarket_bot.market_reader import fetch_markets
    from polymarket_bot.strategy import filter_volatile_markets

    print("Sentiment Engine — quick test")
    print("=" * 60)

    all_markets = fetch_markets(limit=100)
    markets = filter_volatile_markets(all_markets, min_price=0.10, max_price=0.90, min_volume=1_000)
    print(f"Fetched {len(all_markets)} markets, {len(markets)} tradeable\n")

    engine = SentimentEngine()
    for m in markets[:5]:
        result = engine.analyze(m)
        print(f"Market: {result.question[:60]}")
        print(f"  Price: {result.market_price:.2f}  |  Sentiment: {result.avg_sentiment:+.3f}  |  Est. prob: {result.estimated_probability:.2f}  |  Edge: {result.edge:+.3f}")
        print(f"  Articles: {result.articles_found}  |  LLM: {result.llm_used}")
        if result.headlines:
            print(f"  Top headline: {result.headlines[0][:80]}")
        print()
