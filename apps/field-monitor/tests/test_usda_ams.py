import pytest

from fieldmonitor import usda_ams
from fieldmonitor.accounts import Accounts
from fieldmonitor.prices import Prices

ROWS = [
    {"report_date": "11/02/2026", "commodity": "Corn", "class": "Yellow", "avg_price": "4.12"},
    {"report_date": "11/02/2026", "commodity": "Corn", "class": "Yellow", "avg_price": "4.20"},
    {"report_date": "11/02/2026", "commodity": "Corn", "class": "Yellow", "price_min": "4.00", "price_max": "4.10"},
    {"report_date": "11/02/2026", "commodity": "Soybeans", "price": "1045"},          # cents
    {"report_date": "11/02/2026", "commodity": "Soybean Meal", "avg_price": "310"},   # not grain
    {"report_date": "11/02/2026", "commodity": "Corn Gluten Feed", "avg_price": "150"},
    {"report_date": "2026-11-03", "commodity": "Wheat", "class": "HRW", "avg_price": "5.31"},
    {"report_date": "11/02/2026", "commodity": "Corn"},                               # no price
]


def test_parsing_and_medians():
    med = usda_ams.daily_medians(ROWS)
    assert med[("2026-11-02", "corn")] == (4.12, 3)
    assert med[("2026-11-02", "soybeans")] == (10.45, 1)
    assert med[("2026-11-03", "wheat")] == (5.31, 1)
    assert len(med) == 3


def test_report_matching_by_state():
    reports = [{"id": "2850", "title": "Iowa Daily Cash Grain Bids"}, {"id": "3046", "title": "Nebraska Daily Grain Bids"},
               {"id": "9", "title": "Iowa Hog Report"}, {"id": "10", "title": "Missouri Grain Bids"}]
    assert [r["id"] for r in usda_ams.grain_bid_reports(reports, ["Iowa", "Missouri"])] == ["2850", "10"]


def test_sync_stores_once_and_change_is_per_source(tmp_path, monkeypatch):
    prices = Prices(Accounts(tmp_path / "app.db"))
    prices.add("corn", 3.50, source="Manual entry", as_of="2026-10-01T12:00:00+00:00")  # other sources don't drive USDA's change
    monkeypatch.setenv("USDA_AMS_API_KEY", "test-key")
    monkeypatch.setattr(usda_ams, "list_reports", lambda key: [{"id": "2850", "title": "Iowa Daily Grain Bids"}])
    rows = ROWS + [{"report_date": "10/30/2026", "commodity": "Corn", "avg_price": "4.02"}]
    monkeypatch.setattr(usda_ams, "fetch_report", lambda key, rid, days=10: rows)
    out = usda_ams.sync_prices(prices, ["Iowa"])
    assert out["ok"] and out["stored"] == 4
    assert usda_ams.sync_prices(prices, ["Iowa"])["ok"]  # second run adds no duplicates
    corn = next(c for c in prices.board()["commodities"] if c["key"] == "corn")
    assert corn["price"] == 4.12 and corn["change"] == pytest.approx(0.10) and corn["kind"] == "cash"
    assert corn["source"] == "USDA AMS · Iowa Daily Grain Bids" and corn["contract"] == "3 locations"
    with prices.accounts.db() as c:
        assert c.execute("SELECT COUNT(*) FROM prices WHERE source LIKE 'USDA%'").fetchone()[0] == 4


def test_no_key_or_no_reports(tmp_path, monkeypatch):
    prices = Prices(Accounts(tmp_path / "app.db"))
    monkeypatch.delenv("USDA_AMS_API_KEY", raising=False)
    assert usda_ams.sync_prices(prices, ["Iowa"]) == {"ok": False, "error": "USDA_AMS_API_KEY is not set"}
    monkeypatch.setenv("USDA_AMS_API_KEY", "k")
    monkeypatch.setattr(usda_ams, "list_reports", lambda key: [])
    assert not usda_ams.sync_prices(prices, ["Iowa"])["ok"]
