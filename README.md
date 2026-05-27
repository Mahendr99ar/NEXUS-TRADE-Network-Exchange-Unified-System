# NEXUS-TRADE — Order Matching Engine
 
> Built from scratch in Python. No libraries for the core logic — just raw data structures doing exactly what NSE's NEAT system does under the hood.
 
**Mahendra Meena | IIIT Gwalior | B.Tech EEE 2027**
 
---
 
I got curious about how stock exchanges actually work — not the trading part, but the infrastructure. What happens in the milliseconds between you clicking "Buy" and the order getting filled? That curiosity turned into this project.
 
Started it under the name **VELOX** (Latin for "fast") — the whole goal was speed. Somewhere along the way it grew into something bigger, so I renamed it NEXUS-TRADE to reflect what it actually is: a network exchange system, not just a latency experiment.
 
Turns out it's a very interesting DSA problem.
 
---
 
## Numbers
 
| Metric | Value |
|--------|-------|
| Throughput | 74,168 orders/sec |
| P50 Latency | 9.5 µs |
| P99 Latency | 43.3 µs |
| Fill Rate | 97.2% (9,819 / 10,100 trades on 10K test) |
 
Running on a regular laptop. No async tricks, no multiprocessing — just the right data structures chosen for the right reasons.
 
---
 
## What it does
 
NEXUS-TRADE implements **Price-Time Priority matching** — the same algorithm every major exchange (NSE, BSE, NYSE) runs. You submit a BUY or SELL order, and the engine:
 
1. Checks if there's a matching order on the other side
2. Fills as much as possible, FIFO within each price level
3. Parks whatever's left as a resting limit order
4. Logs the trade and updates analytics in real time
Supports LIMIT and MARKET orders, live two-sided order book, O(1) cancellation, and a quant analytics layer on top (VWAP, Realized Vol, Z-score, Order Imbalance).
 
---
 
## Why these data structures
 
This was the actual interesting part to figure out.
 
```
Order Book
├── Bids  → SortedDict (Red-Black Tree) — max-first, O(log n)
│   └── Each price level → deque — O(1) FIFO matching
└── Asks  → SortedDict (Red-Black Tree) — min-first, O(log n)
    └── Each price level → deque — O(1) FIFO matching
 
Order Map → dict (HashMap) — O(1) cancel by order ID
Trade Log → deque(maxlen=1000) — circular buffer
```
 
The exchange needs to answer "what's the best bid/ask right now?" millions of times per second. A plain list would be O(n) for both insert and lookup. A Red-Black Tree gives O(log n) — for 10,000 price levels that's the difference between 10,000 operations and 14.
 
Cancel rates in live trading can be 90%+ of all messages. O(n) cancel (scanning every price level) would kill throughput. The HashMap gives O(1) — direct jump to the order, done.
 
See [`docs/architecture.md`](docs/architecture.md) for the full breakdown with pseudocode.
 
| Operation | Structure | Complexity |
|-----------|-----------|------------|
| Insert order | Red-Black Tree | O(log n) |
| Match order | Deque (FIFO) | O(1) per fill |
| Cancel order | HashMap | O(1) |
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
 
  ✅ TRADE #001 | 95 @ 24500.50 | ID: CAA68D6F
  ✅ TRADE #002 | 105 @ 24501.00 | ID: 6A600B80
  ✅ TRADE #003 | 60 @ 24499.50 | ID: B75FB6AC
 
  VWAP: 24500.47   RealizedVol: 1.28%   Z-score: 0.000   Imbalance: 0.846
 
  Throughput : 74,168 orders/sec
  P50 latency: 9.5 µs
  P99 latency: 43.3 µs
```
 
---
 
## Analytics layer
 
The signals here connect directly to earlier work I did on Pairs Trading (Sharpe: 1.8) and NIFTY Volatility Prediction:
 
| Signal | Formula | Why it matters |
|--------|---------|----------------|
| VWAP | Σ(price × vol) / Σvol | Standard execution benchmark for institutions |
| Realized Volatility | std(log returns) × √252 | Same computation as Black-Scholes sigma input |
| Z-score | (price − mean) / std | Mean-reversion trigger — same signal from the pairs trading work |
| Order Imbalance | (bidQty − askQty) / total | Predicts next ~10s price direction at ~60-65% accuracy (per academic literature) |
 
---
 
## Project structure
 
```
NEXUS-TRADE/
├── src/
│   └── order_matching_engine.py   # core engine
├── tests/
│   └── test_engine.py             # pytest suite
├── docs/
│   └── architecture.md            # detailed design notes
├── requirements.txt
└── README.md
```
 
---
 
## What's next
 
- [ ] WebSocket API via FastAPI — so you can actually submit orders over a connection
- [ ] Multi-symbol support (BANKNIFTY, RELIANCE, etc.)
- [ ] C++ port — Python gets you to ~75K orders/sec, C++ should hit sub-microsecond
- [ ] FIX protocol parsing
- [ ] SQLite persistence for the order log
---
 
**Mahendra Meena** — [LinkedIn](https://www.linkedin.com/in/mahendra-meena-72047b201/?lipi=urn%3Ali%3Apage%3Ad_flagship3_profile_view_base_contact_details%3BiaoO9%2FdjRKWOhaWxs1eueg%3D%3D)
