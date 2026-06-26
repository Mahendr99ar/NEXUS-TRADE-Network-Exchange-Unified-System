"""
Unit Tests — NEXUS-TRADE: Order Matching Engine
Run: pytest tests/ -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from order_matching_engine import Order, OrderBook, MatchingEngine, QuantAnalytics


# ── helpers ──────────────────────────────────────────────────
def make_engine():
    e = MatchingEngine()
    e.add_symbol('TEST')
    return e

def limit(oid, side, price, qty):
    return Order(oid, 'TEST', side, price, qty, 'LIMIT')

def market(oid, side, qty):
    return Order(oid, 'TEST', side, 0, qty, 'MARKET')

def limit_trader(oid, side, price, qty, trader_id):
    return Order(oid, 'TEST', side, price, qty, 'LIMIT', trader_id=trader_id)


# ════════════════════════════════════════════════════════════
# 1. ORDER BOOK BASICS
# ════════════════════════════════════════════════════════════

def test_empty_book_no_best():
    book = OrderBook('X')
    assert book.best_bid() is None
    assert book.best_ask() is None
    assert book.spread() is None


def test_single_bid_appears():
    e = make_engine()
    e.submit_order(limit('B1', 'BUY', 100.0, 50))
    book = e.books['TEST']
    assert book.best_bid() == 100.0


def test_single_ask_appears():
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 102.0, 50))
    book = e.books['TEST']
    assert book.best_ask() == 102.0


def test_spread_calculated():
    e = make_engine()
    e.submit_order(limit('B1', 'BUY',  100.0, 50))
    e.submit_order(limit('A1', 'SELL', 102.0, 50))
    assert e.books['TEST'].spread() == 2.0


def test_best_bid_is_highest():
    """Red-Black Tree must return MAX bid first."""
    e = make_engine()
    e.submit_order(limit('B1', 'BUY', 99.0,  50))
    e.submit_order(limit('B2', 'BUY', 101.0, 50))
    e.submit_order(limit('B3', 'BUY', 100.0, 50))
    assert e.books['TEST'].best_bid() == 101.0


def test_best_ask_is_lowest():
    """Red-Black Tree must return MIN ask first."""
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 105.0, 50))
    e.submit_order(limit('A2', 'SELL', 103.0, 50))
    e.submit_order(limit('A3', 'SELL', 107.0, 50))
    assert e.books['TEST'].best_ask() == 103.0


# ════════════════════════════════════════════════════════════
# 2. MATCHING — LIMIT ORDERS
# ════════════════════════════════════════════════════════════

def test_no_match_when_prices_dont_cross():
    e = make_engine()
    e.submit_order(limit('B1', 'BUY',  100.0, 50))
    trades = e.submit_order(limit('A1', 'SELL', 101.0, 50))
    assert len(trades) == 0


def test_exact_match_single_trade():
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 50))
    trades = e.submit_order(limit('B1', 'BUY',  100.0, 50))
    assert len(trades) == 1
    assert trades[0].price    == 100.0
    assert trades[0].quantity == 50


def test_partial_fill_rest_in_book():
    """BUY 100, only 60 available at ask → 60 filled, 40 resting."""
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 60))
    trades = e.submit_order(limit('B1', 'BUY',  100.0, 100))
    assert len(trades) == 1
    assert trades[0].quantity == 60
    book = e.books['TEST']
    assert book.best_bid() == 100.0
    assert book.bids[100.0].total_qty == 40


def test_multi_level_fill():
    """BUY order sweeps multiple ask price levels."""
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 30))
    e.submit_order(limit('A2', 'SELL', 100.5, 30))
    e.submit_order(limit('A3', 'SELL', 101.0, 30))
    trades = e.submit_order(limit('B1', 'BUY', 101.0, 80))
    assert len(trades) == 3
    assert sum(t.quantity for t in trades) == 80


def test_trade_price_is_passive_side():
    """Trade executes at RESTING order's price, not aggressor's."""
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 50))
    trades = e.submit_order(limit('B1', 'BUY', 105.0, 50))   # aggressive
    assert trades[0].price == 100.0   # passive ask price, not 105


# ════════════════════════════════════════════════════════════
# 3. FIFO — PRICE-TIME PRIORITY
# ════════════════════════════════════════════════════════════

def test_fifo_within_price_level():
    """At same price, earlier order matched first."""
    e    = make_engine()
    e.submit_order(limit('A-FIRST',  'SELL', 100.0, 30))
    e.submit_order(limit('A-SECOND', 'SELL', 100.0, 30))
    trades = e.submit_order(limit('B1', 'BUY', 100.0, 30))
    assert trades[0].sell_order_id == 'A-FIRST'   # first in, first matched


# ════════════════════════════════════════════════════════════
# 4. MARKET ORDERS
# ════════════════════════════════════════════════════════════

def test_market_buy_fills_at_best_ask():
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 50))
    trades = e.submit_order(market('M1', 'BUY', 50))
    assert len(trades) == 1
    assert trades[0].price == 100.0


def test_market_sell_fills_at_best_bid():
    e = make_engine()
    e.submit_order(limit('B1', 'BUY', 99.0, 50))
    trades = e.submit_order(market('M1', 'SELL', 50))
    assert len(trades) == 1
    assert trades[0].price == 99.0


# ════════════════════════════════════════════════════════════
# 5. CANCEL — O(1) VIA HASHMAP
# ════════════════════════════════════════════════════════════

def test_cancel_existing_order():
    e = make_engine()
    e.submit_order(limit('B1', 'BUY', 100.0, 50))
    result = e.cancel_order('TEST', 'B1')
    assert result is True
    assert e.books['TEST'].best_bid() is None


def test_cancel_nonexistent_order():
    e = make_engine()
    result = e.cancel_order('TEST', 'GHOST')
    assert result is False


def test_cancel_removes_from_level():
    e = make_engine()
    e.submit_order(limit('B1', 'BUY', 100.0, 50))
    e.submit_order(limit('B2', 'BUY', 100.0, 80))
    e.cancel_order('TEST', 'B1')
    book = e.books['TEST']
    assert book.bids[100.0].total_qty == 80


# ════════════════════════════════════════════════════════════
# 6. QUANT ANALYTICS
# ════════════════════════════════════════════════════════════

def test_vwap_calculation():
    """VWAP = sum(price*qty) / sum(qty)"""
    from order_matching_engine import Trade
    import time
    qa = QuantAnalytics(window=100)
    qa.update(Trade('T1','TEST','B','A', 100.0, 50))
    qa.update(Trade('T2','TEST','B','A', 102.0, 50))
    expected_vwap = (100.0*50 + 102.0*50) / 100
    assert qa.vwap() == expected_vwap


def test_z_score_zero_at_mean():
    """Z-score should be ~0 when current price equals rolling mean."""
    from order_matching_engine import Trade
    qa = QuantAnalytics(window=20)
    for i in range(20):
        qa.update(Trade(f'T{i}','TEST','B','A', 100.0, 10))
    assert abs(qa.z_score()) < 0.01


def test_order_imbalance_range():
    """Imbalance must always be in [-1, 1]."""
    e = make_engine()
    e.submit_order(limit('B1', 'BUY',  99.0, 200))
    e.submit_order(limit('A1', 'SELL', 101.0, 50))
    qa   = QuantAnalytics()
    book = e.books['TEST']
    imb  = qa.order_imbalance(book)
    assert -1.0 <= imb <= 1.0


# ════════════════════════════════════════════════════════════
# 7. ENGINE STATS
# ════════════════════════════════════════════════════════════

def test_stats_count():
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 50))
    e.submit_order(limit('B1', 'BUY',  100.0, 50))
    s = e.stats()
    assert s['orders_received'] == 2
    assert s['trades_matched']  == 1
    assert s['total_volume']    == 50


def test_observer_callback_fires():
    """Observer pattern: callback must be called on every trade."""
    e      = make_engine()
    fired  = []
    e.on_trade(lambda trades: fired.extend(trades))
    e.submit_order(limit('A1', 'SELL', 100.0, 50))
    e.submit_order(limit('B1', 'BUY',  100.0, 50))
    assert len(fired) == 1
    assert fired[0].quantity == 50


# ════════════════════════════════════════════════════════════
# 8. REGRESSION TESTS — bugs found in code review, now fixed
# ════════════════════════════════════════════════════════════

def test_unfilled_market_order_does_not_pollute_book():
    """BUG (fixed): an unfilled MARKET order used to fall through to
    add_passive() and rest in the book at price=+inf/0, corrupting
    best_bid/best_ask for all future orders. A MARKET order must behave
    as Immediate-or-Cancel: unfilled remainder is dropped, never rested."""
    e = make_engine()
    trades = e.submit_order(market('M1', 'BUY', 100))  # no liquidity at all
    assert trades == []
    book = e.books['TEST']
    assert book.best_bid() is None
    assert book.best_ask() is None


def test_partially_filled_market_order_drops_remainder():
    """Only part of a MARKET order can be filled -> the rest must be
    dropped (IOC), not rested as a phantom limit order."""
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 30))
    trades = e.submit_order(market('M1', 'BUY', 100))   # only 30 available
    assert len(trades) == 1
    assert trades[0].quantity == 30
    book = e.books['TEST']
    assert book.best_bid() is None   # the leftover 70 must NOT be resting


def test_cancel_from_middle_of_queue_is_correct():
    """BUG (fixed): PriceLevel.remove_by_id() used to rebuild the entire
    deque (O(n)) to cancel from the middle of a FIFO queue. It now uses a
    true doubly linked list with direct node references, so cancelling
    order X2 out of [X1, X2, X3] must leave X1 and X3 in original FIFO
    order with no trace of X2."""
    e = make_engine()
    e.submit_order(limit('X1', 'SELL', 50.0, 10))
    e.submit_order(limit('X2', 'SELL', 50.0, 10))
    e.submit_order(limit('X3', 'SELL', 50.0, 10))
    assert e.cancel_order('TEST', 'X2') is True
    trades = e.submit_order(limit('Y1', 'BUY', 50.0, 20))
    assert [t.sell_order_id for t in trades] == ['X1', 'X3']


def test_total_qty_consistent_after_partial_fill_and_midqueue_cancel():
    """BUG (fixed): total_qty used to be decremented both manually in the
    matching loop AND again inside remove_front()/remove_by_id(), causing
    drift after a sequence of partial fills + cancels. Verifies the
    level's total_qty stays exactly correct throughout."""
    e = make_engine()
    e.submit_order(limit('A1', 'SELL', 100.0, 50))
    e.submit_order(limit('A2', 'SELL', 100.0, 70))
    e.submit_order(limit('A3', 'SELL', 100.0, 30))
    book = e.books['TEST']
    assert book.asks[100.0].total_qty == 150

    e.submit_order(limit('B1', 'BUY', 100.0, 20))  # partial fill of A1
    assert book.asks[100.0].total_qty == 130

    e.cancel_order('TEST', 'A2')  # cancel from middle of queue
    assert book.asks[100.0].total_qty == 60

    e.submit_order(limit('B2', 'BUY', 100.0, 60))  # fills remainder of A1 + all of A3
    assert 100.0 not in book.asks  # level fully drained and cleaned up


def test_zero_quantity_order_rejected():
    """BUG (fixed): orders with quantity <= 0 used to be accepted silently
    and would corrupt book state. Now rejected at construction."""
    import pytest
    with pytest.raises(ValueError):
        limit('BAD', 'BUY', 100.0, 0)


def test_negative_price_limit_order_rejected():
    """BUG (fixed): a LIMIT order with a non-positive price used to be
    accepted silently. Now rejected at construction."""
    import pytest
    with pytest.raises(ValueError):
        limit('BAD', 'BUY', -5.0, 10)


def test_market_order_repr_does_not_crash():
    """BUG (fixed): Order.__repr__ called f'{self.price:.2f}' unconditionally,
    which crashes if price is None (as the docstring implies is valid for
    MARKET orders). repr() must not crash regardless of price."""
    o = Order('M1', 'TEST', 'BUY', None, 50, 'MARKET')
    assert 'MKT' in repr(o)


# ════════════════════════════════════════════════════════════
# 9. SELF-TRADE PREVENTION (STP)
# ════════════════════════════════════════════════════════════

def test_self_trade_is_prevented_skips_to_next_order():
    """GAP (fixed): the engine previously had no concept of trader
    identity, so a trader's own resting order could match their own
    incoming order ('wash trade'). With trader_id set, a same-trader match
    must be skipped and the next order in the queue matched instead."""
    e = make_engine()
    e.submit_order(limit_trader('A-SELL', 'SELL', 100.0, 50, 'trader_A'))
    e.submit_order(limit_trader('B-SELL', 'SELL', 100.0, 50, 'trader_B'))

    trades = e.submit_order(limit_trader('A-BUY', 'BUY', 100.0, 50, 'trader_A'))

    assert len(trades) == 1
    assert trades[0].sell_order_id == 'B-SELL'

    remaining = list(e.books['TEST'].asks[100.0])
    assert len(remaining) == 1
    assert remaining[0].order_id == 'A-SELL'


def test_self_trade_prevention_leaves_own_order_resting_untouched():
    e = make_engine()
    e.submit_order(limit_trader('C-SELL', 'SELL', 100.0, 50, 'trader_C'))
    trades = e.submit_order(limit_trader('C-BUY', 'BUY', 100.0, 50, 'trader_C'))

    assert trades == []
    assert 100.0 in e.books['TEST'].asks
    assert e.books['TEST'].asks[100.0].peek_front().order_id == 'C-SELL'


def test_orders_without_trader_id_are_unaffected_by_stp():
    """Backward compatibility: trader_id is optional. Orders that don't
    set it (matching every pre-existing test and the demo/benchmark code)
    must behave exactly as before STP was added -- no skip logic engaged."""
    e = make_engine()
    e.submit_order(limit('X1', 'SELL', 100.0, 50))
    trades = e.submit_order(limit('X2', 'BUY', 100.0, 50))
    assert len(trades) == 1
    assert trades[0].sell_order_id == 'X1'


def test_stp_does_not_break_fifo_for_other_traders():
    """Self-trade prevention must only skip the colliding trader's order,
    not disturb FIFO ordering among the other resting orders."""
    e = make_engine()
    e.submit_order(limit_trader('T1', 'SELL', 100.0, 20, 'trader_B'))
    e.submit_order(limit_trader('T2', 'SELL', 100.0, 20, 'trader_A'))  # would self-trade
    e.submit_order(limit_trader('T3', 'SELL', 100.0, 20, 'trader_B'))

    trades = e.submit_order(limit_trader('BUY1', 'BUY', 100.0, 40, 'trader_A'))
    # Should fill T1 (20) then skip T2 (own), then fill T3 (20)
    assert [t.sell_order_id for t in trades] == ['T1', 'T3']
