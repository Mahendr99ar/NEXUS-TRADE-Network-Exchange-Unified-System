"""
HTTP API and web page for the NEXUS-TRADE matching engine.

Local:  uvicorn api:app --app-dir src --reload
Then open http://127.0.0.1:8000

The engine is single-threaded, and FastAPI runs plain `def` endpoints in a
thread pool, so every call that touches the engine holds `_lock`.
"""

import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from order_matching_engine import MatchingEngine, Order, QuantAnalytics

SYMBOL = "NIFTY50"
BASE_PRICE = 24_500.0
MAX_RESTING_ORDERS = 5_000   # keeps memory bounded on a small free instance
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="NEXUS-TRADE", version="1.0.0")
_lock = threading.Lock()

engine: MatchingEngine
analytics: QuantAnalytics
recent_trades: deque


def _seed() -> None:
    """Fresh engine with five price levels on each side of BASE_PRICE."""
    global engine, analytics, recent_trades
    engine = MatchingEngine()
    analytics = QuantAnalytics(window=100)
    recent_trades = deque(maxlen=200)
    engine.add_symbol(SYMBOL)

    def record(trades):
        for t in trades:
            analytics.update(t)
            recent_trades.appendleft(t)

    engine.on_trade(record)
    for i in range(1, 6):
        engine.submit_order(Order(f"SEED-B{i}", SYMBOL, "BUY",
                                  round(BASE_PRICE - i * 0.5, 2), 100 + i * 20))
        engine.submit_order(Order(f"SEED-A{i}", SYMBOL, "SELL",
                                  round(BASE_PRICE + i * 0.5, 2), 80 + i * 15))


_seed()


class OrderIn(BaseModel):
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    quantity: int = Field(gt=0, le=1_000_000)
    price: Optional[float] = Field(default=None, gt=0, le=10_000_000)
    trader_id: Optional[str] = Field(default=None, max_length=32)


def _trade_dict(t) -> dict:
    return {"trade_id": t.trade_id, "price": t.price, "quantity": t.quantity,
            "buy_order_id": t.buy_order_id, "sell_order_id": t.sell_order_id,
            "timestamp": t.timestamp}


def _levels(side_book, depth: int) -> list[dict]:
    out = []
    for price in list(side_book.keys())[:depth]:
        lv = side_book[price]
        out.append({"price": price, "quantity": lv.total_qty,
                    "orders": lv.order_count()})
    return out


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/api/book")
def get_book(depth: int = 10):
    depth = max(1, min(depth, 50))
    with _lock:
        book = engine.books[SYMBOL]
        return {
            "symbol": SYMBOL,
            "bids": _levels(book.bids, depth),
            "asks": _levels(book.asks, depth),
            "best_bid": book.best_bid(),
            "best_ask": book.best_ask(),
            "spread": book.spread(),
            "mid": book.mid_price(),
        }


@app.post("/api/orders")
def place_order(body: OrderIn):
    if body.order_type == "LIMIT" and body.price is None:
        raise HTTPException(422, "LIMIT orders need a price")
    order_id = "WEB-" + uuid.uuid4().hex[:8].upper()
    with _lock:
        book = engine.books[SYMBOL]
        if body.order_type == "LIMIT" and len(book.order_map) >= MAX_RESTING_ORDERS:
            raise HTTPException(429, "Book is full. Cancel orders or reset the book.")
        order = Order(order_id, SYMBOL, body.side, body.price, body.quantity,
                      body.order_type, trader_id=body.trader_id or None)
        trades = engine.submit_order(order)
        filled = sum(t.quantity for t in trades)
        resting = order_id in book.order_map
    return {
        "order_id": order_id,
        "filled": filled,
        "resting": resting,
        "unfilled": body.quantity - filled,
        "trades": [_trade_dict(t) for t in trades],
    }


@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: str):
    with _lock:
        ok = engine.cancel_order(SYMBOL, order_id)
    if not ok:
        raise HTTPException(404, "No resting order with that id")
    return {"cancelled": order_id}


@app.get("/api/orders")
def resting_orders(limit: int = 50):
    """Resting orders placed from the web page (not the seed orders)."""
    limit = max(1, min(limit, 200))
    with _lock:
        out = []
        for oid, (side, price, node) in engine.books[SYMBOL].order_map.items():
            if oid.startswith("WEB-"):
                out.append({"order_id": oid, "side": side, "price": price,
                            "quantity": node.order.quantity})
        return out[-limit:]


@app.get("/api/trades")
def get_trades(limit: int = 50):
    limit = max(1, min(limit, 200))
    with _lock:
        return [_trade_dict(t) for t in list(recent_trades)[:limit]]


@app.get("/api/analytics")
def get_analytics():
    with _lock:
        book = engine.books[SYMBOL]
        return {
            "vwap": analytics.vwap(),
            "realized_vol_pct": analytics.realized_vol(),
            "z_score": analytics.z_score(),
            "imbalance": analytics.order_imbalance(book),
            "stats": engine.stats(),
        }


@app.post("/api/reset")
def reset():
    with _lock:
        _seed()
    return {"reset": True}
