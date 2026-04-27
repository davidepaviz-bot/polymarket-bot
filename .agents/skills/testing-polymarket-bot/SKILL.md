# Testing the Polymarket Paper Trading Bot

## Overview
CLI-based Python paper trading bot for Polymarket prediction markets. No browser UI — all testing is done via shell commands. No recording needed.

## Devin Secrets Needed
- `GH_TOKEN` — GitHub token for pushing code and creating PRs
- `OPENAI_API_KEY` (optional) — only needed to test LLM sentiment mode
- `GROQ_API_KEY` (optional) — only needed to test Groq LLM sentiment mode

## Environment Setup
```bash
cd /home/ubuntu/repos/polymarket-bot
pip install -r requirements.txt  # installs requests, pandas, vaderSentiment
```

## Running the Bot
```bash
# Default: mean-reversion strategy, 20 cycles, 30s interval
python -m polymarket_bot

# Quick test (short cycles)
python -m polymarket_bot --cycles 5 --interval 3

# Probabilistic edge strategy
python -m polymarket_bot --strategy prob --cycles 10 --interval 5

# Sentiment strategy (VADER — no API key needed)
python -m polymarket_bot --strategy sentiment --cycles 10 --interval 5

# Sentiment with LLM (requires OPENAI_API_KEY or GROQ_API_KEY)
python -m polymarket_bot --strategy sentiment --llm openai --cycles 10
python -m polymarket_bot --strategy sentiment --llm groq --cycles 10

# Expanded market pool
python -m polymarket_bot --markets 200   # default
python -m polymarket_bot --markets 500   # maximum diversity
```

## Testing Standalone Modules
```bash
# Test market reader (API connectivity)
python -m polymarket_bot.market_reader

# Test sentiment engine (news fetch + VADER)
python -m polymarket_bot.sentiment_engine
```

## Key CLI Flags
| Flag | Default | Description |
|---|---|---|
| `--cycles` | 20 | Number of trading cycles |
| `--interval` | 30 | Seconds between cycles |
| `--strategy` | `mean_reversion` | `mean_reversion`, `prob`, or `sentiment` |
| `--capital` | 200.0 | Initial capital in EUR |
| `--markets` | 200 | Markets to fetch (pagination auto) |
| `--llm` | None | LLM provider: `openai` or `groq` |

## What to Verify for Each Strategy

### mean_reversion
- Signal format: `Mean-reversion: YES X.XX < 0.4` or `> 0.6`
- Picks markets based purely on price thresholds
- BUY_YES when price < 0.40, BUY_NO when price > 0.60

### prob
- Signal format: `Prob-edge: est=X.XX, mkt=X.XX, edge=±X.XX`
- Generates probability estimates from price momentum + noise
- BUY_YES/NO when edge > 8%

### sentiment
- Signal format: `Sentiment(VADER): est=X.XX, mkt=X.XX, edge=±X.XX, sent=±X.XX, news=N`
- Must show `news=N` where N >= 1 (proves news was fetched)
- Without LLM API key, `--llm openai` should gracefully fall back to VADER
- Verify: header shows `Sentiment  : VADER` or `Sentiment  : LLM (provider)`

## Common Testing Assertions
1. **Pagination**: "Fetched 200 markets" (not 100) — proves multi-page API works
2. **Tradeable count**: Should be 20-30 with default filters (price 0.10-0.90, vol >= 1K)
3. **Signals per cycle**: 7-20 depending on strategy and market conditions
4. **Fill price > mid**: Every trade should show fill > mid (spread applied)
5. **Exit triggers**: stop-loss (>= 5%), take-profit (>= 3%), max-hold (8 cycles), session-end
6. **Diversification**: No consecutive trades on the same market
7. **Output files**: `logs/trade_log.csv` and `logs/equity_curve.json` saved

## Known Testing Quirks
- **Take-profit rarely triggers** in short runs: the ~0.8% spread means price needs to move ~3.8% total for net 3% gain within 8 cycles. This is realistic.
- **BUY_NO signals are rare** with mean-reversion: price filter 0.10-0.90 + threshold > 0.60 leaves narrow window. Use `--strategy prob` or `--strategy sentiment` for more BUY_NO diversity.
- **Sentiment signals may cluster**: VADER tends to give similar scores for related markets (e.g., all FIFA markets get similar sports news). This is expected.
- **Google News RSS** might occasionally return 0 articles for niche topics, causing HOLD signals. Not a bug.
- **LLM fallback is silent**: when `--llm openai` is used without API key, signals show `Sentiment(VADER):` not `Sentiment(LLM):` — this is correct behavior, not a bug.
- **API rate limiting**: the Gamma API may occasionally return errors if hit too fast. The bot handles this with `try/except` and skips the cycle.
- **Short cycles produce losses**: with 3-5s intervals, spread friction dominates. Use 30s+ intervals for more realistic results.
