# NEXUS-TRADE: Order Matching Engine

A limit order book and matching engine in Python. I wrote the matching loop, the order book, and the FIFO queues myself. Two libraries do supporting work: `sortedcontainers` keeps price levels sorted, and `numpy` handles the analytics math.

**Mahendra Meena | IIIT Gwalior | B.Tech EEE 2027**

**Live demo:** [your-app.onrender.com](https://your-app.onrender.com) *(replace with your Render URL after deploying)*

---

I wanted to know what happens between clicking "Buy" and getting a fill. I don't mean the trading side. I mean the exchange software that does the matching. This project is my answer.

It started as **VELOX** (Latin for "fast"), and the only goal was speed. It grew from a latency experiment into a small network exchange system, so I renamed it NEXUS-TRADE.

Most of the hard parts turned out to be data structure choices.

---

## Numbers

| Metric | Value |
|--------|-------|
| Throughput | ~70,000–80,000 orders/sec |
| P50 latency | ~9–10 µs |
| P99 latency | ~45–55 µs |
| Fill rate | ~97% (varies with the random order mix) |

These were measured on a regular laptop, single-threaded, with no async or multiprocessing. They depend on CPU, Python version, and background load, so run `run_benchmark()` yourself and treat the table as a range, not a fixed result.

---

## What it does

The engine uses **price-time priority**, the matching rule used by continuous limit order books on exchanges such as NSE, BSE, and NYSE. When a BUY or SELL order arrives, the engine:

1. checks the opposite side of the book for a price that crosses,
2. fills as much as it can, oldest order first within each price level,
3. rests any unfilled LIMIT quantity in the book, and
4. records each trade and updates the analytics.

It supports LIMIT and MARKET orders. MARKET orders are Immediate-or-Cancel: any part that can't fill is dropped, never rested at a made-up price. Cancels are O(1). Self-trade prevention is optional: when two orders carry the same `trader_id`, the engine skips the resting one. The analytics layer reports VWAP, realized volatility, z-score, and order imbalance.

---

## Data structures

```
Order Book
├── Bids  → SortedDict, highest price first, O(log n)
│   └── each price level → doubly linked list, O(1) FIFO match, O(1) cancel
└── Asks  → SortedDict, lowest price first, O(log n)
    └── each price level → doubly linked list, O(1) FIFO match, O(1) cancel

Order map → dict: order_id -> (side, price, linked-list node)
Trade log → deque(maxlen=1000): recent trades only, not an audit log
```

The engine keeps asking for the best bid and the best ask. A plain sorted list costs O(n) per insert. `SortedDict` from `sortedcontainers` keeps prices in order with roughly O(log n) inserts and lookups. It is built on a sorted list of sublists rather than a red-black tree, but it plays the same role. At 10,000 price levels, log₂ n is about 14.

Cancels can make up 90% or more of exchange messages, so cancel speed matters. An earlier version stored each price level in a `deque` and cancelled by rebuilding the deque without the cancelled order. That is O(n), even though the dict lookup before it was O(1). Each level is now a doubly linked list, and `order_map` holds a direct reference to each order's node. A cancel unlinks two pointers wherever the order sits in the queue. Cancelling the last order in a 20,000-order queue took ~5 µs with the linked list and ~1.8 ms with the deque rebuild.

Details and pseudocode are in [`docs/architecture.md`](docs/architecture.md).

| Operation | Structure | Complexity |
|-----------|-----------|------------|
| Insert order | SortedDict | O(log n) |
| Match order | Doubly linked list (FIFO) | O(1) per fill |
| Cancel order | dict + linked-list node | O(1) |
| Best bid/ask | SortedDict peek | O(log n) |
| Trade log | deque(maxlen) | O(1) |

---

## Running it locally

```bash
git clone https://github.com/Mahendr99ar/NEXUS-TRADE-Network-Exchange-Unified-System.git
cd NEXUS-TRADE-Network-Exchange-Unified-System

pip install -r requirements-dev.txt

# Terminal demo, then the 10,000-order benchmark
python src/order_matching_engine.py

# Web app at http://127.0.0.1:8000
uvicorn api:app --app-dir src --reload

# Tests
pytest tests/ -v
```

---

## Web API

The web page at `/` calls these endpoints. FastAPI also generates interactive docs at `/docs`.

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/api/book?depth=10` | Bids, asks, best prices, spread, mid |
| POST | `/api/orders` | Submit an order: `{"side": "BUY", "order_type": "LIMIT", "price": 24500.5, "quantity": 50, "trader_id": "optional"}` |
| DELETE | `/api/orders/{order_id}` | Cancel a resting order |
| GET | `/api/orders` | Resting orders placed through the web API |
| GET | `/api/trades?limit=50` | Recent trades, newest first |
| GET | `/api/analytics` | VWAP, realized vol, z-score, imbalance, engine stats |
| POST | `/api/reset` | Clear the book and reseed it |

The web demo trades one symbol, NIFTY50. On startup it seeds five levels on each side of 24,500.

---

## Sample output

```
════════════════════════════════════════════
  NEXUS-TRADE ORDER MATCHING ENGINE: DEMO
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

These signals come from my earlier work on pairs trading (Sharpe 1.8) and NIFTY volatility prediction.

| Signal | Formula | Use |
|--------|---------|-----|
| VWAP | Σ(price × vol) / Σvol | Standard execution benchmark for institutions |
| Realized volatility | std(log returns) × √(252 × 390) | Annualised, treating each trade as a 1-minute bar (390 per session, 252 sessions) |
| Z-score | (price − mean) / std | Mean-reversion entry signal from the pairs trading work |
| Order imbalance | (bidQty − askQty) / total, at the top of book | Studies report 60–65% short-term directional accuracy over ~10 s windows. A heuristic, not a guarantee |

---

## Project structure

```
NEXUS-TRADE/
├── src/
│   ├── order_matching_engine.py   # engine, analytics, demo, benchmark
│   └── api.py                     # FastAPI app for the web demo
├── static/
│   └── index.html                 # web page (plain HTML/JS, no build step)
├── tests/
│   ├── test_engine.py             # 33 engine tests, including regressions
│   └── test_api.py                # 7 API tests
├── docs/
│   └── architecture.md            # design notes
├── .github/workflows/tests.yml    # runs pytest on every push
├── render.yaml                    # free hosting config for Render
├── requirements.txt               # runtime
├── requirements-dev.txt           # runtime + pytest + httpx
└── README.md
```

---

## Known limitations

- **The engine isn't thread-safe.** `order_map`, `bids`, and `asks` are shared mutable state. The web API puts one lock around every engine call, which is enough for a demo. Real concurrent order entry needs a single-writer design.
- **The trade log is a cache, not an audit trail.** `deque(maxlen=1000)` drops older trades. An exchange needs a durable, append-only log for compliance and replay.
- **There's no persistence.** Everything lives in memory. On the free Render plan the instance sleeps after 15 idle minutes, and the book resets when it wakes up.
- **The web demo has one shared book.** Every visitor sees and trades against the same orders.
- **There's one matching thread and no sharding by symbol.** That's fine at this scale. Going further would mean partitioning by symbol.

## What's next

- [x] HTTP API with FastAPI and a web page
- [ ] WebSocket feed instead of polling
- [ ] More symbols (BANKNIFTY, RELIANCE, etc.)
- [ ] C++ port: Python reaches ~75K orders/sec; the goal for C++ is sub-microsecond latency
- [ ] FIX protocol parsing
- [ ] SQLite persistence for the order log
- [ ] Concurrent order submission

---

**Mahendra Meena**, [LinkedIn](https://www.linkedin.com/in/mahendra-meena-72047b201/)
