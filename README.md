# QUANT-ME — Order Matching Engine

> A high-performance, in-memory order matching engine built from scratch in Python,
> replicating the core infrastructure of NSE/BSE trading systems.

**By Mahendra Meena | IIIT Gwalior | B.Tech EEE 2027**

---

## Benchmark Results

| Metric | Value |
|--------|-------|
| Throughput | **74,168 orders/sec** |
| Median Latency (P50) | **9.5 µs** |
| P99 Latency | **43.3 µs** |
| Trades Matched (10K test) | **9,819 / 10,100** |
| Fill Rate | **97.2%** |

---

## What This Does

QUANT-ME is a **Price-Time Priority** order matching engine — the same algorithm
used by every major stock exchange (NSE, BSE, NYSE). It:

- Accepts **LIMIT** and **MARKET** orders
- Matches buyer and seller orders in real time
- Maintains a live **two-sided order book** (bids + asks)
- Supports **O(1) order cancellation**
- Computes real-time **quant analytics** (VWAP, Realized Volatility, Z-score, Order Imbalance)

---

## Data Structures Used

```
Order Book
├── Bids  → SortedDict (Red-Black Tree) — O(log n) insert/lookup, max-first
│   └── Each price level → deque (Doubly Linked List) — O(1) FIFO matching
└── Asks  → SortedDict (Red-Black Tree) — O(log n) insert/lookup, min-first
    └── Each price level → deque (Doubly Linked List) — O(1) FIFO matching

Order Map   → dict (HashMap)      — O(1) cancel by order ID
Trade Log   → deque(maxlen=1000)  — Circular buffer, O(1) append
```

| Operation | Data Structure | Time Complexity |
|-----------|---------------|-----------------|
| Insert order | Red-Black Tree | O(log n) |
| Match order | Doubly Linked List | O(1) per fill |
| Cancel order | HashMap | O(1) |
| Best bid/ask | Red-Black Tree peek | O(log n) |
| Trade log append | Circular Buffer | O(1) |

---

## Project Structure

```
quant-me/
├── src/
│   └── order_matching_engine.py   # Core engine (all-in-one)
├── tests/
│   └── test_engine.py             # Unit tests (pytest)
├── docs/
│   └── architecture.md            # Deep-dive design doc
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/quant-me.git
cd quant-me

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run live demo
python src/order_matching_engine.py

# 4. Run tests
pytest tests/ -v
```

---

## Live Demo Output

```
════════════════════════════════════════════
  QUANT-ME ORDER MATCHING ENGINE — LIVE DEMO
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

## Quant Analytics Module

Built on top of my **Pairs Trading** (Sharpe: 1.8) and **NIFTY Volatility Prediction** work:

| Signal | Formula | Use Case |
|--------|---------|----------|
| VWAP | Σ(price × vol) / Σvol | Execution benchmark |
| Realized Volatility | std(log returns) × √252 | Risk estimation |
| Z-score | (price − mean) / std | Mean-reversion signal |
| Order Imbalance | (bidQty − askQty) / total | Short-term price predictor |

---

## Algorithm — Price-Time Priority

```
Incoming BUY order at price P:
  1. Check best ask price (O(log n) — Red-Black Tree peek)
  2. If best_ask <= P:
       Fill from front of ask queue (O(1) — FIFO deque)
       Repeat until order filled OR no more matching asks
  3. Remaining qty → resting limit order added to bid book
```

This is identical to how NSE's NEAT system processes orders.

---

## Skills Demonstrated

- **DSA**: Red-Black Tree, Doubly Linked List, HashMap, Circular Buffer
- **OOP**: Abstract base classes, Observer pattern, Dataclasses
- **OS**: Concurrent-safe design, nanosecond timing with `perf_counter_ns`
- **Quant Finance**: VWAP, Realized Vol, Z-score, Order Book Imbalance
- **System Design**: Separation of concerns, callback/observer architecture

---

## Roadmap

- [ ] WebSocket API (FastAPI) for live order submission
- [ ] Multi-symbol support (BANKNIFTY, RELIANCE, etc.)
- [ ] C++ port for sub-microsecond latency
- [ ] FIX protocol message parsing
- [ ] Persistent order log with SQLite

---

## Author

**Mahendra Meena**
B.Tech EEE, IIIT Gwalior (2023–2027)
[LinkedIn](https://linkedin.com) | [GitHub](https://github.com)

*NK Securities Quant Hackathon 2025 — Top 6% (356/6000+)*
*Summer of Quant 2025, IIT Kharagpur*
