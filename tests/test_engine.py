"""
Unit Tests — QUANT-ME Order Matching Engine
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
