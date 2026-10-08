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

  i = 0                                     # index of the level being matched
  WHILE remaining > 0 AND i < len(asks):
    price, level = asks.peekitem(i)         # about O(log n)

    IF price > P:
      BREAK                                 # no longer crosses

    FOR each resting order in level, oldest first:
      IF resting.trader == incoming.trader:
        SKIP it                             # self-trade prevention
      fill = min(remaining, resting.qty)
      CREATE Trade(price=price, qty=fill)   # resting order's price
      remaining -= fill
      IF fill == resting.qty:
        unlink resting from level           # O(1)
        remove resting from order_map       # O(1)
      ELSE:
        resting.qty -= fill
        level.total_qty -= fill
      STOP when remaining == 0

    IF level is empty:
      DELETE asks[price]                    # the next level moves to index i
    ELSE:
      i += 1                                # only own orders left here

  IF remaining > 0:
    IF MARKET: cancel it (Immediate-or-Cancel)
    ELSE IF it still crosses the best ask:  # only own orders can cause this
      cancel it (self-trade prevention)
    ELSE: rest it in bids                   # about O(log n)
```

SELL mirrors this and walks bids from the highest price down.

This is the standard price-time priority rule used by continuous-auction exchanges in general. The trade prints at the resting order's price, and the incoming order walks the book until it fills or stops crossing. Specific exchange engines such as NSE's NEAT are proprietary and not publicly documented, so read this as an implementation of the standard algorithm, not a copy of any exchange's code.

### MARKET orders

A MARKET order runs through the same loop with a stand-in price (+inf for BUY, 0 for SELL) so that every level crosses. It never rests. A resting order at +inf or 0 would become the best bid or ask and break every later match. The order keeps `order_type = 'MARKET'`, and its original price is restored once matching ends.

### Self-trade prevention

An incoming order never trades with a resting order that has the same `trader_id`. Every such order is skipped, not only the one at the front, and matching continues with the next order and the next price level. If either side has no `trader_id`, nothing is skipped. Exchanges enforce this (NSE STP, CME self-match prevention) because self-trades inflate volume and can be used for wash trading.

If a LIMIT order still has quantity left after the walk and that quantity would cross the book, the only orders it could cross are the trader's own. Resting it would leave best bid >= best ask, so the remainder is cancelled instead, with a message saying why.

### Tick size and price keys

Prices are dict keys, so `0.1 + 0.2` and `0.3` must land on the same level. `OrderBook.normalize_price` rounds every limit price to a clean float. With a tick size set (the web demo uses 0.05, like NSE equities), a price that isn't a whole number of ticks is rejected.

### Order lifecycle

Each `Order` carries `status` (OPEN, COMPLETE, CANCELLED, REJECTED), `filled_qty`, `original_qty`, and a `message` with the reason for a cancel or reject. `quantity` is the open quantity and shrinks as the order fills.

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

## Broker layer

`src/broker.py` plays the part of a broker. On a real market the exchange never checks whether a trader can afford an order; the broker's risk system does that before the order is sent. This layer adds:

- **Accounts.** `POST /api/login` creates an account with ₹10,00,000 and returns a random token. There is no password; the browser keeps the token.
- **Pre-trade checks (RMS).** Before an order reaches the engine: at most 10 orders a second, at most 50 open orders, a limit price within ±10% of the last traded price, and enough margin. A failed check returns the order as REJECTED with the reason.
- **Margin.** Intraday (MIS) at 20%, so 5x leverage. Margin covers the worst case, either all open buys filling or all open sells filling:

  ```
  worst_long  = position + open_buy_qty
  worst_short = open_sell_qty - position
  margin      = max(worst_long, worst_short, 0) × LTP × 20%
  ```

  An order is accepted if the extra margin it adds fits in `available = equity - used margin`. A sell that closes a long position adds no margin, so it is always allowed. Short selling is allowed when there is margin for it.
- **Positions and P&L.** The engine's trade callback reports every fill. Adding to a position updates the weighted average price. Reducing it books realized P&L, `closed_qty × (price - avg) × direction`. A fill bigger than the position flips it, and the new side opens at the fill price. Unrealized P&L is `position × (LTP - avg)`.

The user's id is the order's `trader_id`, so the engine's self-trade prevention stops a user from trading with themselves.

## Simulated traders

`src/market_maker.py` keeps the web market moving. Every second:

1. The fair price takes a random step (Gaussian, σ ≈ 1.1 points), with a slight pull back towards 24,500 and a hard limit of ±8%.
2. With probability 0.55, another trader sends a small market order, leaning towards the fair price, so trades print.
3. The market maker cancels its quotes and posts five fresh levels on each side, a few ticks apart.

These orders go through the same engine. A user's order already resting at a price stays ahead of a new quote at that price.

## Web layer

`src/api.py` holds one engine, one broker, and one market maker. FastAPI runs plain `def` endpoints in a thread pool and the market loop runs in its own thread, so everything takes a single `threading.Lock` before touching shared state. The market loop starts with the app (FastAPI lifespan) and can be turned off with `NEXUS_MARKET_BOT=0`.

`static/index.html` is the trading terminal: watchlist and market depth, chart, order window, orders, positions, funds, and a profile menu. It polls every 1.5 seconds. It has no framework and no build step.

`POST /api/reset` wipes the market and every account. It only works when the server has an `ADMIN_KEY` environment variable and the request sends the same value in `X-Admin-Key`.
