"""
Simulated market participants for the NEXUS-TRADE web demo.

Without other traders the book would empty after a few orders and the
price would never move. Two bots keep the market alive:

  - a market maker that, every step, cancels its quotes and re-posts five
    price levels on each side of a fair price that follows a random walk
  - order flow: small market orders from other traders, so trades print
    and the chart moves

Both use the same engine and the same rules as everyone else, including
price-time priority. A user's order resting at the same price as a fresh
quote is ahead of it in the queue.
"""

import random

from order_matching_engine import MatchingEngine, Order

LOT = 25


class MarketMaker:
    def __init__(self, engine: MatchingEngine, symbol: str, fair: float = 24_500.0,
                 tick: float = 0.05, levels: int = 5, seed=None):
        self.engine = engine
        self.symbol = symbol
        self.anchor = fair
        self.fair = fair
        self.tick = tick
        self.levels = levels
        self.rng = random.Random(seed)
        self.quotes: list[str] = []
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def _snap(self, price: float) -> float:
        return round(round(price / self.tick) * self.tick, 2)

    def requote(self) -> None:
        for oid in self.quotes:
            self.engine.cancel_order(self.symbol, oid)
        self.quotes = []
        half = self.tick * self.rng.choice([2, 3, 4, 5, 6, 8, 10])
        bid = self._snap(self.fair - half)
        ask = self._snap(self.fair + half)
        if ask <= bid:
            ask = round(bid + self.tick, 2)
        for side, start, step in (('BUY', bid, -1), ('SELL', ask, 1)):
            price = start
            for _ in range(self.levels):
                oid = self._id('MM')
                order = Order(oid, self.symbol, side, price,
                              LOT * self.rng.randint(1, 12), 'LIMIT', trader_id='MM')
                self.engine.submit_order(order)
                if order.status == 'OPEN':
                    self.quotes.append(oid)
                price = round(price + step * self.tick * self.rng.randint(1, 6), 2)

    def flow(self) -> None:
        """One small market order from another trader, now and then."""
        if self.rng.random() > 0.55:
            return
        book = self.engine.books[self.symbol]
        mid = book.mid_price() or self.fair
        # lean the flow towards the fair price so the market follows it
        p_buy = 0.5 + max(-0.25, min(0.25, (self.fair - mid) / 10))
        side = 'BUY' if self.rng.random() < p_buy else 'SELL'
        self.engine.submit_order(Order(self._id('FLOW'), self.symbol, side, None,
                                       LOT * self.rng.randint(1, 6), 'MARKET',
                                       trader_id='FLOW'))

    def step(self) -> None:
        self.fair += self.rng.gauss(0, 1.1) + (self.anchor - self.fair) * 0.003
        self.fair = max(self.anchor * 0.92, min(self.anchor * 1.08, self.fair))
        self.flow()
        self.requote()
