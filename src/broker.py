"""
Broker layer for NEXUS-TRADE: paper-trading accounts, a pre-trade risk
check (RMS), positions and P&L.

The matching engine is the exchange. It matches whatever it is given and
never asks whether the trader can afford it. On a real market that check
belongs to the broker, before the order reaches the exchange. This module
plays that role:

  - each account starts with Rs 10,00,000 of virtual funds
  - products are intraday (MIS) with 20% margin, i.e. 5x leverage
  - short selling is allowed, as long as there is margin for it
  - every order is checked for margin, price band and open-order limits;
    a failed check gives a REJECTED order with the reason, as brokers show
"""

import itertools
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from order_matching_engine import MatchingEngine, Order

OPENING_BALANCE = 1_000_000.0
MARGIN_RATE     = 0.20          # 20% margin = 5x intraday leverage
PRICE_BAND      = 0.10          # limit price must be within ±10% of LTP
MAX_OPEN_ORDERS = 50
MAX_ACCOUNTS    = 5_000
ORDER_RATE      = (10, 1.0)     # at most 10 orders per 1.0 s per account


@dataclass
class Account:
    user_id:  str
    name:     str
    token:    str
    created:  float = field(default_factory=time.time)
    opening:  float = OPENING_BALANCE
    position: int   = 0          # signed: + long, - short
    avg_price: float = 0.0       # average price of the open position
    realized: float = 0.0
    orders:   deque = field(default_factory=lambda: deque(maxlen=300))
    trades:   deque = field(default_factory=lambda: deque(maxlen=300))
    fill_value: dict = field(default_factory=dict)   # order_id -> sum(qty * price)
    recent:   deque = field(default_factory=deque)   # timestamps, for rate limiting


class RejectedOrder(Exception):
    pass


def inr(amount: float) -> str:
    """Rupees with Indian digit grouping: 4410090.5 -> '₹44,10,090.50'."""
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}₹{','.join(groups + [tail])}.{frac}"


class Broker:
    def __init__(self, engine: MatchingEngine, symbol: str,
                 ltp: Callable[[], Optional[float]]):
        self.engine = engine
        self.symbol = symbol
        self.ltp = ltp
        self.by_token: dict[str, Account] = {}
        self.by_id: dict[str, Account] = {}
        self.owner: dict[str, Account] = {}       # order_id -> account
        self._acc_ids = itertools.count(1)
        self._ord_ids = itertools.count(1)
        engine.on_trade(self._on_trades)

    # ── accounts ────────────────────────────────────────────
    def create_account(self, name: str) -> Account:
        if len(self.by_id) >= MAX_ACCOUNTS:
            raise RejectedOrder("Too many accounts on this server, try later")
        n = next(self._acc_ids)
        acc = Account(user_id=f"NX{1000 + n}", name=name.strip()[:32] or "Trader",
                      token=secrets.token_urlsafe(24))
        self.by_token[acc.token] = acc
        self.by_id[acc.user_id] = acc
        return acc

    def get(self, token: Optional[str]) -> Optional[Account]:
        return self.by_token.get(token) if token else None

    def reset_account(self, acc: Account) -> None:
        for o in list(acc.orders):
            if o.status == 'OPEN':
                self.engine.cancel_order(self.symbol, o.order_id)
        for o in acc.orders:
            self.owner.pop(o.order_id, None)
        acc.position, acc.avg_price, acc.realized = 0, 0.0, 0.0
        acc.orders.clear()
        acc.trades.clear()
        acc.fill_value.clear()

    # ── margin maths ────────────────────────────────────────
    def _ref_price(self, fallback: Optional[float] = None) -> float:
        return self.ltp() or fallback or 0.0

    @staticmethod
    def _open_qty(acc: Account, side: str) -> int:
        return sum(o.quantity for o in acc.orders
                   if o.status == 'OPEN' and o.side == side)

    def _margin_for(self, position: int, open_buy: int, open_sell: int,
                    price: float) -> float:
        """Margin for the worst case: every open buy fills, or every open
        sell fills. A sell that closes a long position needs no new margin."""
        worst_long = position + open_buy
        worst_short = open_sell - position
        return max(worst_long, worst_short, 0) * price * MARGIN_RATE

    def funds(self, acc: Account) -> dict:
        ltp = self._ref_price(acc.avg_price)
        unrealized = acc.position * (ltp - acc.avg_price) if acc.position else 0.0
        used = self._margin_for(acc.position, self._open_qty(acc, 'BUY'),
                                self._open_qty(acc, 'SELL'), ltp)
        equity = acc.opening + acc.realized + unrealized
        return {
            "opening_balance": round(acc.opening, 2),
            "realized_pnl": round(acc.realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(acc.realized + unrealized, 2),
            "equity": round(equity, 2),
            "used_margin": round(used, 2),
            "available_margin": round(equity - used, 2),
            "margin_rate": MARGIN_RATE,
        }

    # ── orders ──────────────────────────────────────────────
    def _new_order_id(self) -> str:
        return f"{2610080000000 + next(self._ord_ids)}"

    def _check(self, acc: Account, side: str, order_type: str, qty: int,
               price: Optional[float]) -> None:
        now = time.monotonic()
        limit, window = ORDER_RATE
        while acc.recent and now - acc.recent[0] > window:
            acc.recent.popleft()
        if len(acc.recent) >= limit:
            raise RejectedOrder("Too many orders, slow down")
        acc.recent.append(now)

        if order_type == 'LIMIT':
            open_count = sum(1 for o in acc.orders if o.status == 'OPEN')
            if open_count >= MAX_OPEN_ORDERS:
                raise RejectedOrder(f"Maximum {MAX_OPEN_ORDERS} open orders per account")
            ref = self.ltp()
            if ref and abs(price - ref) > ref * PRICE_BAND:
                lo, hi = ref * (1 - PRICE_BAND), ref * (1 + PRICE_BAND)
                raise RejectedOrder(f"Price outside circuit limits "
                                    f"({lo:,.2f} to {hi:,.2f})")

        ltp = self._ref_price(price)
        open_buy, open_sell = self._open_qty(acc, 'BUY'), self._open_qty(acc, 'SELL')
        before = self._margin_for(acc.position, open_buy, open_sell, ltp)
        after = self._margin_for(acc.position,
                                 open_buy + (qty if side == 'BUY' else 0),
                                 open_sell + (qty if side == 'SELL' else 0), ltp)
        if after > before:
            available = self.funds(acc)["available_margin"]
            needed = after - before
            if needed > available:
                raise RejectedOrder(f"Insufficient funds. Required margin "
                                    f"{inr(needed)}, available {inr(max(available, 0))}")

    def place(self, acc: Account, side: str, order_type: str, qty: int,
              price: Optional[float]) -> Order:
        """Check the order, send it to the engine, and return it. A failed
        check returns a REJECTED order instead of raising."""
        order_id = self._new_order_id()
        order = Order(order_id, self.symbol, side,
                      price if order_type == 'LIMIT' else None, qty, order_type,
                      trader_id=acc.user_id)
        acc.orders.append(order)
        self.owner[order_id] = acc
        try:
            self._check(acc, side, order_type, qty, price)
            self.engine.submit_order(order)       # fills reach _on_trades
        except (RejectedOrder, ValueError) as exc:
            order.status, order.message = 'REJECTED', str(exc)
        return order

    def cancel(self, acc: Account, order_id: str) -> bool:
        if self.owner.get(order_id) is not acc:
            return False
        return self.engine.cancel_order(self.symbol, order_id)

    def avg_fill_price(self, acc: Account, order: Order) -> Optional[float]:
        if not order.filled_qty:
            return None
        return round(acc.fill_value.get(order.order_id, 0) / order.filled_qty, 2)

    # ── fills ───────────────────────────────────────────────
    def _on_trades(self, trades) -> None:
        for t in trades:
            for order_id, sign in ((t.buy_order_id, 1), (t.sell_order_id, -1)):
                acc = self.owner.get(order_id)
                if acc is None:
                    continue
                self._apply_fill(acc, sign, t.quantity, t.price)
                acc.fill_value[order_id] = acc.fill_value.get(order_id, 0) + t.quantity * t.price
                acc.trades.append({
                    "trade_id": t.trade_id, "order_id": order_id,
                    "side": "BUY" if sign > 0 else "SELL",
                    "quantity": t.quantity, "price": t.price, "timestamp": t.timestamp,
                })

    @staticmethod
    def _apply_fill(acc: Account, sign: int, qty: int, price: float) -> None:
        """Update position, average price and realized P&L for one fill.
        sign is +1 for a buy, -1 for a sell."""
        pos = acc.position
        if pos == 0 or (pos > 0) == (sign > 0):
            # opening or adding: new weighted average price
            new_pos = pos + sign * qty
            acc.avg_price = (abs(pos) * acc.avg_price + qty * price) / abs(new_pos)
            acc.position = new_pos
            return
        # reducing, closing, or flipping the position
        closing = min(qty, abs(pos))
        direction = 1 if pos > 0 else -1
        acc.realized += closing * (price - acc.avg_price) * direction
        acc.position = pos + sign * qty
        if acc.position == 0:
            acc.avg_price = 0.0
        elif (acc.position > 0) != (pos > 0):
            acc.avg_price = price          # flipped: the rest opens at this price
