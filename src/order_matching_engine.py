"""
NEXUS-TRADE order matching engine.
Mahendra Meena, IIIT Gwalior.

Run: python src/order_matching_engine.py

Data structures:
  - SortedDict (balanced tree): price levels, O(log n)
  - doubly linked list: FIFO queue at each price level, O(1)
  - dict: order_id -> node, for O(1) cancel
  - deque(maxlen=1000): recent trades
"""

import time
import uuid
import statistics
import random
import numpy as np
from dataclasses import dataclass, field
from collections import deque
from sortedcontainers import SortedDict
from typing import Optional


# ══════════════════════════════════════════════════════════════
# 1.  DATA CLASSES
# ══════════════════════════════════════════════════════════════

@dataclass
class Order:
    """Single order submitted by a trader."""
    order_id:   str
    symbol:     str
    side:       str          # 'BUY' or 'SELL'
    price:      float        # ignored for MARKET orders
    quantity:   int
    order_type: str = 'LIMIT'   # 'LIMIT' or 'MARKET'
    trader_id:  Optional[str] = None   # used for self-trade prevention (STP)
    timestamp:  float = field(default_factory=time.time)
    # Lifecycle. `quantity` is the open (unfilled) quantity and shrinks as
    # the order fills; `original_qty` keeps what was asked for.
    original_qty: int = 0
    filled_qty:   int = 0
    status:       str = 'NEW'    # NEW, OPEN, COMPLETE, CANCELLED, REJECTED
    message:      str = ''       # reason for CANCELLED / REJECTED

    def __post_init__(self):
        if self.side not in ('BUY', 'SELL'):
            raise ValueError(f"Order side must be BUY or SELL, got {self.side}")
        if self.order_type not in ('LIMIT', 'MARKET'):
            raise ValueError(f"Order type must be LIMIT or MARKET, got {self.order_type}")
        if self.quantity <= 0:
            raise ValueError(f"Order quantity must be > 0, got {self.quantity}")
        if self.order_type == 'LIMIT' and (self.price is None or self.price <= 0):
            raise ValueError(f"LIMIT order price must be > 0, got {self.price}")
        if not self.original_qty:
            self.original_qty = self.quantity

    def __repr__(self):
        price_str = f"{self.price:.2f}" if self.price is not None else "MKT"
        return (f"Order({self.order_id} | {self.side} {self.quantity} "
                f"@ {price_str} [{self.order_type}])")


@dataclass
class Trade:
    """A matched trade between a buyer and a seller."""
    trade_id:      str
    symbol:        str
    buy_order_id:  str
    sell_order_id: str
    price:         float
    quantity:      int
    timestamp:     float = field(default_factory=time.time)

    def __repr__(self):
        return (f"Trade({self.trade_id} | {self.quantity} @ "
                f"{self.price:.2f})")


# ══════════════════════════════════════════════════════════════
# 2.  PRICE LEVEL  (doubly linked list)
# ══════════════════════════════════════════════════════════════

class _Node:
    """One node in the doubly linked list holding an Order."""
    __slots__ = ('order', 'prev', 'next')

    def __init__(self, order: 'Order'):
        self.order = order
        self.prev: Optional['_Node'] = None
        self.next: Optional['_Node'] = None


class PriceLevel:
    """
    All orders at one price, in arrival order (FIFO).

    A doubly linked list rather than a deque: removing an order from the
    middle of a deque means rebuilding it (O(n)). Here OrderBook.order_map
    keeps a reference to each order's node, so a cancel unlinks it in O(1)
    wherever it sits in the queue.
    """
    def __init__(self, price: float):
        self.price     = price
        self.head: Optional[_Node] = None   # oldest (matched first)
        self.tail: Optional[_Node] = None   # newest
        self._count    = 0
        self.total_qty = 0

    def add_order(self, order: 'Order') -> _Node:
        """O(1) append at tail. Returns the node so OrderBook can store it
        in order_map for cancellation."""
        node = _Node(order)
        if self.tail is None:
            self.head = self.tail = node
        else:
            node.prev = self.tail
            self.tail.next = node
            self.tail = node
        self._count += 1
        self.total_qty += order.quantity
        return node

    def peek_front(self) -> Optional['Order']:
        return self.head.order if self.head else None

    def remove_front(self) -> Optional['Order']:
        """O(1) pop from head."""
        if self.head is None:
            return None
        node = self.head
        self._unlink(node)
        return node.order

    def remove_node(self, node: _Node) -> None:
        """O(1) removal of any node. This is what makes cancel O(1)."""
        self._unlink(node)

    def _unlink(self, node: _Node) -> None:
        if node.prev is not None:
            node.prev.next = node.next
        else:
            self.head = node.next
        if node.next is not None:
            node.next.prev = node.prev
        else:
            self.tail = node.prev
        node.prev = node.next = None
        self._count -= 1
        self.total_qty -= node.order.quantity

    def is_empty(self) -> bool:
        return self.head is None

    def order_count(self) -> int:
        return self._count

    def __iter__(self):
        node = self.head
        while node is not None:
            yield node.order
            node = node.next


# ══════════════════════════════════════════════════════════════
# 3.  ORDER BOOK
# ══════════════════════════════════════════════════════════════

class OrderBook:
    """
    Two-sided limit order book for one symbol.

      bids      SortedDict, highest price first        O(log n)
      asks      SortedDict, lowest price first         O(log n)
      order_map dict, order_id -> (side, price, node)  O(1) cancel

    The dict alone only finds the price level in O(1). Storing the linked
    list node as well lets PriceLevel.remove_node unlink the order in O(1),
    whatever the queue length or the order's position.
    """
    def __init__(self, symbol: str, tick_size: Optional[float] = None):
        self.symbol    = symbol
        self.tick_size = tick_size
        self.bids      = SortedDict(lambda p: -p)   # max-first
        self.asks      = SortedDict()               # min-first
        self.order_map: dict[str, tuple[str, float, _Node]] = {}

    # ── best prices ─────────────────────────────────────────
    def best_bid(self) -> Optional[float]:
        return self.bids.peekitem(0)[0] if self.bids else None

    def best_ask(self) -> Optional[float]:
        return self.asks.peekitem(0)[0] if self.asks else None

    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        return round(ba - bb, 2) if (bb and ba) else None

    def mid_price(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        return round((bb + ba) / 2, 2) if (bb and ba) else None

    def normalize_price(self, price: float) -> float:
        """Snap a limit price to a clean float key.

        Prices are dict keys, so 0.1 + 0.2 and 0.3 must not become two
        levels. With a tick size, the price must be a whole number of
        ticks (NSE equities use 0.05) or the order is rejected."""
        if self.tick_size is None:
            return round(price, 8)
        ticks = round(price / self.tick_size)
        if ticks <= 0 or abs(ticks * self.tick_size - price) > 1e-6:
            raise ValueError(f"Price must be a multiple of the tick size "
                             f"{self.tick_size}, got {price}")
        return round(ticks * self.tick_size, 8)

    # ── helpers ─────────────────────────────────────────────
    def _get_or_create_level(self, side: str, price: float) -> PriceLevel:
        book = self.bids if side == 'BUY' else self.asks
        if price not in book:
            book[price] = PriceLevel(price)
        return book[price]

    def _cleanup_level(self, side: str, price: float):
        book = self.bids if side == 'BUY' else self.asks
        if price in book and book[price].is_empty():
            del book[price]

    def add_passive(self, order: Order):
        """Rest the unfilled part of a limit order in the book."""
        level = self._get_or_create_level(order.side, order.price)
        node = level.add_order(order)
        self.order_map[order.order_id] = (order.side, order.price, node)

    def remove_filled(self, side: str, price: float, order_id: str) -> None:
        """Drop the order_map entry for an order that has been fully
        filled and already removed from its level."""
        self.order_map.pop(order_id, None)

    def cancel(self, order_id: str) -> bool:
        """O(1): the dict lookup returns the node, and the node is
        unlinked without scanning the level."""
        entry = self.order_map.pop(order_id, None)
        if entry is None:
            return False
        side, price, node = entry
        book = self.bids if side == 'BUY' else self.asks
        if price in book:
            book[price].remove_node(node)
            self._cleanup_level(side, price)
        node.order.status = 'CANCELLED'
        node.order.message = 'Cancelled by user'
        return True

    # ── display ─────────────────────────────────────────────
    def snapshot(self, depth: int = 5) -> str:
        lines = [f"\n{'═'*52}",
                 f"  Order Book: {self.symbol}",
                 f"{'─'*52}",
                 f"  {'PRICE':>10}  {'QTY':>8}  {'ORDERS':>6}  SIDE"]
        lines.append(f"{'─'*52}")

        ask_prices = list(self.asks.keys())[:depth]
        for p in reversed(ask_prices):
            lv = self.asks[p]
            bar = '█' * min(20, lv.total_qty // 10)
            lines.append(f"  \033[91m{p:>10.2f}  {lv.total_qty:>8}  "
                         f"{lv.order_count():>6}\033[0m  ASK  {bar}")

        sp = self.spread()
        mid = self.mid_price()
        lines.append(f"{'─'*52}")
        lines.append(f"  Spread: {sp}  ·  Mid: {mid}")
        lines.append(f"{'─'*52}")

        bid_prices = list(self.bids.keys())[:depth]
        for p in bid_prices:
            lv = self.bids[p]
            bar = '█' * min(20, lv.total_qty // 10)
            lines.append(f"  \033[92m{p:>10.2f}  {lv.total_qty:>8}  "
                         f"{lv.order_count():>6}\033[0m  BID  {bar}")
        lines.append(f"{'═'*52}\n")
        return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
# 4.  MATCHING ENGINE  (price-time priority)
# ══════════════════════════════════════════════════════════════

class MatchingEngine:
    """
    Receives orders, matches them, and emits trades.

    Price-time priority, the rule used by continuous limit order books:
      - better price matches first
      - at the same price, the earlier order matches first (FIFO)
    """
    def __init__(self):
        self.books:  dict[str, OrderBook] = {}
        self.trades: deque = deque(maxlen=1000)   # circular buffer
        self.callbacks = []           # observer pattern
        self._stats = {
            'orders_received': 0,
            'orders_matched':  0,
            'total_volume':    0,
            'total_latency_ns': 0,
        }

    def add_symbol(self, symbol: str, tick_size: Optional[float] = None,
                   verbose: bool = True):
        self.books[symbol] = OrderBook(symbol, tick_size)
        if verbose:
            print(f"  [Engine] Symbol added: {symbol}")

    def on_trade(self, callback):
        """Observer pattern: register a callback for every trade."""
        self.callbacks.append(callback)

    # ── main entry point ────────────────────────────────────
    def submit_order(self, order: Order) -> list[Trade]:
        """Match an order and return the trades it produced.

        Raises ValueError for an unknown symbol or an off-tick price; the
        order is then marked REJECTED and the book is untouched."""
        t0 = time.perf_counter_ns()

        if order.symbol not in self.books:
            order.status, order.message = 'REJECTED', f"Unknown symbol: {order.symbol}"
            raise ValueError(order.message)
        book = self.books[order.symbol]
        if order.order_type == 'LIMIT':
            try:
                order.price = book.normalize_price(order.price)
            except ValueError as exc:
                order.status, order.message = 'REJECTED', str(exc)
                raise

        self._stats['orders_received'] += 1
        trades = (self._match_market(book, order)
                  if order.order_type == 'MARKET'
                  else self._match_limit(book, order))

        # Record trades
        self.trades.extend(trades)
        self._stats['orders_matched']  += len(trades)
        self._stats['total_volume']    += sum(t.quantity for t in trades)
        self._stats['total_latency_ns'] += time.perf_counter_ns() - t0

        # Notify observers
        if trades:
            for cb in self.callbacks:
                cb(trades)

        return trades

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        if symbol not in self.books:
            return False
        return self.books[symbol].cancel(order_id)

    # ── limit order matching ─────────────────────────────────
    def _match_limit(self, book: OrderBook, order: Order, rest_unfilled: bool = True) -> list[Trade]:
        """Price-time priority matching loop.

        Walks the opposite side from the best price. Within a level, orders
        fill oldest first. Trades print at the resting order's price.

        Self-trade prevention (STP): a resting order with the same
        trader_id as the incoming order is skipped, however many of them
        there are, and matching carries on with the next order and the
        next price level. Exchanges enforce this (NSE STP, CME self-match
        prevention) because self-trades inflate volume and can be used for
        wash trading. Orders without a trader_id never trigger STP.

        rest_unfilled: True for LIMIT orders, so leftover quantity rests.
        False for MARKET orders (Immediate-or-Cancel), so it is dropped.
        A leftover LIMIT quantity that still crosses the book (only
        possible when the crossing orders are the trader's own) is
        cancelled instead of rested, so the book can never end up with
        best bid >= best ask.
        """
        trades    = []
        buy       = order.side == 'BUY'
        opposite  = book.asks if buy else book.bids
        remaining = order.quantity
        stp       = order.trader_id is not None
        idx       = 0      # index of the price level being matched

        while remaining > 0 and idx < len(opposite):
            price, level = opposite.peekitem(idx)
            if (buy and price > order.price) or (not buy and price < order.price):
                break      # no longer crosses

            node = level.head
            while node is not None and remaining > 0:
                resting = node.order
                nxt = node.next
                if stp and resting.trader_id == order.trader_id:
                    node = nxt          # skip own order, keep its place in the queue
                    continue

                fill = min(remaining, resting.quantity)
                trades.append(Trade(
                    trade_id      = str(uuid.uuid4())[:8].upper(),
                    symbol        = order.symbol,
                    buy_order_id  = order.order_id if buy else resting.order_id,
                    sell_order_id = resting.order_id if buy else order.order_id,
                    price         = price,          # passive side's price
                    quantity      = fill,
                ))
                remaining -= fill
                resting.filled_qty += fill

                if fill == resting.quantity:
                    # remove_node subtracts the order's open quantity from
                    # total_qty, so don't subtract it here as well.
                    level.remove_node(node)
                    book.order_map.pop(resting.order_id, None)
                    resting.quantity = 0
                    resting.status = 'COMPLETE'
                else:
                    resting.quantity -= fill
                    level.total_qty  -= fill
                node = nxt

            if level.is_empty():
                del opposite[price]      # next level slides into this index
            else:
                idx += 1                 # only own orders (or none) left here

        order.filled_qty += order.quantity - remaining
        order.quantity = remaining

        if remaining == 0:
            order.status = 'COMPLETE'
        elif not rest_unfilled:
            order.status = 'CANCELLED'
            order.message = ('No liquidity, order cancelled' if order.filled_qty == 0
                             else f'{remaining} unfilled, cancelled (IOC)')
        else:
            best = book.best_ask() if buy else book.best_bid()
            if best is not None and ((buy and best <= order.price) or
                                     (not buy and best >= order.price)):
                order.status = 'CANCELLED'
                order.message = (f'{remaining} cancelled by self-trade prevention '
                                 f'(would cross your own order)')
            else:
                book.add_passive(order)
                order.status = 'OPEN'
        return trades

    # ── market order matching ────────────────────────────────
    def _match_market(self, book: OrderBook, order: Order) -> list[Trade]:
        """Market order, Immediate-or-Cancel: fill against whatever is
        resting and drop the rest. There is no limit price to rest it at."""
        # The sentinel price is only for the price check during the walk.
        # order_type stays MARKET.
        original_price = order.price
        order.price = float('inf') if order.side == 'BUY' else 0.0
        try:
            trades = self._match_limit(book, order, rest_unfilled=False)
        finally:
            # Put the caller's price back once matching is done.
            order.price = original_price
        return trades

    # ── stats ────────────────────────────────────────────────
    def stats(self) -> dict:
        s = self._stats
        avg_lat = (s['total_latency_ns'] / max(s['orders_received'], 1)) / 1000
        return {
            'orders_received': s['orders_received'],
            'trades_matched':  s['orders_matched'],
            'total_volume':    s['total_volume'],
            'avg_latency_us':  round(avg_lat, 2),
        }


# ══════════════════════════════════════════════════════════════
# 5.  QUANT ANALYTICS
# ══════════════════════════════════════════════════════════════

class QuantAnalytics:
    """Rolling analytics over the most recent `window` trades (numpy)."""
    def __init__(self, window: int = 100):
        self.window  = window
        self.prices  = deque(maxlen=window)
        self.volumes = deque(maxlen=window)

    def update(self, trade: Trade):
        self.prices.append(trade.price)
        self.volumes.append(trade.quantity)

    def vwap(self) -> float:
        """Volume Weighted Average Price."""
        if not self.prices:
            return 0.0
        p = np.array(self.prices, dtype=float)
        v = np.array(self.volumes, dtype=float)
        return round(float(np.dot(p, v) / v.sum()), 2)

    def realized_vol(self) -> float:
        """
        Annualised realized volatility, in percent.

        std(log returns) * sqrt(252 * 390): each trade is treated as one
        1-minute bar, with 390 minutes per session and 252 sessions a year.
        """
        if len(self.prices) < 2:
            return 0.0
        p           = np.array(self.prices, dtype=float)
        log_returns = np.diff(np.log(p))
        annual_vol  = float(np.std(log_returns) * np.sqrt(252 * 390))
        return round(annual_vol * 100, 2)   # in percent

    def z_score(self) -> float:
        """Z-score of the latest price against the rolling mean.
        Returns 0 until there are at least 10 trades."""
        if len(self.prices) < 10:
            return 0.0
        p = np.array(self.prices, dtype=float)
        return round(float((p[-1] - p.mean()) / (p.std() + 1e-9)), 3)

    def order_imbalance(self, book: OrderBook) -> float:
        """
        (BidQty - AskQty) / (BidQty + AskQty)
        at the best bid and best ask.
        Range: -1.0 (all asks) to +1.0 (all bids). Positive means more
        size on the bid."""
        bb, ba = book.best_bid(), book.best_ask()
        if not bb or not ba:
            return 0.0
        bid_qty = book.bids[bb].total_qty
        ask_qty = book.asks[ba].total_qty
        return round((bid_qty - ask_qty) / (bid_qty + ask_qty + 1e-9), 3)

    def summary(self, book: OrderBook) -> str:
        return (f"  VWAP: {self.vwap():<10.2f}"
                f"  RealizedVol: {self.realized_vol():<7.2f}%"
                f"  Z-score: {self.z_score():<8.3f}"
                f"  Imbalance: {self.order_imbalance(book):.3f}")


# ══════════════════════════════════════════════════════════════
# 6.  BENCHMARK
# ══════════════════════════════════════════════════════════════

def run_benchmark():
    print("\n" + "═"*52)
    print("  BENCHMARK: 10,000 orders")
    print("═"*52)

    engine = MatchingEngine()
    engine.add_symbol('NIFTY50')

    BASE_PRICE = 24_500.0
    latencies  = []

    # Seed order book with resting orders first
    for i in range(50):
        bid_price = round(BASE_PRICE - (i + 1) * 0.5, 2)
        ask_price = round(BASE_PRICE + (i + 1) * 0.5, 2)
        engine.submit_order(Order(
            order_id=f"SEED-B-{i:04d}", symbol='NIFTY50',
            side='BUY',  price=bid_price, quantity=100 + i * 5
        ))
        engine.submit_order(Order(
            order_id=f"SEED-A-{i:04d}", symbol='NIFTY50',
            side='SELL', price=ask_price, quantity=100 + i * 5
        ))

    # Submit 10,000 random orders and time each one. GC stays enabled, so
    # the numbers include GC pauses. P99.9 is printed next to P50 because
    # the gap between them is mostly GC (see run_gc_comparison()).
    for i in range(10_000):
        side  = 'BUY' if random.random() > 0.5 else 'SELL'
        price = round(BASE_PRICE + random.uniform(-5, 5), 2)
        qty   = random.randint(10, 200)
        otype = 'MARKET' if random.random() < 0.3 else 'LIMIT'

        order = Order(
            order_id=f"ORD-{i:06d}", symbol='NIFTY50',
            side=side, price=price, quantity=qty, order_type=otype
        )
        t0 = time.perf_counter_ns()
        engine.submit_order(order)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000)   # to microseconds

    s = engine.stats()
    lat_sorted = sorted(latencies)
    p50  = statistics.median(latencies)
    p99  = lat_sorted[int(len(lat_sorted) * 0.99)]
    p999 = lat_sorted[int(len(lat_sorted) * 0.999)]
    p_max = lat_sorted[-1]
    tput = 10_000 / (sum(latencies) / 1_000_000)

    print(f"\n  Orders processed  : {s['orders_received']:,}")
    print(f"  Trades matched    : {s['trades_matched']:,}")
    print(f"  Total volume      : {s['total_volume']:,}")
    print(f"\n  Latency (µs):")
    print(f"    Median (P50)    : {p50:.1f} µs")
    print(f"    P99             : {p99:.1f} µs")
    print(f"    P99.9           : {p999:.1f} µs")
    print(f"    Max             : {p_max:.1f} µs")

    tail_ratio = p999 / p50 if p50 > 0 else 0
    if tail_ratio > 20:
        print(f"\n  ⚠ Tail latency note: P99.9 is {tail_ratio:.0f}x the median.")
        print(f"    This gap is most often explained by Python's cyclic")
        print(f"    garbage collector pausing the interpreter mid-benchmark.")
        print(f"    run_gc_comparison() measures the effect on this machine.")

    print(f"\n  Throughput        : {tput:,.0f} orders/sec")
    print("═"*52)
    return engine


def run_gc_comparison(n: int = 10_000):
    """Run the same workload with the garbage collector on and off, to
    see how much of the P99.9 latency comes from GC pauses on this machine.

    Garbage-collected runtimes (Python, Java, Go) all show occasional
    stalls of a few hundred microseconds. The usual mitigations are
    gc.disable() with manual collection at safe points, gc.freeze() after
    warm-up, or moving the hot path to C++ or Rust.
    """
    import gc

    def _run(gc_enabled: bool):
        if gc_enabled:
            gc.enable()
        else:
            gc.disable()
        engine = MatchingEngine()
        engine.add_symbol('GCTEST')
        BASE = 24_500.0
        for i in range(50):
            engine.submit_order(Order(f"SB{i}", 'GCTEST', 'BUY', round(BASE - (i+1)*0.5, 2), 100))
            engine.submit_order(Order(f"SA{i}", 'GCTEST', 'SELL', round(BASE + (i+1)*0.5, 2), 100))
        latencies = []
        for i in range(n):
            side = 'BUY' if random.random() > 0.5 else 'SELL'
            price = round(BASE + random.uniform(-5, 5), 2)
            qty = random.randint(10, 200)
            otype = 'MARKET' if random.random() < 0.3 else 'LIMIT'
            order = Order(f"O{i}", 'GCTEST', side, price, qty, otype)
            t0 = time.perf_counter_ns()
            engine.submit_order(order)
            t1 = time.perf_counter_ns()
            latencies.append((t1 - t0) / 1_000)
        gc.enable()
        lat_sorted = sorted(latencies)
        return {
            "p50": statistics.median(latencies),
            "p99": lat_sorted[int(len(lat_sorted) * 0.99)],
            "p999": lat_sorted[int(len(lat_sorted) * 0.999)],
            "max": lat_sorted[-1],
        }

    print("\n" + "═" * 52)
    print("  GC IMPACT ON TAIL LATENCY")
    print("═" * 52)
    random.seed(42)
    with_gc = _run(gc_enabled=True)
    random.seed(42)
    without_gc = _run(gc_enabled=False)

    print(f"\n  {'Metric':<10}{'GC on':>12}{'GC off':>12}{'Delta':>12}")
    for key in ("p50", "p99", "p999", "max"):
        delta = with_gc[key] - without_gc[key]
        print(f"  {key:<10}{with_gc[key]:>10.1f}µs{without_gc[key]:>10.1f}µs{delta:>+10.1f}µs")
    print("═" * 52)
    return with_gc, without_gc


# ══════════════════════════════════════════════════════════════
# 7.  DEMO
# ══════════════════════════════════════════════════════════════

def run_demo():
    print("\n" + "═"*52)
    print("  NEXUS-TRADE ORDER MATCHING ENGINE: DEMO")
    print("  By Mahendra Meena | IIIT Gwalior")
    print("═"*52)

    engine    = MatchingEngine()
    analytics = QuantAnalytics(window=50)

    engine.add_symbol('NIFTY50')
    book = engine.books['NIFTY50']

    # Register trade callback (observer pattern)
    trade_count = [0]
    def on_trade_executed(trades):
        for t in trades:
            trade_count[0] += 1
            analytics.update(t)
            print(f"  TRADE #{trade_count[0]:03d} | "
                  f"{t.quantity} @ {t.price:.2f} | ID: {t.trade_id}")

    engine.on_trade(on_trade_executed)

    # Seed the book
    BASE = 24_500.0
    print("\n  [1] Seeding order book...")
    for i in range(1, 6):
        engine.submit_order(Order(f"BID-{i}", 'NIFTY50', 'BUY',
                                  round(BASE - i * 0.5, 2), 100 + i * 20))
        engine.submit_order(Order(f"ASK-{i}", 'NIFTY50', 'SELL',
                                  round(BASE + i * 0.5, 2), 80 + i * 15))

    print(book.snapshot())
    print(f"  Spread: {book.spread()} | Mid: {book.mid_price()}")

    # Cross the spread
    print("\n  [2] Placing aggressive BUY limit order (crosses spread)...")
    engine.submit_order(Order('AGG-BUY-1', 'NIFTY50', 'BUY',
                               24_501.50, 200, 'LIMIT'))
    print(book.snapshot())

    print("\n  [3] Placing MARKET SELL order (60 qty)...")
    engine.submit_order(Order('MKT-SELL-1', 'NIFTY50', 'SELL',
                               0, 60, 'MARKET'))
    print(book.snapshot())

    # Cancel
    print("\n  [4] Cancelling BID-3...")
    cancelled = engine.cancel_order('NIFTY50', 'BID-3')
    print(f"  Cancel result: {'ok' if cancelled else 'not found'}")

    # Analytics
    print("\n  [5] Analytics")
    print(analytics.summary(book))

    # Stats
    print("\n  [6] Engine Stats:")
    for k, v in engine.stats().items():
        print(f"      {k:<22}: {v}")

    print("\n" + "═"*52)
    print("  Demo finished.")
    print("═"*52 + "\n")


# ══════════════════════════════════════════════════════════════
# 8.  ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    run_demo()
    input("\n  Press Enter to run 10,000-order benchmark...\n")
    run_benchmark()
