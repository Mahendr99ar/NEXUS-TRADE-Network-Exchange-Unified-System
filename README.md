# NEXUS-TRADE: Order Matching Engine

A limit order book and matching engine in Python, with a paper-trading broker and a web terminal on top. I wrote the matching loop, the order book, and the FIFO queues myself. Two libraries do supporting work: `sortedcontainers` keeps price levels sorted, and `numpy` handles the analytics math.

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

The project has three layers, the same split as a real market:

| Layer | Real-world equivalent | File |
|-------|----------------------|------|
| **Exchange** | NSE's matching engine | `src/order_matching_engine.py` |
| **Broker** | A broker's account and risk system (RMS) | `src/broker.py` |
| **Other traders** | Market makers and other participants | `src/market_maker.py` |

**The exchange** matches orders by **price-time priority**, the rule used by continuous limit order books on exchanges such as NSE, BSE, and NYSE. When a BUY or SELL order arrives, the engine:

1. checks the opposite side of the book for a price that crosses,
2. fills as much as it can, oldest order first within each price level,
3. rests any unfilled LIMIT quantity in the book, and
4. records each trade and updates the analytics.

MARKET orders are Immediate-or-Cancel: any part that can't fill is cancelled, never rested at a made-up price. Cancels are O(1). Prices must be a multiple of the tick size (0.05 in the web demo). Self-trade prevention skips every resting order from the same trader and never leaves the book crossed. Each order tracks its status (OPEN, COMPLETE, CANCELLED, REJECTED) and filled quantity.

**The broker** sits in front of the exchange, as a real broker does. The engine never asks whether a trader can afford an order. The broker checks that before the order reaches the engine:

- each account starts with ₹10,00,000 of virtual funds
- orders are intraday (MIS) with 20% margin, so 5x leverage
- short selling is allowed when there is margin for it
- an order is **rejected with a reason** if margin is short, the price is outside the ±10% circuit band, the price is off-tick, the account has 50 open orders, or it sends more than 10 orders a second
- positions, average price, and realized and unrealized P&L update on every fill

**The other traders** keep the market alive. A market maker re-quotes five levels on each side every second around a fair price that follows a random walk, and small market orders from other traders print trades. They go through the same engine and the same rules. A user's order resting at a price gets filled before a fresh market-maker quote at that price.

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

# Web terminal at http://127.0.0.1:8000
uvicorn api:app --app-dir src --reload

# Tests
pytest tests/ -v
```

---

## Web terminal

Open the site, enter a name, and you get a paper account. Then:

- **B** / **S** (or the Buy and Sell buttons) open the order window. It shows the margin the order needs and what you have.
- **Orders** lists open orders (with Cancel) and executed ones. A rejected order shows why it was rejected.
- **Positions** shows quantity, average price, LTP, and P&L, with an **Exit** button that closes the position at market.
- **Funds** shows available and used margin, account value, and realized and unrealized P&L.
- The profile menu (top right) has funds, **Reset account**, and **Logout**.

## Web API

FastAPI also generates interactive docs at `/docs`. Account endpoints need the `X-Token` header returned by `/api/login`.

| Method | Path | Login | What it does |
|--------|------|-------|--------------|
| POST | `/api/login` | | Create a paper account: `{"name": "Mahendra"}` → token, funds, position |
| GET | `/api/me` | ✓ | Profile, funds, position, orders, and trades |
| POST | `/api/orders` | ✓ | Place an order: `{"side": "BUY", "order_type": "LIMIT", "price": 24500.5, "quantity": 25}`. Returns the order with its status, or REJECTED with a message |
| DELETE | `/api/orders/{order_id}` | ✓ | Cancel one of your open orders |
| GET | `/api/orders` | ✓ | Your orders, newest first |
| POST | `/api/account/reset` | ✓ | Cancel your orders, clear your position, restore ₹10,00,000 |
| GET | `/api/book?depth=5` | | Bids, asks, LTP, spread, mid |
| GET | `/api/trades?limit=50` | | Market trades, newest first |
| GET | `/api/analytics` | | VWAP, realized vol, z-score, imbalance, engine stats |
| POST | `/api/reset` | admin | Wipe the market and all accounts. Needs the `ADMIN_KEY` environment variable and an `X-Admin-Key` header |

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
│   ├── order_matching_engine.py   # exchange: book, matching, analytics, demo, benchmark
│   ├── broker.py                  # accounts, margin check (RMS), positions, P&L
│   ├── market_maker.py            # simulated market maker and order flow
│   └── api.py                     # FastAPI app and the background market loop
├── static/
│   └── index.html                 # web terminal (plain HTML/JS, no build step)
├── tests/
│   ├── test_engine.py             # 40 engine tests, including regressions
│   └── test_api.py                # 20 API, broker, and market-maker tests
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

- **Accounts live in memory.** There's no database and no password. The browser keeps a token, and on the free Render plan the instance sleeps after 15 idle minutes, which wipes every account and resets the market.
- **One instrument.** Only NIFTY50, and the price is simulated, not real market data.
- **Margin is simplified.** A flat 20% on the worst-case position, valued at the last traded price. Real brokers use SPAN and exposure margins, and auto square-off at the end of the day.
- **The engine isn't thread-safe.** The web layer puts one lock around the engine, the broker, and the market loop. That's enough for a demo. Real order entry needs a single-writer design.
- **The trade log is a cache, not an audit trail.** `deque(maxlen=1000)` drops older trades. An exchange needs a durable, append-only log for compliance and replay.
- **The page polls.** It asks the server every 1.5 seconds instead of receiving a WebSocket stream.

## What's next

- [x] HTTP API with FastAPI and a web terminal
- [x] Paper-trading accounts with a margin check, positions, and P&L
- [ ] WebSocket feed instead of polling
- [ ] More symbols (BANKNIFTY, RELIANCE, etc.)
- [ ] SQLite or Postgres so accounts survive restarts
- [ ] Stop-loss and good-till-date orders
- [ ] C++ port: Python reaches ~75K orders/sec; the goal for C++ is sub-microsecond latency
- [ ] FIX protocol parsing

---

**Mahendra Meena**, [LinkedIn](https://www.linkedin.com/in/mahendra-meena-72047b201/)
