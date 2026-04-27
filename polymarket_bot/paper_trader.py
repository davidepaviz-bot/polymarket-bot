"""
Paper Trading System – simulated execution, position tracking & risk management.

Features:
  • Simulated BUY / SELL on binary YES/NO outcomes (no real money).
  • Single-position-at-a-time policy (risk rule).
  • Stop-loss logic: auto-close if price diverges beyond threshold.
  • Equity curve and full trade log.
"""

import csv
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from polymarket_bot.strategy import Side, Signal

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")


@dataclass
class Position:
    market_id: str
    question: str
    side: str          # "YES" or "NO"
    entry_price: float
    size: float        # amount in € risked
    opened_at: str = ""


@dataclass
class TradeRecord:
    trade_id: int
    market_id: str
    question: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    balance_after: float
    opened_at: str
    closed_at: str


class PaperTrader:
    """Core paper-trading engine with risk management."""

    def __init__(
        self,
        initial_capital: float = 200.0,
        max_position_pct: float = 0.15,
        stop_loss_pct: float = 0.30,
    ):
        self.initial_capital = initial_capital
        self.balance = initial_capital
        self.position: Optional[Position] = None
        self.trade_log: list[TradeRecord] = []
        self.equity_curve: list[dict] = [
            {"timestamp": datetime.now(timezone.utc).isoformat(), "equity": initial_capital}
        ]
        self.trade_counter = 0

        # Risk parameters
        self.max_position_pct = max_position_pct  # max 15% of capital per trade
        self.stop_loss_pct = stop_loss_pct         # close if price moves 30% against us

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def has_open_position(self) -> bool:
        return self.position is not None

    def process_signal(self, signal: Signal, size_override: float | None = None) -> Optional[TradeRecord]:
        """
        Given a strategy signal, decide whether to open or skip.
        Returns a TradeRecord only when a position is *closed* (for logging).
        size_override: if set, use this as the position size instead of default %.
        """
        if signal.side == Side.HOLD:
            return None

        if self.has_open_position:
            return None  # risk rule: max 1 position at a time

        return self._open_position(signal, size_override=size_override)

    def check_stop_loss(self, current_yes_price: float) -> Optional[TradeRecord]:
        """Close the open position if the stop-loss threshold is breached."""
        if not self.has_open_position:
            return None

        pos = self.position
        if pos.side == "YES":
            loss_ratio = (pos.entry_price - current_yes_price) / pos.entry_price
        else:
            current_no_price = 1.0 - current_yes_price
            loss_ratio = (pos.entry_price - current_no_price) / pos.entry_price

        if loss_ratio >= self.stop_loss_pct:
            return self._close_position(
                exit_price=current_yes_price if pos.side == "YES" else 1.0 - current_yes_price,
                reason="stop-loss",
            )
        return None

    def close_position_at(self, exit_yes_price: float, reason: str = "manual") -> Optional[TradeRecord]:
        """Explicitly close the current position at a given YES price."""
        if not self.has_open_position:
            return None
        pos = self.position
        exit_price = exit_yes_price if pos.side == "YES" else 1.0 - exit_yes_price
        return self._close_position(exit_price, reason)

    def simulate_resolution(self, outcome_is_yes: bool) -> Optional[TradeRecord]:
        """
        Simulate market resolution.  If outcome matches our side we get
        $1 per share; otherwise $0.
        """
        if not self.has_open_position:
            return None
        pos = self.position
        if (pos.side == "YES" and outcome_is_yes) or (pos.side == "NO" and not outcome_is_yes):
            exit_price = 1.0
        else:
            exit_price = 0.0
        return self._close_position(exit_price, reason="resolution")

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        total_trades = len(self.trade_log)
        wins = sum(1 for t in self.trade_log if t.pnl > 0)
        losses = sum(1 for t in self.trade_log if t.pnl < 0)
        total_pnl = sum(t.pnl for t in self.trade_log)
        return {
            "balance": round(self.balance, 2),
            "initial_capital": self.initial_capital,
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total_trades * 100, 1) if total_trades else 0.0,
            "open_position": self.position is not None,
        }

    def print_summary(self):
        s = self.summary()
        print("\n" + "=" * 60)
        print("  PAPER TRADING SUMMARY")
        print("=" * 60)
        print(f"  Initial capital : €{s['initial_capital']:.2f}")
        print(f"  Current balance : €{s['balance']:.2f}")
        print(f"  Total PnL       : €{s['total_pnl']:.2f}")
        print(f"  Total trades    : {s['total_trades']}")
        print(f"  Wins / Losses   : {s['wins']} / {s['losses']}")
        print(f"  Win rate        : {s['win_rate']}%")
        print(f"  Open position   : {'Yes' if s['open_position'] else 'No'}")
        print("=" * 60 + "\n")

    def save_trade_log(self, filename: str = "trade_log.csv"):
        os.makedirs(LOGS_DIR, exist_ok=True)
        filepath = os.path.join(LOGS_DIR, filename)
        if not self.trade_log:
            return
        fieldnames = list(asdict(self.trade_log[0]).keys())
        with open(filepath, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for t in self.trade_log:
                writer.writerow(asdict(t))
        print(f"Trade log saved → {filepath}")

    def save_equity_curve(self, filename: str = "equity_curve.json"):
        os.makedirs(LOGS_DIR, exist_ok=True)
        filepath = os.path.join(LOGS_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(self.equity_curve, fh, indent=2)
        print(f"Equity curve saved → {filepath}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open_position(self, signal: Signal, size_override: float | None = None) -> None:
        size = size_override if size_override is not None else round(self.balance * self.max_position_pct, 2)
        if size < 0.01:
            return None

        side = "YES" if signal.side == Side.BUY_YES else "NO"
        entry_price = signal.yes_price if side == "YES" else signal.no_price

        self.position = Position(
            market_id=signal.market_id,
            question=signal.question,
            side=side,
            entry_price=entry_price,
            size=size,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        self.balance -= size
        print(f"  ▶ OPEN {side} | {signal.question[:50]} | price={entry_price:.2f} | size=€{size:.2f}")
        return None  # trade record created on close

    def _close_position(self, exit_price: float, reason: str) -> TradeRecord:
        pos = self.position
        shares = pos.size / pos.entry_price
        payout = shares * exit_price
        pnl = round(payout - pos.size, 2)
        self.balance = round(self.balance + payout, 2)

        self.trade_counter += 1
        record = TradeRecord(
            trade_id=self.trade_counter,
            market_id=pos.market_id,
            question=pos.question,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            size=pos.size,
            pnl=pnl,
            balance_after=self.balance,
            opened_at=pos.opened_at,
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.trade_log.append(record)
        self.equity_curve.append({
            "timestamp": record.closed_at,
            "equity": self.balance,
        })
        self.position = None
        symbol = "✓" if pnl >= 0 else "✗"
        print(f"  {symbol} CLOSE {pos.side} ({reason}) | pnl=€{pnl:+.2f} | balance=€{self.balance:.2f}")
        return record
