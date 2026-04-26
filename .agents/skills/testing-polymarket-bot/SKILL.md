# Testing the Polymarket Paper Trading Bot

## Overview
This is a CLI-only Python application (no browser GUI). All testing is done via shell commands. No screen recording is needed — collect command outputs as text evidence.

## Prerequisites
- Python 3.10+
- `pip install -r requirements.txt` (requests, pandas)
- No API keys or secrets needed — Polymarket Gamma API is public
- No auth required

## How to Run
```bash
# Mean-reversion strategy (default), 15 cycles, 3s interval
python -m polymarket_bot --cycles 15 --interval 3

# Prob-edge strategy
python -m polymarket_bot --cycles 15 --interval 3 --strategy prob

# Longer run for more exit triggers
python -m polymarket_bot --cycles 30 --interval 3

# Expanded market pool (500 markets, 5 API pages)
python -m polymarket_bot --cycles 10 --interval 3 --markets 500
```

## Key CLI Arguments
- `--cycles N` — number of fetch-evaluate-trade loops
- `--interval N` — seconds between cycles (3s is good for testing)
- `--strategy mean_reversion|prob` — which strategy to use
- `--capital N` — initial paper capital (default 200)
- `--markets N` — number of markets to fetch per cycle (default 200, supports pagination up to 500+)

## What to Verify

### 1. Market Fetch & Pagination (v3.1)
- Output shows "Fetched X markets" where X matches `--markets` value
- With `--markets 200`: should show "Fetched 200 markets" (2 API pages), ~29 tradeable
- With `--markets 500`: should show "Fetched 500 markets" (5 API pages), ~88 tradeable
- If pagination is broken, only 100 markets will appear (single page)
- The Gamma API supports `offset` parameter for pagination, page size is 100

### 2. Spread & Fill Price (v3.0)
- Every OPEN trade line shows `mid=X fill=X spread=X`
- For YES trades: fill > mid (spread adds cost)
- For NO trades: fill is in NO price range (~0.35-0.55), not YES range
- Spread is ~0.004 (0.4%) for high-volume markets

### 3. Exit Logic (v3.0)
- **Stop-loss**: triggers when unrealized <= -5% (look for "Stop loss!" in output)
- **Max-hold**: triggers at exactly hold cycle 8 (look for "Max hold (8)")
- **Session-end**: "Closing remaining position at market..." at final cycle
- **Take-profit**: triggers when unrealized >= +3% (look for "Take profit!") — NOTE: this rarely triggers in short runs because the spread friction (~0.8%) makes 3% gains hard to achieve within 8 cycles

### 4. Filters (v3.1)
- Price range: 0.10-0.90 (widened from 0.15-0.85 in v3.0)
- Volume threshold: 1,000 (lowered from 10,000 in v3.0)
- Markets with YES price 0.10-0.15 should appear in signals (they were excluded in v3.0)

### 5. No Event Resolution (v3.0)
- v3.0 removed simulated resolutions — verify zero occurrences of "Event resolved" or "resolution" in output
- All P&L should come from price movements, not random binary payouts

### 6. Output Files
- `logs/trade_log.csv` — should have rows with non-zero pnl values
- `logs/equity_curve.json` — should have multiple distinct equity values
- `data/market_snapshot.csv` — market data snapshots

## Known Testing Quirks

- **BUY_NO signals are rare with mean-reversion** because the volatility filter (0.10-0.90) and the BUY_NO threshold (YES > 0.60) leave a narrower window. Use `--strategy prob` to more reliably trigger BUY_NO trades.
- **Take-profit rarely fires** in short test runs. The micro-price simulation creates small movements around the API mid, and the ~0.8% spread friction means the price needs to move ~3.8% total for a 3% net gain. Running 50+ cycles might help.
- **Diversification**: the bot avoids trading the same market twice in a row (`_recently_traded` list). With many cycles, you should see trades across different markets.
- **Results are stochastic**: the micro-price simulation uses random noise, so exact P&L values will differ between runs. Focus on structural assertions (fill > mid, exit triggers fire at correct thresholds) rather than exact numbers.
- **First cycle with --markets 500 is slower** (~3-4s extra) due to 5 API requests. This is expected.

## Assertion Patterns (grep commands)
```bash
# Check v3.0 header
grep 'v3.0 (Short-Term)' output.txt

# Check fetched market count (pagination)
grep 'Fetched 200 markets' output.txt  # or 500

# Check fill prices shown
grep 'fill=' output.txt

# Check no event resolution
grep -c 'resolution' output.txt  # should be 0

# Check exit triggers
grep 'Stop loss' output.txt
grep 'Max hold' output.txt
grep 'Take profit' output.txt
grep 'Closing remaining position' output.txt

# Check BUY_NO
grep 'BUY_NO' output.txt

# Check signal count per cycle
grep 'signals' output.txt
```

## Devin Secrets Needed
None — the Polymarket Gamma API is public and requires no authentication.
