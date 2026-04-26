"""
Market Data Layer – Polymarket API ingestion.

Fetches live market data from the public Gamma API (no auth required),
parses prices / volumes / metadata, and persists snapshots to CSV.
"""

import csv
import json
import os
import time
from datetime import datetime, timezone

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def fetch_markets(limit: int = 20, active: bool = True) -> list[dict]:
    """Return a list of parsed market dicts from the Gamma API.

    Supports pagination: if *limit* exceeds the API page size (100),
    multiple requests are made transparently.
    """
    page_size = min(limit, 100)
    collected: list[dict] = []
    offset = 0

    while len(collected) < limit:
        params = {
            "limit": page_size,
            "active": str(active).lower(),
            "closed": "false",
            "offset": offset,
        }
        resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
        resp.raise_for_status()
        raw_markets = resp.json()

        if not raw_markets:
            break

        collected.extend(_parse_market(m) for m in raw_markets if _is_valid(m))
        offset += len(raw_markets)

        if len(raw_markets) < page_size:
            break

    return collected[:limit]


def fetch_market_by_id(market_id: str) -> dict | None:
    """Fetch a single market by its condition ID / slug."""
    resp = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return _parse_market(resp.json())


def _is_valid(raw: dict) -> bool:
    """Filter out markets without usable price data."""
    try:
        prices = json.loads(raw.get("outcomePrices", "[]"))
        return len(prices) >= 2
    except (json.JSONDecodeError, TypeError):
        return False


def _parse_market(raw: dict) -> dict:
    """Extract the fields we care about from a raw Gamma market object."""
    outcomes = json.loads(raw.get("outcomes", '["Yes","No"]'))
    prices = json.loads(raw.get("outcomePrices", "[0,0]"))
    yes_price = float(prices[0]) if len(prices) > 0 else 0.0
    no_price = float(prices[1]) if len(prices) > 1 else 0.0
    return {
        "id": raw.get("id", raw.get("conditionId", "")),
        "question": raw.get("question", "N/A"),
        "slug": raw.get("slug", ""),
        "yes_price": yes_price,
        "no_price": no_price,
        "volume": float(raw.get("volume", 0) or 0),
        "liquidity": float(raw.get("liquidity", 0) or 0),
        "active": raw.get("active", False),
        "closed": raw.get("closed", False),
        "end_date": raw.get("endDate", ""),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def save_snapshot_csv(markets: list[dict], filename: str = "market_snapshot.csv"):
    """Append market data rows to a CSV file for later analysis."""
    _ensure_data_dir()
    filepath = os.path.join(DATA_DIR, filename)
    file_exists = os.path.isfile(filepath)
    fieldnames = [
        "fetched_at", "id", "question", "yes_price", "no_price",
        "volume", "liquidity", "active", "closed", "end_date",
    ]
    with open(filepath, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        for m in markets:
            writer.writerow(m)


def save_snapshot_json(markets: list[dict], filename: str = "market_snapshot.json"):
    """Append market data to a JSON-lines file."""
    _ensure_data_dir()
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, "a", encoding="utf-8") as fh:
        for m in markets:
            fh.write(json.dumps(m) + "\n")


if __name__ == "__main__":
    print("Fetching markets from Polymarket …")
    markets = fetch_markets(limit=10)
    for m in markets:
        print(f"  {m['question'][:60]:60s}  YES={m['yes_price']:.2f}  NO={m['no_price']:.2f}  vol={m['volume']:,.0f}")
    save_snapshot_csv(markets)
    save_snapshot_json(markets)
    print(f"\nSaved {len(markets)} markets to {DATA_DIR}/")
