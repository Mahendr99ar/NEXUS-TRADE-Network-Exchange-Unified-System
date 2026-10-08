"""Tests for the HTTP API, the broker layer, and the market maker."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from fastapi.testclient import TestClient

import api
import broker as broker_mod

# TestClient without a `with` block does not run the lifespan hook, so the
# background market loop stays off and every test sees a still market.
client = TestClient(api.app)


@pytest.fixture(autouse=True)
def fresh_market():
    api._init_market(seed=42)


def login(name='Asha'):
    r = client.post('/api/login', json={'name': name})
    assert r.status_code == 200
    return {'X-Token': r.json()['token']}


def order(headers, **body):
    r = client.post('/api/orders', json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def book():
    return client.get('/api/book').json()


# ── pages and market data ───────────────────────────────────

def test_index_page_served():
    r = client.get('/')
    assert r.status_code == 200 and 'NEXUS-TRADE' in r.text


def test_book_is_seeded_two_sided_on_tick():
    b = book()
    assert len(b['bids']) == 5 and len(b['asks']) == 5
    assert b['best_bid'] < b['best_ask']
    for lv in b['bids'] + b['asks']:
        assert abs(round(lv['price'] / 0.05) * 0.05 - lv['price']) < 1e-9


# ── accounts ────────────────────────────────────────────────

def test_login_gives_opening_funds():
    r = client.post('/api/login', json={'name': 'Asha'}).json()
    assert r['token'] and r['user']['name'] == 'Asha'
    assert r['funds']['available_margin'] == 1_000_000
    assert r['position']['quantity'] == 0


def test_endpoints_need_login():
    assert client.get('/api/me').status_code == 401
    assert client.post('/api/orders', json={'side': 'BUY', 'price': 100,
                                            'quantity': 1}).status_code == 401
    assert client.get('/api/me', headers={'X-Token': 'nope'}).status_code == 401


# ── orders and positions ────────────────────────────────────

def test_buy_fills_at_best_ask_and_opens_position():
    h = login()
    ask = book()['best_ask']
    o = order(h, side='BUY', order_type='LIMIT', price=ask, quantity=25)
    assert o['status'] == 'COMPLETE' and o['avg_price'] == ask
    me = client.get('/api/me', headers=h).json()
    assert me['position']['quantity'] == 25
    assert me['position']['avg_price'] == ask
    assert me['funds']['used_margin'] > 0


def test_limit_order_rests_and_only_owner_can_cancel():
    h, other = login('A'), login('B')
    price = round(book()['best_bid'] - 1.0, 2)
    o = order(h, side='BUY', price=price, quantity=25)
    assert o['status'] == 'OPEN' and o['pending'] == 25
    assert client.delete(f"/api/orders/{o['order_id']}", headers=other).status_code == 404
    assert client.delete(f"/api/orders/{o['order_id']}", headers=h).status_code == 200
    mine = client.get('/api/orders', headers=h).json()
    assert mine[0]['status'] == 'CANCELLED'


def test_short_selling_is_allowed_with_margin():
    h = login()
    o = order(h, side='SELL', order_type='MARKET', quantity=25)
    assert o['status'] == 'COMPLETE'
    assert client.get('/api/me', headers=h).json()['position']['quantity'] == -25


def test_insufficient_funds_rejected_with_reason():
    h = login()
    # 1,000 units * ~24,500 * 20% = ~49 lakh margin, far above 10 lakh
    o = order(h, side='BUY', order_type='MARKET', quantity=1000)
    assert o['status'] == 'REJECTED'
    assert 'Insufficient funds' in o['message']
    assert client.get('/api/me', headers=h).json()['position']['quantity'] == 0


def test_closing_order_needs_no_extra_margin():
    h = login()
    order(h, side='BUY', order_type='MARKET', quantity=150)   # ~7.35 lakh margin
    o = order(h, side='SELL', order_type='MARKET', quantity=150)
    assert o['status'] == 'COMPLETE'
    assert client.get('/api/me', headers=h).json()['position']['quantity'] == 0


def test_round_trip_realizes_spread_as_loss():
    h = login()
    b = book()
    buy = order(h, side='BUY', order_type='MARKET', quantity=25)
    sell = order(h, side='SELL', order_type='MARKET', quantity=25)
    me = client.get('/api/me', headers=h).json()
    expected = round((sell['avg_price'] - buy['avg_price']) * 25, 2)
    assert buy['avg_price'] == b['best_ask'] and sell['avg_price'] == b['best_bid']
    assert me['position']['quantity'] == 0
    assert me['funds']['realized_pnl'] == expected < 0


def test_two_users_trade_with_each_other():
    seller, buyer = login('S'), login('B')
    price = round(book()['best_ask'] - 0.05, 2)      # inside the spread
    rest = order(seller, side='SELL', price=price, quantity=25)
    assert rest['status'] == 'OPEN'
    hit = order(buyer, side='BUY', price=price, quantity=25)
    assert hit['status'] == 'COMPLETE' and hit['avg_price'] == price
    assert client.get('/api/me', headers=seller).json()['position']['quantity'] == -25
    assert client.get('/api/me', headers=buyer).json()['position']['quantity'] == 25


def test_self_trade_prevented_between_own_orders():
    h = login()
    price = round(book()['best_ask'] - 0.05, 2)
    order(h, side='SELL', price=price, quantity=25)
    o = order(h, side='BUY', price=price, quantity=25)
    assert o['filled'] == 0 and o['status'] == 'CANCELLED'
    b = book()
    assert b['best_bid'] < b['best_ask']


def test_off_tick_and_circuit_prices_rejected():
    h = login()
    o = order(h, side='BUY', price=24_400.03, quantity=25)
    assert o['status'] == 'REJECTED' and 'tick' in o['message']
    o = order(h, side='BUY', price=20_000.00, quantity=25)
    assert o['status'] == 'REJECTED' and 'circuit' in o['message']


def test_validation_errors():
    h = login()
    assert client.post('/api/orders', json={'side': 'BUY', 'quantity': 10}, headers=h).status_code == 422
    assert client.post('/api/orders', json={'side': 'BUY', 'price': 100, 'quantity': 0}, headers=h).status_code == 422
    assert client.post('/api/orders', json={'side': 'HOLD', 'price': 100, 'quantity': 1}, headers=h).status_code == 422


def test_rate_limit():
    h = login()
    price = round(book()['best_bid'] - 5, 2)
    statuses = [order(h, side='BUY', price=price, quantity=1)['status'] for _ in range(11)]
    assert statuses[:10] == ['OPEN'] * 10
    assert statuses[10] == 'REJECTED'


def test_account_reset():
    h = login()
    order(h, side='BUY', order_type='MARKET', quantity=25)
    me = client.post('/api/account/reset', headers=h).json()
    assert me['position']['quantity'] == 0 and me['orders'] == []
    assert me['funds']['available_margin'] == 1_000_000


def test_market_reset_needs_admin_key(monkeypatch):
    assert client.post('/api/reset').status_code == 403
    monkeypatch.setattr(api, 'ADMIN_KEY', 'secret')
    assert client.post('/api/reset', headers={'X-Admin-Key': 'wrong'}).status_code == 403
    assert client.post('/api/reset', headers={'X-Admin-Key': 'secret'}).status_code == 200


# ── broker maths ────────────────────────────────────────────

def test_position_avg_price_and_flip():
    acc = broker_mod.Account('X', 'x', 't')
    fill = broker_mod.Broker._apply_fill
    fill(acc, +1, 10, 100.0)
    fill(acc, +1, 10, 110.0)
    assert acc.position == 20 and acc.avg_price == 105.0
    fill(acc, -1, 5, 120.0)                       # partial close
    assert acc.position == 15 and acc.realized == 75.0
    fill(acc, -1, 25, 100.0)                      # close 15, open short 10
    assert acc.realized == 0.0                    # 75 + 15 * (100 - 105)
    assert acc.position == -10 and acc.avg_price == 100.0


# ── market maker ────────────────────────────────────────────

def test_market_maker_keeps_book_alive_and_uncrossed():
    trades_before = len(client.get('/api/trades?limit=300').json())
    for _ in range(300):
        api.maker.step()
        b = api.engine.books['NIFTY50']
        assert b.best_bid() is not None and b.best_ask() is not None
        assert b.best_bid() < b.best_ask()
    assert len(client.get('/api/trades?limit=300').json()) > trades_before


def test_inr_format():
    assert broker_mod.inr(4410090.5) == '₹44,10,090.50'
    assert broker_mod.inr(999) == '₹999.00'
    assert broker_mod.inr(1000000) == '₹10,00,000.00'
    assert broker_mod.inr(-12345.678) == '-₹12,345.68'
