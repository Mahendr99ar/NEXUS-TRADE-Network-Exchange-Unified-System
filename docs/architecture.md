# Architecture Deep-Dive — QUANT-ME

## Why These Data Structures?

### Red-Black Tree for Price Levels

A stock exchange needs to answer two questions millions of times per second:
- "What is the best bid price right now?" → MAX of all bid prices
- "What is the best ask price right now?" → MIN of all ask prices

**Option 1 — Plain Python list:**
Insert: O(n) — must scan to find correct sorted position
Best price: O(n) — must scan entire list

**Option 2 — Red-Black Tree (SortedDict):**
Insert: O(log n) — tree stays balanced automatically
Best price: O(log n) — peek at leftmost/rightmost node

For 10,000 price levels: list = 10,000 operations vs tree = 14 operations.

### Doubly Linked List (deque) for FIFO Queue

At each price level, multiple orders wait in a queue.
Price-Time Priority says: same price → earlier order wins.

deque gives:
- Append to back (new order): O(1)
- Pop from front (match oldest): O(1)
- This is exactly what FIFO requires

### HashMap for O(1) Cancel

When a trader cancels an order, we must find it instantly.
Without HashMap: scan every price level → O(n * levels)
With HashMap (order_id → price): direct jump → O(1)

In live trading, cancel rates can be 90%+ of all messages.
O(1) cancel is not optional — it is mandatory.

## Matching Algorithm

```
submit_order(BUY, price=P, qty=Q):

  remaining = Q

  WHILE remaining > 0:
    best_ask = asks.peekitem(0)       # O(log n)

    IF best_ask > P:
      BREAK                           # price condition not met

    ask_queue = asks[best_ask]
    front_order = ask_queue[0]        # O(1) peek

    fill = min(remaining, front_order.qty)

    CREATE Trade(price=best_ask, qty=fill)

    front_order.qty -= fill
    remaining -= fill

    IF front_order.qty == 0:
      ask_queue.popleft()             # O(1) dequeue

    IF ask_queue.empty:
      DELETE asks[best_ask]           # O(log n)

  IF remaining > 0:
    ADD resting limit order to bids   # O(log n)
```

## Quant Analytics — Connection to Real Finance

### VWAP (Volume Weighted Average Price)
Used by institutional traders as execution benchmark.
"Did I get a better price than VWAP?" = good execution.

### Realized Volatility
Same formula used in options pricing (Black-Scholes sigma input).
Same computation Mahendra used in NIFTY Volatility Curve project.

### Z-Score
Same mean-reversion signal from Pairs Trading project (Sharpe 1.8).
When Z > 2.0, price is statistically far from mean → likely to revert.

### Order Book Imbalance
(BidQty - AskQty) / (BidQty + AskQty)
Academic research shows this predicts next 10-second price direction
with 60-65% accuracy. Used by HFT firms as a real-time alpha signal.
