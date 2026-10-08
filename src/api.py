"""
HTTP API and web page for NEXUS-TRADE.

Local:  uvicorn api:app --app-dir src --reload
Then open http://127.0.0.1:8000

Layers:
  order_matching_engine.py  the exchange: order book and matching
  broker.py                 accounts, margin check (RMS), positions, P&L
  market_maker.py           simulated traders that keep the market moving
  api.py (this file)        HTTP endpoints and the background market loop

The engine is single-threaded. FastAPI runs plain `def` endpoints in a
thread pool and the market loop runs in its own thread, so everything that
touches the engine or the accounts holds `_lock`.
"""

import logging
import os
import threading
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from broker import Broker, RejectedOrder
from market_maker import MarketMaker
from order_matching_engine import MatchingEngine, QuantAnalytics

SYMBOL = "NIFTY50"
TICK = 0.05
PREV_CLOSE = 24_500.0
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MARKET_STEP_SECONDS = 1.0
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

log = logging.getLogger("nexus")
_lock = threading.Lock()

engine: MatchingEngine
analytics: QuantAnalytics
market_trades: deque
broker: Broker
maker: MarketMaker


def _ltp() -> Optional[float]:
    if market_trades:
        return market_trades[0].price
    return engine.books[SYMBOL].mid_price()


def _init_market(seed=None) -> None:
    """Fresh engine, empty accounts, and a seeded book."""
    global engine, analytics, market_trades, broker, maker
    engine = MatchingEngine()
    engine.add_symbol(SYMBOL, tick_size=TICK, verbose=False)
    analytics = QuantAnalytics(window=100)
    market_trades = deque(maxlen=300)

    def record(trades):
        for t in trades:
            analytics.update(t)
            market_trades.appendleft(t)

    engine.on_trade(record)
    broker = Broker(engine, SYMBOL, _ltp)
    maker = MarketMaker(engine, SYMBOL, fair=PREV_CLOSE, tick=TICK, seed=seed)
    maker.requote()


_init_market(seed=42)


def _market_loop(stop: threading.Event) -> None:
    while not stop.wait(MARKET_STEP_SECONDS):
        try:
            with _lock:
                maker.step()
        except Exception:                      # keep the market alive
            log.exception("market step failed")


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    thread = None
    if os.environ.get("NEXUS_MARKET_BOT", "1") != "0":
        thread = threading.Thread(target=_market_loop, args=(stop,), daemon=True)
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=2)


app = FastAPI(title="NEXUS-TRADE", version="2.0.0", lifespan=lifespan)


# ── helpers ─────────────────────────────────────────────────
def _account(token: Optional[str]):
    acc = broker.get(token)
    if acc is None:
        raise HTTPException(401, "Not logged in, or the server restarted. Log in again.")
    return acc


def _order_dict(acc, o) -> dict:
    return {
        "order_id": o.order_id, "side": o.side, "order_type": o.order_type,
        "product": "MIS", "symbol": o.symbol,
        "price": o.price, "quantity": o.original_qty, "filled": o.filled_qty,
        "pending": o.quantity if o.status == "OPEN" else 0,
        "avg_price": broker.avg_fill_price(acc, o),
        "status": o.status, "message": o.message, "timestamp": o.timestamp,
    }


def _trade_dict(t) -> dict:
    return {"trade_id": t.trade_id, "price": t.price, "quantity": t.quantity,
            "timestamp": t.timestamp}


def _levels(side_book, depth: int) -> list[dict]:
    out = []
    for price in list(side_book.keys())[:depth]:
        lv = side_book[price]
        out.append({"price": price, "quantity": lv.total_qty, "orders": lv.order_count()})
    return out


def _me(acc) -> dict:
    ltp = _ltp()
    pos = acc.position
    unrealized = pos * (ltp - acc.avg_price) if pos and ltp else 0.0
    return {
        "user": {"id": acc.user_id, "name": acc.name, "since": acc.created},
        "funds": broker.funds(acc),
        "position": {
            "symbol": SYMBOL, "product": "MIS", "quantity": pos,
            "avg_price": round(acc.avg_price, 2), "ltp": ltp,
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(acc.realized, 2),
            "pnl": round(acc.realized + unrealized, 2),
        },
        "orders": [_order_dict(acc, o) for o in reversed(acc.orders)],
        "trades": list(reversed(acc.trades)),
    }


# ── models ──────────────────────────────────────────────────
class LoginIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class OrderIn(BaseModel):
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    quantity: int = Field(gt=0, le=100_000)
    price: Optional[float] = Field(default=None, gt=0, le=10_000_000)


# ── pages ───────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


# ── public market data ──────────────────────────────────────
@app.get("/api/book")
def get_book(depth: int = 5):
    depth = max(1, min(depth, 20))
    with _lock:
        book = engine.books[SYMBOL]
        return {
            "symbol": SYMBOL, "tick_size": TICK, "prev_close": PREV_CLOSE,
            "ltp": _ltp(),
            "bids": _levels(book.bids, depth), "asks": _levels(book.asks, depth),
            "best_bid": book.best_bid(), "best_ask": book.best_ask(),
            "spread": book.spread(), "mid": book.mid_price(),
        }


@app.get("/api/trades")
def get_trades(limit: int = 50):
    limit = max(1, min(limit, 300))
    with _lock:
        return [_trade_dict(t) for t in list(market_trades)[:limit]]


@app.get("/api/analytics")
def get_analytics():
    with _lock:
        return {
            "vwap": analytics.vwap(),
            "realized_vol_pct": analytics.realized_vol(),
            "z_score": analytics.z_score(),
            "imbalance": analytics.order_imbalance(engine.books[SYMBOL]),
            "stats": engine.stats(),
        }


# ── accounts ────────────────────────────────────────────────
@app.post("/api/login")
def login(body: LoginIn):
    """Create a paper-trading account. Returns a token for the
    X-Token header. Accounts live in memory until the server restarts."""
    with _lock:
        try:
            acc = broker.create_account(body.name)
        except RejectedOrder as exc:
            raise HTTPException(503, str(exc))
        return {"token": acc.token, **_me(acc)}


@app.get("/api/me")
def me(x_token: Optional[str] = Header(default=None)):
    with _lock:
        return _me(_account(x_token))


@app.post("/api/account/reset")
def reset_account(x_token: Optional[str] = Header(default=None)):
    with _lock:
        acc = _account(x_token)
        broker.reset_account(acc)
        return _me(acc)


# ── orders ──────────────────────────────────────────────────
@app.post("/api/orders")
def place_order(body: OrderIn, x_token: Optional[str] = Header(default=None)):
    if body.order_type == "LIMIT" and body.price is None:
        raise HTTPException(422, "LIMIT orders need a price")
    with _lock:
        acc = _account(x_token)
        order = broker.place(acc, body.side, body.order_type, body.quantity, body.price)
        return _order_dict(acc, order)


@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: str, x_token: Optional[str] = Header(default=None)):
    with _lock:
        acc = _account(x_token)
        if not broker.cancel(acc, order_id):
            raise HTTPException(404, "No open order with that id in your account")
    return {"cancelled": order_id}


@app.get("/api/orders")
def my_orders(x_token: Optional[str] = Header(default=None)):
    with _lock:
        acc = _account(x_token)
        return [_order_dict(acc, o) for o in reversed(acc.orders)]


# ── admin ───────────────────────────────────────────────────
@app.post("/api/reset", include_in_schema=False)
def reset(x_admin_key: Optional[str] = Header(default=None)):
    """Wipe the market and every account. Needs the ADMIN_KEY env var."""
    if not ADMIN_KEY or x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "Forbidden")
    with _lock:
        _init_market()
    return {"reset": True}
