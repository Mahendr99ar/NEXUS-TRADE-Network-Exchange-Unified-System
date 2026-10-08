"""Tests for the HTTP API in src/api.py."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from fastapi.testclient import TestClient

import api

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def fresh_book():
    client.post('/api/reset')


def test_index_page_served():
    r = client.get('/')
    assert r.status_code == 200
    assert 'NEXUS-TRADE' in r.text


def test_book_is_seeded():
    b = client.get('/api/book').json()
    assert b['best_bid'] == 24499.5
    assert b['best_ask'] == 24500.5
    assert b['spread'] == 1.0
    assert len(b['bids']) == 5 and len(b['asks']) == 5


def test_crossing_limit_order_trades_at_resting_price():
    r = client.post('/api/orders', json={'side': 'BUY', 'order_type': 'LIMIT',
                                         'price': 24501.0, 'quantity': 50}).json()
    assert r['filled'] == 50
    assert r['trades'][0]['price'] == 24500.5
    assert r['resting'] is False


def test_limit_order_rests_and_can_be_cancelled():
    r = client.post('/api/orders', json={'side': 'BUY', 'price': 24490.0,
                                         'quantity': 10}).json()
    assert r['resting'] is True and r['filled'] == 0
    assert any(o['order_id'] == r['order_id'] for o in client.get('/api/orders').json())
    assert client.delete(f"/api/orders/{r['order_id']}").status_code == 200
    assert client.delete(f"/api/orders/{r['order_id']}").status_code == 404


def test_market_order_drops_unfilled_remainder():
    r = client.post('/api/orders', json={'side': 'SELL', 'order_type': 'MARKET',
                                         'quantity': 100_000}).json()
    assert r['resting'] is False
    assert r['unfilled'] == 100_000 - r['filled']
    assert client.get('/api/book').json()['best_bid'] is None


def test_validation_errors():
    assert client.post('/api/orders', json={'side': 'BUY', 'quantity': 10}).status_code == 422
    assert client.post('/api/orders', json={'side': 'BUY', 'price': 100, 'quantity': 0}).status_code == 422
    assert client.post('/api/orders', json={'side': 'HOLD', 'price': 100, 'quantity': 1}).status_code == 422


def test_trades_and_analytics_update():
    client.post('/api/orders', json={'side': 'BUY', 'price': 24501.0, 'quantity': 50})
    assert len(client.get('/api/trades').json()) == 1
    a = client.get('/api/analytics').json()
    assert a['vwap'] == 24500.5
    assert a['stats']['trades_matched'] == 1
