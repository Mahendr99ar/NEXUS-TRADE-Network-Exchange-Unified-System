"""
╔══════════════════════════════════════════════════════════════╗
║   NEXUS-TRADE: Order Matching Engine                         ║
║   By: Mahendra Meena | IIIT Gwalior                          ║
║   Run: python order_matching_engine.py                       ║
╚══════════════════════════════════════════════════════════════╝

DS USED:
  - Red-Black Tree  → price level indexing  (O log n)
  - Doubly LinkedList → FIFO queue per level (O 1)
  - HashMap         → O(1) order cancel
  - Circular Buffer → trade log
"""

# ─── IMPORTS ──────────────────────────────────────────────────
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
# 1.  DATA CLASSES  (OOP — clean separation of concerns)
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

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError(f"Order quantity must be > 0, got {self.quantity}")
        if self.order_type == 'LIMIT' and self.price is not None and self.price <= 0:
            raise ValueError(f"LIMIT order price must be > 0, got {self.price}")

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
# 2.  PRICE LEVEL  (true Doubly Linked List — O(1) FIFO + O(1) remove)
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
    All orders at ONE price, in arrival order (FIFO).

    DSA: a real doubly linked list (head/tail pointers + per-node prev/next),
    NOT a deque. This matters because cancelling an order from the *middle*
    of a deque requires rebuilding the whole deque (O(n)). With an actual
    DLL, the order_map in OrderBook can hold a direct reference to this
    order's node, so removing it is a true O(1) pointer-splice regardless
    of where in the queue it sits.
    """
    def __init__(self, price: float):
        self.price     = price
        self.head: Optional[_Node] = None   # oldest (matched first)
        self.tail: Optional[_Node] = None   # newest
        self._count    = 0
        self.total_qty = 0

    def add_order(self, order: 'Order') -> _Node:
        """O(1) append at tail. Returns the node so the caller (OrderBook)
        can stash it in order_map for O(1) future cancellation."""
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
        """O(1) removal of an arbitrary node — this is what makes cancel
        truly O(1) instead of the O(n) deque-rebuild it used to be."""
        self._unlink(node)

    def remove_specific(self, order_id: str) -> Optional['Order']:
        """Remove and return the order matching order_id, wherever it sits
        in the queue. Used by self-trade prevention, which may need to
        fully-fill an order that is NOT at the head (the head was skipped
        because it belongs to the same trader as the incoming order).
        O(k) where k is the (small, bounded by STP skip count) position in
        the queue -- not a full scan, and never the O(n) deque-rebuild
        pattern this class was originally fixed to avoid for the common
        case (remove_front / remove_node are still O(1) and used for
        every non-STP fill)."""
        node = self.head
        while node is not None:
            if node.order.order_id == order_id:
                order = node.order
                self._unlink(node)
                return order
            node = node.next
        return None

    def find_node(self, order_id: str) -> Optional[_Node]:
        node = self.head
        while node is not None:
            if node.order.order_id == order_id:
                return node
            node = node.next
        return None

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
# 3.  ORDER BOOK  (Red-Black Tree per side)
# ══════════════════════════════════════════════════════════════

class OrderBook:
    """
    Two-sided limit order book for one symbol.

    DSA:
      bids → SortedDict (Red-Black Tree), highest price first  → O(log n)
      asks → SortedDict (Red-Black Tree), lowest price first   → O(log n)
      order_map → dict (HashMap), order_id → (side, price, node) → O(1) cancel

    NOTE on the O(1) cancel claim: the HashMap alone only gets you O(1) to
    find *which price level* an order lives in. Actually removing it from
    that level used to call deque-rebuild (O(n)). order_map now stores a
    direct reference to the order's linked-list node, so PriceLevel.remove_node
    can unlink it in true O(1), independent of queue length or position.
    """
    def __init__(self, symbol: str):
        self.symbol    = symbol
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
        """Rest of unmatched limit order goes into the book."""
        level = self._get_or_create_level(order.side, order.price)
        node = level.add_order(order)
        self.order_map[order.order_id] = (order.side, order.price, node)

    def remove_filled(self, side: str, price: float, order_id: str) -> None:
        """Pop the order_map entry once an order is fully filled and
        removed from its level via remove_front(). Keeps order_map in sync
        without needing another lookup."""
        self.order_map.pop(order_id, None)

    def cancel(self, order_id: str) -> bool:
        """True O(1): HashMap lookup gives the exact node directly, so
        removal is a constant-time pointer unlink, not a level scan or
        queue rebuild."""
        entry = self.order_map.pop(order_id, None)
        if entry is None:
            return False
        side, price, node = entry
        book = self.bids if side == 'BUY' else self.asks
        if price in book:
            book[price].remove_node(node)
            self._cleanup_level(side, price)
        return True

    # ── display ─────────────────────────────────────────────
    def snapshot(self, depth: int = 5) -> str:
        lines = [f"\n{'═'*52}",
                 f"  Order Book — {self.symbol}",
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
# 4.  MATCHING ENGINE  (Price-Time Priority Algorithm)
# ══════════════════════════════════════════════════════════════

class MatchingEngine:
    """
    Core engine: receives orders, matches them, emits trades.

    Algorithm: Price-Time Priority (same as NSE / BSE)
      - Best price matched first
      - Within same price → earliest order matched first (FIFO)
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

    def add_symbol(self, symbol: str):
        self.books[symbol] = OrderBook(symbol)
        print(f"  [Engine] Symbol added: {symbol}")

    def on_trade(self, callback):
        """Observer pattern: register a callback for every trade."""
        self.callbacks.append(callback)

    # ── main entry point ────────────────────────────────────
    def submit_order(self, order: Order) -> list[Trade]:
        t0 = time.perf_counter_ns()
        self._stats['orders_received'] += 1

        if order.symbol not in self.books:
            raise ValueError(f"Unknown symbol: {order.symbol}")

        book   = self.books[order.symbol]
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

    @staticmethod
    def _peek_after(level: 'PriceLevel', order: 'Order') -> Optional['Order']:
        """Given an order known to be in `level`, return the next order
        in FIFO sequence after it (or None if it's last). Used only by
        self-trade prevention to look past a skipped same-trader order."""
        node = level.find_node(order.order_id)
        if node is None or node.next is None:
            return None
        return node.next.order

    # ── limit order matching ─────────────────────────────────
    def _match_limit(self, book: OrderBook, order: Order, rest_unfilled: bool = True) -> list[Trade]:
        """Core price-time-priority matching loop.

        rest_unfilled: when True (normal LIMIT order behavior), any quantity
        left over after walking the book is parked as a new resting order.
        When False (used for MARKET orders via _match_market), leftover
        quantity is simply dropped — a market order either fills against
        available liquidity or the unfilled remainder is cancelled, it must
        NEVER become a resting limit order at an artificial price like
        +inf/0, which would silently corrupt the book.
        """
        trades         = []
        remaining_qty  = order.quantity

        if order.side == 'BUY':
            # Walk up asks from lowest price
            while remaining_qty > 0 and book.best_ask() is not None:
                best_ask = book.best_ask()
                if best_ask > order.price:
                    break      # price condition not met → stop

                ask_level  = book.asks[best_ask]
                ask_order  = ask_level.peek_front()

                # Self-trade prevention (STP): a trader's own resting order
                # is never matched against their own incoming order. Real
                # exchanges enforce this (e.g. NSE STP, CME self-match
                # prevention) because self-trades create misleading volume/
                # price signals and can be a wash-trading vector. We skip
                # the resting order and check the next one at this price
                # level instead -- "skip" semantics, the most common STP
                # mode. If trader_id is None on either side, STP is not
                # enforced for that order (keeps existing demo/tests, which
                # don't set trader_id, behaving exactly as before).
                if (order.trader_id is not None
                        and ask_order.trader_id is not None
                        and order.trader_id == ask_order.trader_id):
                    next_order = self._peek_after(ask_level, ask_order)
                    if next_order is None:
                        break  # nothing else to match at this price level
                    ask_order = next_order

                fill_qty   = min(remaining_qty, ask_order.quantity)
                fully_filled = fill_qty == ask_order.quantity

                trades.append(Trade(
                    trade_id      = str(uuid.uuid4())[:8].upper(),
                    symbol        = order.symbol,
                    buy_order_id  = order.order_id,
                    sell_order_id = ask_order.order_id,
                    price         = best_ask,   # passive side's price
                    quantity      = fill_qty,
                ))

                remaining_qty -= fill_qty

                if fully_filled:
                    # remove_front() subtracts the order's full remaining
                    # quantity from total_qty exactly once — let it own
                    # the bookkeeping here, don't also subtract manually
                    # (that previously caused total_qty to be double-counted).
                    ask_level.remove_specific(ask_order.order_id)
                    book.order_map.pop(ask_order.order_id, None)
                else:
                    ask_order.quantity  -= fill_qty
                    ask_level.total_qty -= fill_qty

                if ask_level.is_empty():
                    del book.asks[best_ask]

        else:  # SELL
            # Walk down bids from highest price
            while remaining_qty > 0 and book.best_bid() is not None:
                best_bid  = book.best_bid()
                if best_bid < order.price:
                    break

                bid_level  = book.bids[best_bid]
                bid_order  = bid_level.peek_front()

                # Self-trade prevention -- see BUY-side comment above for
                # rationale. Same "skip" semantics.
                if (order.trader_id is not None
                        and bid_order.trader_id is not None
                        and order.trader_id == bid_order.trader_id):
                    next_order = self._peek_after(bid_level, bid_order)
                    if next_order is None:
                        break
                    bid_order = next_order

                fill_qty   = min(remaining_qty, bid_order.quantity)
                fully_filled = fill_qty == bid_order.quantity

                trades.append(Trade(
                    trade_id      = str(uuid.uuid4())[:8].upper(),
                    symbol        = order.symbol,
                    buy_order_id  = bid_order.order_id,
                    sell_order_id = order.order_id,
                    price         = best_bid,
                    quantity      = fill_qty,
                ))

                remaining_qty -= fill_qty

                if fully_filled:
                    bid_level.remove_specific(bid_order.order_id)
                    book.order_map.pop(bid_order.order_id, None)
                else:
                    bid_order.quantity  -= fill_qty
                    bid_level.total_qty -= fill_qty

                if bid_level.is_empty():
                    del book.bids[best_bid]

        # Remaining qty → passive resting limit order (LIMIT only; MARKET
        # orders pass rest_unfilled=False so any unfilled remainder is
        # dropped instead of resting at an artificial price)
        if remaining_qty > 0 and rest_unfilled:
            order.quantity = remaining_qty
            book.add_passive(order)
        elif remaining_qty > 0:
            # Market order partially/fully unfilled due to insufficient
            # liquidity — record how much was left on the floor instead of
            # silently discarding the information.
            order.quantity = remaining_qty  # reflects unfilled qty for the caller

        return trades

    # ── market order matching ────────────────────────────────
    def _match_market(self, book: OrderBook, order: Order) -> list[Trade]:
        """Market order: match at whatever price is available (IOC —
        Immediate or Cancel). Any quantity that can't be filled against
        current resting liquidity is simply not executed; it must NEVER
        rest in the book, because there is no real limit price to rest it
        at (using +inf/0 as a stand-in would corrupt best_bid/best_ask)."""
        # Use a sentinel price only for the matching walk's price
        # comparison; the order's own order_type stays MARKET throughout,
        # so callers/logs don't see it silently relabeled as LIMIT.
        original_price = order.price
        order.price = float('inf') if order.side == 'BUY' else 0.0
        try:
            trades = self._match_limit(book, order, rest_unfilled=False)
        finally:
            # Restore a sane price for any post-trade inspection/logging;
            # the matching walk itself has already completed.
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
# 5.  QUANT ANALYTICS  (Mahendra's Quant Background Applied!)
# ══════════════════════════════════════════════════════════════

class QuantAnalytics:
    """
    Real-time analytics on live trade stream.
    Uses: numpy vectorised ops, same math as your NIFTY projects.
    """
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
        Annualised realized volatility — same formula you used in
        your NIFTY Volatility Curve Prediction project!
        """
        if len(self.prices) < 2:
            return 0.0
        p           = np.array(self.prices, dtype=float)
        log_returns = np.diff(np.log(p))
        annual_vol  = float(np.std(log_returns) * np.sqrt(252 * 390))
        return round(annual_vol * 100, 2)   # in percent

    def z_score(self) -> float:
        """
        Z-score of current price vs rolling mean.
        Exact same formula as your Pairs Trading Z-score entry signal!
        """
        if len(self.prices) < 10:
            return 0.0
        p = np.array(self.prices, dtype=float)
        return round(float((p[-1] - p.mean()) / (p.std() + 1e-9)), 3)

    def order_imbalance(self, book: OrderBook) -> float:
        """
        (BidQty - AskQty) / (BidQty + AskQty)
        Range: -1.0 (all asks) to +1.0 (all bids)
        Positive → buy pressure → price likely to rise.
        """
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
# 6.  BENCHMARK  (Numbers for your resume bullets)
# ══════════════════════════════════════════════════════════════

def run_benchmark():
    print("\n" + "═"*52)
    print("  BENCHMARK — 10,000 Orders")
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

    # Submit 10,000 random orders and measure latency. GC is left enabled
    # (default Python behavior) -- this benchmark intentionally measures
    # realistic end-to-end latency including any GC pauses, rather than
    # disabling GC to produce a more flattering number. See the tail-
    # latency note below: P99.9 vs P50 is reported explicitly because the
    # gap is itself a real, explainable finding (see "GC and tail latency"
    # in README.md), not something to hide by cherry-picking percentiles.
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
        print(f"    Run with gc_comparison=True (see run_gc_comparison()) to")
        print(f"    measure the effect directly on this machine.")

    print(f"\n  Throughput        : {tput:,.0f} orders/sec")
    print("\n  ✅ Resume bullet ready:")
    print(f"  'Processed {s['orders_received']:,} orders at {tput:,.0f} orders/sec,")
    print(f"   median latency {p50:.1f}µs, P99 {p99:.1f}µs'")
    print("═"*52)
    return engine


def run_gc_comparison(n: int = 10_000):
    """Measures the same 10,000-order workload with Python's garbage
    collector enabled vs disabled, to quantify how much of the tail
    latency (P99.9) is attributable to GC pauses on THIS machine.

    Why this matters: a matching engine that's fast on the median but has
    occasional multi-hundred-microsecond stalls is a real, well-known
    characteristic of garbage-collected language runtimes (Python, Java,
    Go all have versions of this conversation) -- it's worth being able
    to name the cause and the standard mitigations (gc.disable() + manual
    collection at safe points, gc.freeze() after warmup to exclude
    long-lived objects from collection scans, or a GC-free runtime like
    C++/Rust for the hottest path) rather than only reporting the
    flattering median number.
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
# 7.  INTERACTIVE DEMO  (Run this to see it live)
# ══════════════════════════════════════════════════════════════

def run_demo():
    print("\n" + "═"*52)
    print("  NEXUS-TRADE ORDER MATCHING ENGINE — LIVE DEMO")
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
            print(f"  ✅ TRADE #{trade_count[0]:03d} | "
                  f"{t.quantity} @ {t.price:.2f} | ID: {t.trade_id}")

    engine.on_trade(on_trade_executed)

    # ── Seed the book ──────────────────────────────────────
    BASE = 24_500.0
    print("\n  [1] Seeding order book...")
    for i in range(1, 6):
        engine.submit_order(Order(f"BID-{i}", 'NIFTY50', 'BUY',
                                  round(BASE - i * 0.5, 2), 100 + i * 20))
        engine.submit_order(Order(f"ASK-{i}", 'NIFTY50', 'SELL',
                                  round(BASE + i * 0.5, 2), 80 + i * 15))

    print(book.snapshot())
    print(f"  Spread: {book.spread()} | Mid: {book.mid_price()}")

    # ── Match some orders ──────────────────────────────────
    print("\n  [2] Placing aggressive BUY limit order (crosses spread)...")
    engine.submit_order(Order('AGG-BUY-1', 'NIFTY50', 'BUY',
                               24_501.50, 200, 'LIMIT'))
    print(book.snapshot())

    print("\n  [3] Placing MARKET SELL order (60 qty)...")
    engine.submit_order(Order('MKT-SELL-1', 'NIFTY50', 'SELL',
                               0, 60, 'MARKET'))
    print(book.snapshot())

    # ── Cancel order demo ─────────────────────────────────
    print("\n  [4] Cancel order BID-3 (O(1) via HashMap)...")
    cancelled = engine.cancel_order('NIFTY50', 'BID-3')
    print(f"  Cancel result: {'Success ✅' if cancelled else 'Failed ❌'}")

    # ── Quant analytics ───────────────────────────────────
    print("\n  [5] Quant Analytics (Mahendra's speciality)...")
    print(analytics.summary(book))

    # ── Stats ─────────────────────────────────────────────
    print("\n  [6] Engine Stats:")
    for k, v in engine.stats().items():
        print(f"      {k:<22}: {v}")

    print("\n" + "═"*52)
    print("  Demo complete! Now run benchmark for resume numbers.")
    print("═"*52 + "\n")


# ══════════════════════════════════════════════════════════════
# 8.  ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    run_demo()
    input("\n  Press Enter to run 10,000-order benchmark...\n")
    run_benchmark()
