# NEXUS-TRADE Architecture

Why each data structure is there, and what it would cost to use something simpler.

---

## Price levels: SortedDict

The engine keeps asking two questions:

- What is the best bid? (highest bid price)
- What is the best ask? (lowest ask price)

**Plain Python list**
- Insert: O(n) to find the sorted position and shift elements
- Best price: O(1) if kept sorted, but every insert pays O(n)

**`sortedcontainers.SortedDict`**
- Insert: about O(log n)
- Best price: `peekitem(0)`, about O(log n)

`SortedDict` is not a red-black tree. It keeps keys in a sorted list of sublists, which in practice is often faster than a tree in Python. It gives the same operations a balanced tree would. Bids use the key function `-price`, so index 0 is always the highest bid. Asks use natural order, so index 0 is the lowest ask.

At 10,000 price levels, log₂ n is about 14 steps against up to 10,000 for a linear scan.

---

## Queue at each price level: doubly linked list

Price-time priority means that at the same price, the earlier order fills first. Each `PriceLevel` is a doubly linked list of `_Node` objects (`prev`/`next` pointers, plus `head`/`tail` on the level). It is not a `deque`, and the difference shows up on cancel:

- append a new order at the tail: O(1)
- remove the oldest order from the head: O(1)
- **remove any node from anywhere in the queue: O(1)**

A `deque` can't do the third one. Removing from the middle meant rebuilding it, `deque(x for x in old if x.id != target)`, which is O(n). With linked-list nodes, `OrderBook.order_map` keeps a reference to each order's node, so cancelling order #4,000 in a 10,000-order queue costs the same as cancelling order #1.

`PriceLevel.total_qty` is updated as nodes are added and unlinked. On a partial fill, the matching loop subtracts the filled quantity itself. On a full fill, unlinking the node subtracts it. Doing both would count the fill twice.

---

## Cancel: dict + node reference

A cancel has two steps: find the order, then remove it. Both need to be fast.

- Without the dict: search every price level, O(orders × levels)
- With `order_id -> (side, price, node)`: O(1) lookup and O(1) unlink

Most orders on a live exchange are cancelled, not filled, and cancels can be 90% or more of messages. An O(1) lookup followed by an O(n) removal would not help.

---

## Matching algorithm

```
submit_order(BUY, price=P, qty=Q):

  remaining = Q

  WHILE remaining > 0 AND asks not empty:
    best_ask = asks.peekitem(0)             # about O(log n)

    IF best_ask > P:
      BREAK                                 # no longer crosses

    level = asks[best_ask]
    resting = level.peek_front()            # O(1)
    (if STP applies, skip to the next order in the level)

    fill = min(remaining, resting.qty)
    CREATE Trade(price=best_ask, qty=fill)  # resting order's price
    remaining -= fill

    IF fill == resting.qty:
      unlink resting from level             # O(1)
      remove resting from order_map         # O(1)
    ELSE:
      resting.qty -= fill
      level.total_qty -= fill

    IF level is empty:
      DELETE asks[best_ask]

  IF remaining > 0:
    IF LIMIT:  rest remaining in bids       # about O(log n)
    IF MARKET: drop it (Immediate-or-Cancel)
```

SELL mirrors this and walks bids from the highest price down.

This is the standard price-time priority rule used by continuous-auction exchanges in general. The trade prints at the resting order's price, and the incoming order walks the book until it fills or stops crossing. Specific exchange engines such as NSE's NEAT are proprietary and not publicly documented, so read this as an implementation of the standard algorithm, not a copy of any exchange's code.

### MARKET orders

A MARKET order runs through the same loop with a stand-in price (+inf for BUY, 0 for SELL) so that every level crosses. It never rests. A resting order at +inf or 0 would become the best bid or ask and break every later match. The order keeps `order_type = 'MARKET'`, and its original price is restored once matching ends.

### Self-trade prevention

If the incoming order and the order at the front of the level have the same `trader_id`, the front order is skipped and the next order at that price is tried. If either side has no `trader_id`, nothing is skipped. Exchanges enforce this (NSE STP, CME self-match prevention) because self-trades inflate volume and can be used for wash trading.

---

## Analytics

These come from earlier projects of mine.

### VWAP

`Σ(price × volume) / Σ volume` over the last `window` trades. Desks use VWAP as an execution benchmark: did the order average a better price than VWAP?

### Realized volatility

`std(log returns) × √(252 × 390)`, reported in percent.

Each trade is treated as one 1-minute bar: 390 minutes per session, 252 sessions per year. It is the same quantity as the sigma input to Black-Scholes, and the same computation as in my NIFTY volatility curve project, applied here to the live trade stream instead of historical data.

### Z-score

`(latest_price - rolling_mean) / rolling_std`, returning 0 until there are 10 trades.

When |Z| > 2, the price is far from its recent mean. In my pairs trading project (Sharpe 1.8) that was the mean-reversion entry signal. Here it is computed in real time instead of on end-of-day data.

### Order book imbalance

`(BidQty - AskQty) / (BidQty + AskQty)` at the best bid and best ask.

It ranges from -1.0 (all size on the ask, selling pressure) to +1.0 (all size on the bid, buying pressure). Studies report 60–65% directional accuracy over roughly the next 10 seconds, and high-frequency firms use imbalance as a short-term signal. Treat it as a heuristic, not a guarantee.

---

## Web layer

`src/api.py` wraps one `MatchingEngine` in a FastAPI app. FastAPI runs plain `def` endpoints in a thread pool, and the engine is not thread-safe, so every endpoint takes a single `threading.Lock` before it touches the engine. `static/index.html` polls the API every two seconds. It has no framework and no build step.

To keep a public free instance from running out of memory, quantities are capped at 1,000,000 and the book refuses new LIMIT orders once it holds 5,000 resting orders. `POST /api/reset` clears it.
