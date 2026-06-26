# Architecture Deep-Dive — NEXUS-TRADE
 
This doc explains the "why" behind every design decision. The data structures aren't random — each one was chosen because the alternative would've been a bottleneck at exchange scale.
 
---
 
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
 
For 10,000 price levels: list = 10,000 operations vs tree = 14 operations. Not a small difference when you're doing this millions of times a second.
 
---
 
### Doubly Linked List for FIFO Queue

At each price level, multiple orders wait in a queue. Price-Time Priority says: same price -> earlier order wins.

This is implemented as a real doubly linked list (`_Node` objects with `prev`/`next` pointers, plus `head`/`tail` on `PriceLevel`), not a `deque`. That distinction matters for cancellation:

- Append to back (new order): O(1)
- Pop from front (match oldest): O(1)
- **Remove an arbitrary node from anywhere in the queue: O(1)** -- this is the part a `deque` cannot do without rebuilding the whole structure (`deque(x for x in old if x.id != target)` is O(n)). With real DLL nodes, `OrderBook.order_map` stores a direct reference to each order's node, so cancelling order #4,000 out of a 10,000-order queue is exactly as fast as cancelling order #1.

---

### HashMap for O(1) Cancel

When a trader cancels an order, we need to find it instantly **and remove it instantly** -- both halves matter.

- Without HashMap: scan every price level to find the order -> O(n x levels)
- With HashMap (order_id -> side, price, **and a direct DLL node reference**): O(1) lookup, O(1) removal

In live trading, cancel rates can be 90%+ of all messages -- most orders never actually fill, they just get cancelled. Genuine O(1) cancel (not just O(1) *lookup* followed by an O(n) removal) is the whole point here.
 
---
 
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
 
This follows the standard price-time priority matching logic used by continuous-auction exchanges generally (NSE's NEAT, NYSE, etc. all use variants of this) -- passive side's price wins, aggressor walks the book until filled or price condition breaks. The exact implementation details of any specific exchange's matching engine are proprietary and not publicly documented, so this should be read as "implements the standard algorithm," not "replicates NEAT's actual code."
 
---
 
## Quant Analytics — Connection to Real Finance
 
These signals didn't come out of nowhere. They connect directly to earlier projects.
 
### VWAP (Volume Weighted Average Price)
 
Used by institutional traders as an execution benchmark — "did I get a better average price than VWAP?" is how desks measure execution quality. Standard stuff, but useful to have live inside the engine.
 
### Realized Volatility
 
`std(log returns) × √252`
 
Same formula used as the sigma input in Black-Scholes. Also the same computation from the NIFTY Volatility Curve Prediction project — just applied here on live trade stream instead of historical data.
 
### Z-Score
 
`(current_price - rolling_mean) / rolling_std`
 
When Z > 2.0, price is statistically far from its mean — likely to revert. Same mean-reversion entry signal from the Pairs Trading project (Sharpe: 1.8), just running in real time here instead of on EOD data.
 
### Order Book Imbalance
 
`(BidQty - AskQty) / (BidQty + AskQty)`
 
Range is -1.0 (all asks, selling pressure) to +1.0 (all bids, buying pressure). Academic research puts its short-term directional accuracy at 60-65% over the next ~10 seconds. HFT firms use this as a real-time alpha signal — it's one of the few signals that actually works at sub-second resolution.
