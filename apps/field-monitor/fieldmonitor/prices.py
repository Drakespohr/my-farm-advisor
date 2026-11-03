"""Crop prices for the News page: corn, soybeans, wheat.

Every stored price carries its source, kind (futures or cash), and timestamp, and the UI
shows them, so a manually entered elevator bid is never mistaken for a live quote.

Providers:
- manual: an admin enters prices in the app or with `fieldmonitor set-price` / CSV import
  (e.g. your local elevator's cash bids). Always available.
- barchart: delayed CBOT futures via Barchart OnDemand `getQuote` when BARCHART_API_KEY is set.
  Exchange data is licensed; use a plan that allows display in your app. The response format
  was written from Barchart's documentation without a key to test against, so check the first
  pull. Other licensed feeds (DTN, CME DataMine) can be added as providers the same way.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import threading
import time
from typing import Any

import httpx

from .accounts import Accounts, now

log = logging.getLogger(__name__)

COMMODITIES = {
    "corn": {"name": "Corn", "unit": "bu", "futures": "ZC*1"},
    "soybeans": {"name": "Soybeans", "unit": "bu", "futures": "ZS*1"},
    "wheat": {"name": "Wheat", "unit": "bu", "futures": "ZW*1"},
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
  id INTEGER PRIMARY KEY AUTOINCREMENT, commodity TEXT NOT NULL, price REAL NOT NULL, unit TEXT NOT NULL,
  change REAL, kind TEXT NOT NULL, contract TEXT, source TEXT NOT NULL, as_of TEXT NOT NULL, created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_by_c ON prices(commodity, as_of);
"""


class Prices:
    def __init__(self, accounts: Accounts) -> None:
        self.accounts = accounts
        with accounts.db() as c:
            c.executescript(SCHEMA)

    def add(self, commodity: str, price: float, *, change: float | None = None, kind: str = "cash",
            source: str = "Manual entry", contract: str | None = None, as_of: str | None = None, unit: str = "bu") -> None:
        if commodity not in COMMODITIES:
            raise ValueError(f"commodity must be one of {', '.join(COMMODITIES)}")
        if not (0 < price < 100):
            raise ValueError("Enter the price in dollars per bushel (e.g. 4.32).")
        if kind not in ("cash", "futures"):
            raise ValueError("kind must be cash or futures")
        with self.accounts.db() as c:
            if change is None:  # derive the change from the previous price of the same kind
                prev = c.execute("SELECT price FROM prices WHERE commodity=? AND kind=? ORDER BY as_of DESC, id DESC LIMIT 1",
                                 (commodity, kind)).fetchone()
                change = round(price - prev["price"], 4) if prev else None
            c.execute("INSERT INTO prices (commodity, price, unit, change, kind, contract, source, as_of, created) VALUES (?,?,?,?,?,?,?,?,?)",
                      (commodity, round(price, 4), unit, change, kind, contract, source[:80], as_of or now(), now()))

    def import_csv(self, text: str, source: str) -> int:
        """Columns: commodity, price[, change, kind, contract, as_of]."""
        n = 0
        for row in csv.DictReader(io.StringIO(text)):
            self.add(row["commodity"].strip().lower(), float(row["price"]),
                     change=float(row["change"]) if row.get("change") else None, kind=(row.get("kind") or "cash").strip(),
                     source=source, contract=row.get("contract") or None, as_of=row.get("as_of") or None)
            n += 1
        return n

    def board(self) -> dict[str, Any]:
        out = []
        with self.accounts.db() as c:
            for key, meta in COMMODITIES.items():
                rows = c.execute("SELECT * FROM prices WHERE commodity=? ORDER BY as_of DESC, id DESC LIMIT 30", (key,)).fetchall()
                if not rows:
                    out.append({"key": key, **meta, "price": None})
                    continue
                latest = dict(rows[0])
                hist = [r["price"] for r in reversed(rows) if r["kind"] == latest["kind"]]
                pct = (latest["change"] / (latest["price"] - latest["change"]) * 100) if latest["change"] not in (None, 0) and latest["price"] != latest["change"] else (0.0 if latest["change"] == 0 else None)
                out.append({"key": key, **meta, "price": latest["price"], "change": latest["change"], "pct": round(pct, 2) if pct is not None else None,
                            "kind": latest["kind"], "contract": latest["contract"], "source": latest["source"], "as_of": latest["as_of"], "history": hist})
        return {"commodities": out, "live_feed": bool(os.environ.get("BARCHART_API_KEY"))}

    # ---- Barchart (optional, licensed) ------------------------------------------------
    def fetch_barchart(self) -> int:
        key = os.environ.get("BARCHART_API_KEY")
        if not key:
            return 0
        symbols = ",".join(m["futures"] for m in COMMODITIES.values())
        r = httpx.get("https://ondemand.websol.barchart.com/getQuote.json", params={"apikey": key, "symbols": symbols}, timeout=20)
        r.raise_for_status()
        by_root = {m["futures"][:2]: k for k, m in COMMODITIES.items()}
        n = 0
        for q in r.json().get("results", []):
            k = by_root.get(str(q.get("symbol", ""))[:2])
            if not k or q.get("lastPrice") is None:
                continue
            # CBOT grains are quoted in cents per bushel.
            self.add(k, float(q["lastPrice"]) / 100, change=float(q.get("netChange") or 0) / 100, kind="futures",
                     source="Barchart (delayed)", contract=q.get("symbol"), as_of=q.get("tradeTimestamp") or now())
            n += 1
        return n

    def start(self, every_s: float = 900) -> None:
        if not os.environ.get("BARCHART_API_KEY"):
            return

        def loop() -> None:
            while True:
                try:
                    self.fetch_barchart()
                except Exception as exc:
                    log.warning("price fetch failed: %s", exc)
                time.sleep(every_s)
        threading.Thread(target=loop, daemon=True, name="prices").start()
