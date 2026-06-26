# Code Review Fixes — Changelog

This document lists every issue found in a code review of the original
NEXUS-TRADE implementation, why it mattered, and how it was fixed. Useful
reference if you're asked to explain these changes (e.g. in an interview).

## 1. Cancel was O(n), not O(1), despite the README claiming O(1)

**Where:** `PriceLevel.remove_by_id()` (old code)

**Bug:**
```python
def remove_by_id(self, order_id: str) -> bool:
    self.orders = deque(o for o in self.orders if o.order_id != order_id)
```
This rebuilds the *entire* queue at that price level to filter out one
order. That's O(n) where n = orders at that price level, not O(1). The
HashMap (`order_map`) only got you O(1) to find *which price level* the
order was in — actually removing it from the level was still linear.

**Fix:** Replaced the `deque`-based `PriceLevel` with a genuine doubly
linked list (`_Node` objects with `prev`/`next` pointers). `OrderBook.order_map`
now stores a direct reference to each order's node
(`order_id -> (side, price, node)`), so `cancel()` does an O(1) hashmap
lookup *and* an O(1) pointer-unlink — no scanning, no rebuilding.

**Verified:** cancelling the last order in a queue of N orders:

| N | Old (deque-rebuild) | New (DLL node unlink) |
|---|---|---|
| 100 | ~19 µs | ~8 µs |
| 1,000 | ~88 µs | ~7 µs |
| 5,000 | ~434 µs | ~4 µs |
| 20,000 | ~1,785 µs | ~5 µs |

The new version is flat regardless of queue length/position — that's what
O(1) actually looks like. The old version grew linearly — that's O(n)
wearing an O(1) label.

---

## 2. Unfilled MARKET orders polluted the order book

**Where:** `MatchingEngine._match_market()` (old code)

**Bug:**
```python
def _match_market(self, book, order):
    order.price = float('inf') if order.side == 'BUY' else 0.0
    order.order_type = 'LIMIT'
    return self._match_limit(book, order)
```
`_match_limit` always rests any unfilled remainder as a passive limit
order. So if a MARKET order couldn't be fully filled (insufficient
liquidity), the leftover quantity would get parked in the book **at
price = +inf (or 0)** — a phantom order that would corrupt `best_bid()` /
`best_ask()` for every subsequent order, since +inf/0 are not real prices.

**Fix:** `_match_limit` now takes a `rest_unfilled` parameter. MARKET
orders call it with `rest_unfilled=False`, so any unfilled remainder is
simply dropped (the order behaves as Immediate-or-Cancel / IOC, which is
standard exchange behavior for market orders with insufficient liquidity)
instead of being rested at an artificial price. The order's `order_type`
is also no longer silently mutated to `'LIMIT'`.

**Verified:** new regression tests `test_unfilled_market_order_does_not_pollute_book`
and `test_partially_filled_market_order_drops_remainder` assert
`best_bid()`/`best_ask()` stay `None` after an unfilled/partially-filled
market order, instead of becoming `inf`/`0`.

---

## 3. `total_qty` bookkeeping could drift after partial fills

**Where:** `MatchingEngine._match_limit()` (old code), interacting with
the `PriceLevel` rewrite above.

**Bug (introduced risk during the fix above):** Once `PriceLevel` owns its
own `total_qty` decrement inside `remove_front()`/`remove_node()`, the
matching loop must NOT also manually subtract `fill_qty` from
`level.total_qty` when an order is fully filled — that would double-count
the same fill. The original code's manual `level.total_qty -= fill_qty`
on every iteration was safe only because the old `remove_by_id` recomputed
`total_qty` from scratch (`sum(o.quantity for o in self.orders)`) rather
than decrementing it — i.e. the original code was internally consistent
but relied on an O(n) recompute every cancel.

**Fix:** The matching loop now only manually decrements `total_qty` on a
*partial* fill (order stays in the queue with reduced quantity). On a
*full* fill, `remove_front()` is the sole owner of subtracting that
order's quantity from `total_qty`. This keeps `total_qty` correct without
needing an O(n) recompute anywhere.

**Verified:** `test_total_qty_consistent_after_partial_fill_and_midqueue_cancel`
walks through partial fill -> mid-queue cancel -> full fill and asserts
`total_qty` is exactly correct at every step.

---

## 4. No input validation on `Order`

**Where:** `Order` dataclass (old code) had no validation at all.

**Bug:** `quantity <= 0` or `price <= 0` (for LIMIT orders) were silently
accepted, which can corrupt book state in subtle ways (e.g. a zero-qty
order matching as "filled" without actually trading anything).

**Fix:** Added `__post_init__` validation that raises `ValueError` for
non-positive quantity, and non-positive price on LIMIT orders specifically
(MARKET orders legitimately have no meaningful price).

---

## 5. `Order.__repr__` would crash on `price=None`

**Where:** `Order.__repr__()` (old code) used `f"{self.price:.2f}"`
unconditionally. The docstring says price is "ignored for MARKET orders,"
implying `None` is a valid/expected value for market orders, but the repr
would crash (`TypeError: unsupported format string passed to NoneType.__format__`)
if anyone actually passed `None`.

**Fix:** `__repr__` now handles `price is None` by printing `MKT` instead
of attempting to format it as a float.

---

## 6. README / docs overclaims

These weren't bugs, but were misleading or unverifiable claims:

- **"No libraries for the core logic"** directly contradicted the same
  README using `sortedcontainers.SortedDict` and `numpy`. Reworded to be
  accurate: core matching logic is hand-written, supporting libraries are
  used for the balanced tree and vectorized math.
- **A single cherry-picked benchmark number** (74,168 orders/sec,
  9.5µs/43.3µs latency) presented as a fixed fact. These numbers are
  machine- and run-dependent — re-running the exact same benchmark code
  produced 71,595–76,031 orders/sec across multiple runs in this review.
  Reworded as a range with an explicit note that it's environment-dependent.
- **"Same logic NSE's NEAT system runs"** is unverifiable — NEAT's
  internals are proprietary and not publicly documented. Reworded to
  "implements the standard price-time priority algorithm used by
  continuous-auction exchanges generally," which is accurate and
  defensible.
- Added a **"Known limitations"** section to the README (no concurrency
  safety, trade log is a bounded cache not an audit trail, no
  persistence) — these were true of the original code too, just not
  disclosed. Stating them up front is more credible than waiting for a
  reviewer/interviewer to find them.

---

## Test suite

22 original tests (all still pass, unmodified) + 7 new regression tests
covering the bugs above = **29 tests, all passing**.
