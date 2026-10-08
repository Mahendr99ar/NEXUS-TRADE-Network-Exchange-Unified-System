# Changelog

## Paper trading, broker layer, and live market

- **Accounts.** `POST /api/login` creates a paper account with ₹10,00,000. The web page has a login screen and a profile menu with funds, Reset account, and Logout.
- **Broker layer** (`src/broker.py`). Before an order reaches the engine it is checked for margin (intraday MIS, 20%, so 5x), price band (±10% of LTP), tick size, open-order limit (50), and order rate (10 a second). A failed check gives a REJECTED order with the reason. Short selling is allowed when there is margin for it.
- **Positions and P&L.** Every fill updates the position, average price, and realized and unrealized P&L. The page shows Positions (with an Exit button), Funds, open and executed orders, and the user's own trades.
- **Simulated traders** (`src/market_maker.py`). A market maker re-quotes five levels a side every second around a random-walk fair price, and small market orders from other traders print trades, so the price moves and the book never runs dry.
- **Security.** Cancelling needs the token of the account that placed the order. `POST /api/reset` needs the `ADMIN_KEY` environment variable.
- 20 API, broker, and market-maker tests in `tests/test_api.py`.

## Engine fixes

### Self-trade prevention only skipped one order

The old loop skipped the order at the front of the level and then matched the next one without checking its trader. With two orders from the same trader at the front, the second one self-traded. The loop now skips every own order in the level and moves on to the next price level.

**Test:** `test_stp_skips_every_own_order_not_just_the_first`, `test_stp_moves_on_to_next_price_level`.

### Self-trade prevention could cross the book

When the only crossing orders belonged to the same trader, matching stopped and the incoming LIMIT order rested anyway, leaving best bid above best ask (spread -1 in a reproduction). A remainder that would cross is now cancelled with a message instead.

**Test:** `test_stp_never_leaves_a_crossed_book`.

### Float prices created duplicate levels

`0.1 + 0.2` and `0.3` became two price levels. Limit prices are now normalized before use as keys, and with a tick size set, off-tick prices are rejected.

**Test:** `test_float_prices_share_one_level`, `test_tick_size_rejects_off_tick_price`.

### Order status

`Order` now has `status`, `filled_qty`, `original_qty`, and `message`, so the caller can see what happened to an order after it was submitted. Invalid sides and order types are rejected at construction.

## Web API and hosting

- Added `src/api.py`, a FastAPI app with endpoints for the book, order entry, cancel, trades, analytics, and reset. One lock serialises access to the engine.
- Added `static/index.html`, a web page showing the order book, an order form, the trade tape, and analytics. Plain HTML and JS.
- Added `render.yaml` for free hosting on Render, and a GitHub Actions workflow that runs the tests on every push.
- Split dependencies into `requirements.txt` (runtime) and `requirements-dev.txt` (adds pytest and httpx).
- Added 7 API tests in `tests/test_api.py`.

## Self-trade prevention

Orders can carry an optional `trader_id`. If an incoming order would match a resting order with the same `trader_id`, the resting order is skipped and the next order at that price is tried. Orders without a `trader_id` behave as before. Four tests cover it, including one checking that FIFO order among other traders is unchanged.

## Fixes

### 1. Cancel was O(n), not O(1)

`PriceLevel.remove_by_id()` cancelled by rebuilding the whole queue:

```python
def remove_by_id(self, order_id: str) -> bool:
    self.orders = deque(o for o in self.orders if o.order_id != order_id)
```

The `order_map` lookup found the right price level in O(1), but removing the order from that level was still linear in the number of orders there.

**Fix:** `PriceLevel` is now a doubly linked list of `_Node` objects. `order_map` stores `order_id -> (side, price, node)`, so `cancel()` is an O(1) lookup followed by an O(1) unlink.

Time to cancel the last order in a queue of N orders:

| N | Old (deque rebuild) | New (node unlink) |
|---|---|---|
| 100 | ~19 µs | ~8 µs |
| 1,000 | ~88 µs | ~7 µs |
| 5,000 | ~434 µs | ~4 µs |
| 20,000 | ~1,785 µs | ~5 µs |

The new times stay flat as N grows. The old ones grew linearly.

### 2. Unfilled MARKET orders rested in the book

```python
def _match_market(self, book, order):
    order.price = float('inf') if order.side == 'BUY' else 0.0
    order.order_type = 'LIMIT'
    return self._match_limit(book, order)
```

`_match_limit` rests any unfilled quantity. A MARKET order that couldn't fully fill would therefore rest at +inf (BUY) or 0 (SELL), and that phantom order would then be returned by `best_bid()` or `best_ask()` for every later order.

**Fix:** `_match_limit` takes a `rest_unfilled` flag, and MARKET orders pass `False`. The unfilled part is dropped, which makes MARKET orders Immediate-or-Cancel. `order_type` is no longer changed to `'LIMIT'`.

**Tests:** `test_unfilled_market_order_does_not_pollute_book` and `test_partially_filled_market_order_drops_remainder`.

### 3. `total_qty` could be double-counted

After the linked-list change, unlinking a node subtracts that order's quantity from `level.total_qty`. The matching loop also subtracted `fill_qty` on every fill, so a full fill would have been counted twice. The old code got away with this because `remove_by_id` recomputed `total_qty` from scratch with an O(n) sum.

**Fix:** the loop subtracts only on a partial fill. On a full fill, unlinking the node does the subtraction.

**Test:** `test_total_qty_consistent_after_partial_fill_and_midqueue_cancel` runs a partial fill, a mid-queue cancel, and a full fill, and checks `total_qty` after each step.

### 4. `Order` accepted invalid input

`quantity <= 0`, or `price <= 0` on a LIMIT order, was accepted silently. A zero-quantity order could "fill" without trading anything.

**Fix:** `__post_init__` raises `ValueError` for these cases. MARKET orders may still have no price.

### 5. `Order.__repr__` crashed when `price` was `None`

`__repr__` always formatted `f"{self.price:.2f}"`, which raises `TypeError` when `price` is `None`. That is a valid value for MARKET orders.

**Fix:** `None` prints as `MKT`.

### 6. README corrections

- "No libraries for the core logic" contradicted the use of `sortedcontainers` and `numpy`. The README now says the matching logic is hand-written and those two libraries do supporting work.
- One benchmark run (74,168 orders/sec, 9.5 µs / 43.3 µs) had been presented as a fixed number. Repeat runs of the same code gave 71,595–76,031 orders/sec, so the README now gives ranges.
- "Same logic NSE's NEAT system runs" can't be verified, because NEAT's internals aren't public. It now says the engine implements the standard price-time priority algorithm.
- Added a "Known limitations" section covering concurrency, the bounded trade log, and persistence.
- `SortedDict` was described as a red-black tree. It is a sorted list of sublists, so the docs now say that.
- The realized volatility formula in the docs said √252, but the code uses √(252 × 390). The docs now match the code.

## Tests

40 engine tests and 20 API, broker, and market-maker tests, 60 in total, all passing.
