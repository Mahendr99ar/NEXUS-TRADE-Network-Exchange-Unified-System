# NEXUS-TRADE

*What happens between clicking Buy and getting a fill.*

A limit order book and matching engine written from scratch in Python, with a paper-trading broker, a simulated market maker and a web terminal on top.

[![tests](https://github.com/Mahendr99ar/NEXUS-TRADE-Network-Exchange-Unified-System/actions/workflows/tests.yml/badge.svg)](https://github.com/Mahendr99ar/NEXUS-TRADE-Network-Exchange-Unified-System/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)](render.yaml)
[![Tests: 60](https://img.shields.io/badge/tests-60%20passing-2E7D32.svg)](tests)
[![Live on Render](https://img.shields.io/badge/demo-Render-46E3B7.svg)](https://nexus-trade-network-exchange-unified.onrender.com)

> **NEXUS-TRADE is not a trading bot and does not touch real money.** It is the exchange side: the software that decides which order fills against which, in what order, at what price. The market is simulated and every account is a paper account.

**Live demo:** [nexus-trade-network-exchange-unified.onrender.com](https://nexus-trade-network-exchange-unified.onrender.com)
**API docs:** [/docs](https://nexus-trade-network-exchange-unified.onrender.com/docs)
**Design notes:** [docs/architecture.md](docs/architecture.md)

> The demo runs on Render's free plan. It sleeps after 15 idle minutes, so the first visit can take about a minute, and a restart wipes every account.

---

## What this is

It started as **VELOX** (Latin for "fast"), a latency experiment with one goal: match orders as quickly as plain Python allows. It grew into a small exchange system with the same three layers a real market has.

| Layer | Real-world equivalent | File |
|---|---|---|
| Exchange | NSE's matching engine | `src/order_matching_engine.py` |
| Broker | A broker's account and risk system (RMS) | `src/broker.py` |
| Other traders | Market makers and order flow | `src/market_maker.py` |

The matching loop, the order book and the FIFO queues are hand-written. Two libraries do supporting work: `sortedcontainers` keeps price levels sorted, and `numpy` does the analytics maths.

Most of the hard parts turned out to be data structure choices.

---

## Why not just use a sorted list and a deque?

That was the first version. It worked, and it was slow in exactly the places that matter.

- **Inserting into a sorted list is O(n).** The engine asks for the best bid and best ask on every order. `SortedDict` keeps prices ordered with roughly O(log n) inserts. At 10,000 price levels, log₂ n is about 14.
- **Cancelling from a deque is O(n).** Cancels can be 90% or more of the messages an exchange sees. Removing an order from the middle of a deque meant rebuilding it. Each price level is now a doubly linked list, and a map points straight at every order's node, so a cancel unlinks two pointers.

Cancelling the last order in a 20,000-order queue: about **5 µs** with the linked list, about **1.8 ms** with the deque rebuild.

---

## How it works

### Stage 1: The broker checks the order

The engine never asks whether a trader can afford an order. The broker does that first, as a real broker does:

- each account starts with **₹10,00,000** of virtual funds
- orders are intraday (MIS) at **20% margin**, so 5x leverage
- short selling is allowed when there is margin for it
- an order is **rejected with a reason** if margin is short, the price is outside the **±10% circuit band**, the price is off-tick, the account already has **50 open orders**, or it sends more than **10 orders a second**

### Stage 2: The engine matches by price-time priority

The same rule continuous limit order books use on NSE, BSE and NYSE. When an order arrives the engine:

1. checks the opposite side of the book for a price that crosses,
2. fills as much as it can, oldest order first within each price level,
3. rests any unfilled LIMIT quantity in the book,
4. records each trade and updates the analytics.

MARKET orders are Immediate-or-Cancel: whatever cannot fill is cancelled, never rested at a made-up price. Prices must sit on the tick (0.05 in the web demo). Self-trade prevention skips every resting order from the same trader and never leaves the book crossed.

### Stage 3: Positions update on every fill

Quantity, average price, and realized and unrealized P&L change as each trade prints. Every order carries its status (OPEN, COMPLETE, CANCELLED, REJECTED) and its filled quantity.

### Stage 4: The market keeps moving

A market maker re-quotes five levels on each side every second around a fair price that follows a random walk. Small market orders from other simulated traders print trades. They go through the same engine and the same rules, so a user's order resting at a price fills before a fresh market-maker quote at that price.

---

## Architecture

```
 Browser (static/index.html)            FastAPI (src/api.py)                     Engine (src/order_matching_engine.py)
 ---------------------------            --------------------                     -------------------------------------
 order window, book, positions   --->   /api/login  -> X-Token                   Bids: SortedDict, highest first
 polls every 1.5 s                      /api/orders --> Broker (src/broker.py)   Asks: SortedDict, lowest first
                                                        margin, band, tick,        each level: doubly linked list (FIFO)
                                                        open-order cap,          order_map: id -> linked-list node
                                                        rate limit       ----->  match, rest, cancel in O(1)
                                                                                 trade log: deque(maxlen=1000)
                                        background loop --> Market maker                 |
                                        one lock around     (src/market_maker.py) ------>+
                                        engine + broker                                  v
                                                                                 VWAP, realized vol, z-score, imbalance
```

| Operation | Structure | Complexity |
|---|---|---|
| Insert order | SortedDict | O(log n) |
| Match order | Doubly linked list (FIFO) | O(1) per fill |
| Cancel order | dict + linked-list node | O(1) |
| Best bid / ask | SortedDict peek | O(log n) |
| Trade log | deque(maxlen) | O(1) |

---

## Results

Measured with `run_benchmark()` on a laptop, one thread, no async, no multiprocessing, 10,000 orders:

| Metric | Value |
|---|---|
| Throughput | about 70,000 to 80,000 orders/s |
| P50 latency | about 9 to 10 µs |
| P99 latency | about 45 to 56 µs |
| Fill rate | about 97%, depends on the random order mix |
| Tests | 60 passing (40 engine, 20 API, broker and market maker) |

These move with the CPU, the Python version and whatever else the machine is doing. Run the benchmark yourself and read the table as a range. CI runs it on every push too.

---

## How it compares

| | NEXUS-TRADE | Sorted list + deque (v1) | Real exchange engine |
|---|---|---|---|
| Price-time priority | ✓ | ✓ | ✓ |
| O(1) cancel from anywhere in a queue | ✓ | ✗ | ✓ |
| Pre-trade risk checks with a reason | ✓ | ✗ | ✓ |
| Self-trade prevention | ✓ | ✗ | ✓ |
| Durable audit log | ✗ | ✗ | ✓ |
| Sub-microsecond latency | ✗ | ✗ | ✓ |

---

## Run it

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

Deploying your own copy: `render.yaml` is a Render Blueprint. Point Render at the repo and it builds, starts on the free plan, health-checks `/healthz`, and generates an `ADMIN_KEY`.

---

## Using the web terminal

Enter a name and you get a paper account.

- **B** / **S** open the order window. It shows the margin the order needs and what you have.
- **Orders** lists open orders (with Cancel) and executed ones. A rejected order says why.
- **Positions** shows quantity, average price, LTP and P&L, with **Exit** to close at market.
- **Funds** shows available and used margin, account value, and realized and unrealized P&L.
- The profile menu has **Reset account** and **Logout**.

---

## API

Account endpoints need the `X-Token` header that `/api/login` returns. FastAPI serves interactive docs at `/docs`.

| Method | Path | Login | What it does |
|---|---|---|---|
| POST | `/api/login` | | Create a paper account: `{"name": "Mahendra"}` returns a token, funds and position |
| GET | `/api/me` | ✓ | Profile, funds, position, orders and trades |
| POST | `/api/orders` | ✓ | Place an order: `{"side": "BUY", "order_type": "LIMIT", "price": 24500.5, "quantity": 25}` |
| DELETE | `/api/orders/{order_id}` | ✓ | Cancel one of your open orders |
| GET | `/api/orders` | ✓ | Your orders, newest first |
| POST | `/api/account/reset` | ✓ | Cancel your orders, clear your position, restore ₹10,00,000 |
| GET | `/api/book?depth=5` | | Bids, asks, LTP, spread, mid |
| GET | `/api/trades?limit=50` | | Market trades, newest first |
| GET | `/api/analytics` | | VWAP, realized vol, z-score, imbalance, engine stats |
| POST | `/api/reset` | admin | Wipe the market and every account. Needs `ADMIN_KEY` and an `X-Admin-Key` header |
| GET | `/healthz` | | Health check for Render |

---

## Analytics

| Signal | Formula | Use |
|---|---|---|
| VWAP | Σ(price × vol) / Σvol | The standard execution benchmark |
| Realized volatility | std(log returns) × √(252 × 390) | Annualised, treating each trade as a one-minute bar |
| Z-score | (price − mean) / std | Mean-reversion signal, carried over from earlier pairs-trading work |
| Order imbalance | (bidQty − askQty) / total, top of book | Short-horizon pressure. A heuristic, not a prediction |

---

## Repository structure

```
NEXUS-TRADE/
├── src/
│   ├── order_matching_engine.py   exchange: book, matching, analytics, demo, benchmark
│   ├── broker.py                  accounts, RMS checks, positions, P&L
│   ├── market_maker.py            simulated market maker and order flow
│   └── api.py                     FastAPI app and the background market loop
├── static/index.html              web terminal, plain HTML and JS, no build step
├── tests/
│   ├── test_engine.py             40 engine tests, including regressions
│   └── test_api.py                20 API, broker and market-maker tests
├── docs/architecture.md           design notes and pseudocode
├── .github/workflows/tests.yml    pytest and the benchmark on every push
├── render.yaml                    free hosting on Render
├── requirements.txt
└── requirements-dev.txt
```

---

## What NEXUS-TRADE will not do

- Connect to a real exchange or a real broker.
- Use real market data. NIFTY50 is the only symbol and its price is a random walk.
- Keep your account. It lives in memory and disappears on restart.
- Pretend the trade log is an audit trail. It keeps the last 1,000 trades and drops the rest.

## Limitations

- **Margin is simplified.** A flat 20% on the worst-case position at the last traded price. Real brokers use SPAN plus exposure margin and square off intraday positions at the close.
- **The engine is not thread-safe.** The web layer wraps engine, broker and market loop in one lock. Fine for a demo; real order entry needs a single-writer design.
- **The page polls** every 1.5 seconds instead of listening on a WebSocket.
- **Python sets the ceiling.** Around 75,000 orders a second is about what one Python thread does. Sub-microsecond matching needs C++ or Rust.

## Next

- [x] FastAPI and a web terminal
- [x] Paper accounts with RMS checks, positions and P&L
- [ ] WebSocket market data instead of polling
- [ ] More symbols (BANKNIFTY, RELIANCE)
- [ ] SQLite or Postgres so accounts survive a restart
- [ ] Stop-loss and good-till-date orders
- [ ] A C++ port of the engine
- [ ] FIX message parsing

---

## License

MIT. See [LICENSE](LICENSE).

---

*Built by [Mahendra Meena](https://www.linkedin.com/in/mahendra-meena-72047b201/), IIIT Gwalior. Most of an exchange is choosing the right list.*
