# NEXUS-TRADE — Order Matching Engine

> Built from scratch in Python. The core matching algorithm, order book, and FIFO queueing are hand-written — `sortedcontainers` (balanced tree) and `numpy` (vectorized analytics math) are used as supporting libraries rather than reimplemented.

**Mahendra Meena | IIIT Gwalior | B.Tech EEE 2027**

---

I got curious about how stock exchanges actually work — not the trading part, but the infrastructure. What happens in the milliseconds between you clicking "Buy" and the order getting filled? That curiosity turned into this project.

Started it under the name **VELOX** (Latin for "fast") — the whole goal was speed. Somewhere along the way it grew into something bigger, so I renamed it NEXUS-TRADE to reflect what it actually is: a network exchange system, not just a latency experiment.

Turns out it's a very interesting DSA problem.

---

## Numbers

| Metric | Value |
|--------|-------|
| Throughput | ~70,000–80,000 orders/sec |
| P50 Latency | ~9–10 µs |
| P99 Latency | ~45–55 µs |
| Fill Rate | ~97% (varies run to run with random order mix) |

Measured on a regular laptop, single-threaded, no async/multiprocessing tricks — just the right data structures for the right reasons. These numbers are environment-dependent (CPU, Python version, background load) — re-run `run_benchmark()` yourself rather than treating any single number as fixed; report a range, not a cherry-picked best run.

---

## What it does

NEXUS-TRADE implements **Price-Time Priority matching** — the standard algorithm used by major exchanges (NSE, BSE, NYSE) for their continuous limit order books. You submit a BUY or SELL order, and the engine:

1. Checks if there's a matching order on the other side
2. Fills as much as possible, FIFO within each price level
3. Parks whatever's left as a resting limit order (LIMIT only — see note below)
4. Logs the trade and updates analytics in real time

Supports LIMIT and MARKET orders (MARKET orders behave as Immediate-or-Cancel — unfilled remainder is dropped, never rested at an artificial price), a live two-sided order book, genuinely O(1) cancellation, and a quant analytics layer on top (VWAP, Realized Vol, Z-score, Order Imbalance).

---

## Why these data structures

This was the actual interesting part to figure out.

```
Order Book
├── Bids  → SortedDict (Red-Black Tree) — max-first, O(log n)
│   └── Each price level → real doubly linked list — O(1) FIFO matching, O(1) cancel
└── Asks  → SortedDict (Red-Black Tree) — min-first, O(log n)
    └── Each price level → real doubly linked list — O(1) FIFO matching, O(1) cancel

Order Map → dict (HashMap) → order_id -> (side, price, DLL node reference)
Trade Log → deque(maxlen=1000) — circular buffer (recent-trade cache, not a durable audit log)
```

The exchange needs to answer "what's the best bid/ask right now?" millions of times per second. A plain list would be O(n) for both insert and lookup. A Red-Black Tree gives O(log n) — for 10,000 price levels that's the difference between 10,000 operations and 14.

Cancel rates in live trading can be 90%+ of all messages. **A correctness note from code review**: an earlier version of this project used a `deque` per price level and "cancelled" an order by rebuilding the entire deque while filtering it out — that's O(n) per cancel, not O(1), no matter what the HashMap lookup costs. It's now a true doubly linked list where `order_map` stores a direct reference to the order's node, so cancelling from anywhere in the queue (front, middle, or back) is a constant-time pointer unlink. Verified empirically: cancelling the last order in a 20,000-order queue takes ~5µs with the linked-list version vs. ~1.8ms with the old deque-rebuild version — a ~350x difference that only shows up at scale, which is exactly why it matters for an exchange.

See [`docs/architecture.md`](docs/architecture.md) for the full breakdown with pseudocode.

| Operation | Structure | Complexity |
|-----------|-----------|------------|
| Insert order | Red-Black Tree | O(log n) |
| Match order | Doubly linked list (FIFO) | O(1) per fill |
| Cancel order | HashMap + DLL node ref | O(1) |
| Best bid/ask | RB-Tree peek | O(log n) |
| Trade log | Circular Buffer | O(1) |

---

## Running it

```bash
git clone https://github.com/Mahendr99ar/NEXUS-TRADE-Network-Exchange-Unified-System.git
cd NEXUS-TRADE-Network-Exchange-Unified-System

pip install -r requirements.txt

# Live demo
python src/order_matching_engine.py

# Tests
pytest tests/ -v
```

---

## Sample output

```
════════════════════════════════════════════
  NEXUS-TRADE ORDER MATCHING ENGINE — LIVE DEMO
  By Mahendra Meena | IIIT Gwalior
════════════════════════════════════════════
  [Engine] Symbol added: NIFTY50

  TRADE #001 | 95 @ 24500.50 | ID: CAA68D6F
  TRADE #002 | 105 @ 24501.00 | ID: 6A600B80
  TRADE #003 | 60 @ 24499.50 | ID: B75FB6AC

  VWAP: 24500.47   RealizedVol: 1.28%   Z-score: 0.000   Imbalance: 0.846

  Throughput : ~75,000 orders/sec (varies by run/machine)
  P50 latency: ~9-10 µs
  P99 latency: ~45-55 µs
```

---

## Analytics layer

The signals here connect directly to earlier work I did on Pairs Trading (Sharpe: 1.8) and NIFTY Volatility Prediction:

| Signal | Formula | Why it matters |
|--------|---------|----------------|
| VWAP | Σ(price × vol) / Σvol | Standard execution benchmark for institutions |
| Realized Volatility | std(log returns) × √252 | Same computation as Black-Scholes sigma input |
| Z-score | (price − mean) / std | Mean-reversion trigger — same signal from the pairs trading work |
| Order Imbalance | (bidQty − askQty) / total | Reported in academic literature to have short-term directional accuracy in the 60-65% range over ~10s windows; treat as a research-backed heuristic, not a guarantee |

---

## Project structure

```
NEXUS-TRADE/
├── src/
│   └── order_matching_engine.py   # core engine
├── tests/
│   └── test_engine.py             # pytest suite (29 tests, incl. regression tests for fixed bugs)
├── docs/
│   └── architecture.md            # detailed design notes
├── requirements.txt
└── README.md
```

---

## Known limitations (honest list)

- **Single-threaded, no concurrency safety.** `order_map`, `bids`, and `asks` are shared mutable state with no locking. Fine for a single-process demo; would need synchronization (or a single-writer event-loop design) before handling concurrent order submission from multiple clients.
- **Trade log is a bounded cache, not an audit trail.** `deque(maxlen=1000)` silently drops older trades. A real exchange needs a durable, append-only log for compliance/replay — this is explicitly out of scope here.
- **No persistence.** Order book and trade history live in memory only; a crash/restart loses all state.
- **Single matching thread / no sharding across symbols** — fine at this scale, would need partitioning per-symbol to scale further.

## What's next

- [ ] WebSocket API via FastAPI — so you can actually submit orders over a connection
- [ ] Multi-symbol support (BANKNIFTY, RELIANCE, etc.)
- [ ] C++ port — Python gets you to ~75K orders/sec, C++ should hit sub-microsecond
- [ ] FIX protocol parsing
- [ ] SQLite persistence for the order log
- [ ] Thread-safety / concurrent order submission
---

**Mahendra Meena** — [LinkedIn](https://www.linkedin.com/in/mahendra-meena-72047b201/?lipi=urn%3Ali%3Apage%3Ad_flagship3_profile_view_base_contact_details%3BiaoO9%2FdjRKWOhaWxs1eueg%3D%3D)
