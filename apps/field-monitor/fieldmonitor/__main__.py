"""Command-line entry point: `python -m fieldmonitor <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from .catalog import search_scenes
from .pipeline import process_observation
from .store import Store


def _cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)


def _cmd_import(args: argparse.Namespace) -> None:
    from .accounts import Accounts

    store = Store.from_env()
    accounts = Accounts(store.root / "app.db")
    owner = None
    if args.owner:
        owner = accounts.get_user_by_email(args.owner)
        if not owner:
            sys.exit(f"No account for {args.owner}. Sign up in the app first.")
    elif accounts.user_count():
        sys.exit("Accounts exist: pass --owner EMAIL so the imported fields belong to someone.")
    data = json.loads(Path(args.geojson).read_text())
    feats = data["features"] if data.get("type") == "FeatureCollection" else [data]
    for n, feat in enumerate(feats, start=1):
        geom = feat.get("geometry", feat)
        props = feat.get("properties") or {}
        name = props.get(args.name_property) or props.get("name") or props.get("field_id") or f"Field {n}"
        fid = props.get(args.id_property) if args.id_property else props.get("field_id")
        rec, created = store.create_field(
            geom, str(name), field_id=str(fid) if fid else None, crop=props.get("crop"), source=f"cli:{args.geojson}"
        )
        if owner:
            current = accounts.owner_of(rec["id"])
            if current and current != owner["id"]:
                print(f"skipped  {rec['id']}: owned by another account", file=sys.stderr)
                continue
            accounts.grant(rec["id"], owner["id"], "owner")
        print(f"{'created ' if created else 'exists  '} {rec['id']}  {rec['area_ac']:.1f} ac  {rec['name']}")


def _cmd_process(args: argparse.Namespace) -> None:
    store = Store.from_env()
    fields = store.list_fields() if args.field == "all" else [store.get_field(args.field)]
    for f in fields:
        scenes, source = search_scenes(f["geometry"], args.start, args.end, args.max_cloud)
        print(f"{f['id']}: {len(scenes)} scenes via {source}")
        for s in scenes:
            try:
                m = process_observation(store, f, s)
            except Exception as exc:  # report and continue with the next scene
                print(f"  {s.date}  ERROR {exc}", file=sys.stderr)
                continue
            if m["status"] == "clear":
                st = m["stats"]
                print(f"  {m['date']}  clear {m['field_clear_fraction']:.0%}  NDVI {st['ndvi']['median']:.3f}  NDRE {st['ndre']['median']:.3f}  NDMI {st['ndmi']['median']:.3f}")
            else:
                print(f"  {m['date']}  cloudy (clear {m['field_clear_fraction']:.0%})")


def _prices():
    from .accounts import Accounts
    from .prices import Prices

    return Prices(Accounts(Store.from_env().root / "app.db"))


def _cmd_set_price(args: argparse.Namespace) -> None:
    _prices().add(args.commodity, args.price, change=args.change, kind=args.kind, source=args.source, contract=args.contract)
    print(f"{args.commodity}: ${args.price:.2f}/bu ({args.kind}, {args.source})")


def _cmd_import_prices(args: argparse.Namespace) -> None:
    n = _prices().import_csv(Path(args.csv).read_text(), args.source)
    print(f"imported {n} prices from {args.csv}")


def _cmd_fields(_: argparse.Namespace) -> None:
    for f in Store.from_env().list_fields(include_archived=True):
        flag = " [archived]" if f.get("archived") else ""
        print(f"{f['id']}  {f['area_ac']:.1f} ac  {f['name']}{flag}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="fieldmonitor", description="Sentinel-2 field monitoring")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=_cmd_serve)

    s = sub.add_parser("import", help="import field boundaries from GeoJSON")
    s.add_argument("geojson")
    s.add_argument("--name-property", default="name")
    s.add_argument("--id-property", default=None)
    s.add_argument("--owner", default=None, help="email of the account that owns the imported fields")
    s.set_defaults(fn=_cmd_import)

    s = sub.add_parser("process", help="fetch and process imagery for a field (or 'all')")
    s.add_argument("field")
    s.add_argument("--start", type=date.fromisoformat, required=True)
    s.add_argument("--end", type=date.fromisoformat, required=True)
    s.add_argument("--max-cloud", type=float, default=60.0)
    s.set_defaults(fn=_cmd_process)

    s = sub.add_parser("set-price", help="record a crop price shown on the News page")
    s.add_argument("commodity", choices=["corn", "soybeans", "wheat"])
    s.add_argument("price", type=float, help="dollars per bushel, e.g. 4.32")
    s.add_argument("--change", type=float, default=None, help="change vs previous (default: computed)")
    s.add_argument("--kind", choices=["cash", "futures"], default="cash")
    s.add_argument("--source", default="Manual entry", help='e.g. "Chariton Co-op cash bid"')
    s.add_argument("--contract", default=None, help="e.g. ZCZ26")
    s.set_defaults(fn=_cmd_set_price)

    s = sub.add_parser("import-prices", help="import prices from CSV (commodity,price[,change,kind,contract,as_of])")
    s.add_argument("csv")
    s.add_argument("--source", required=True)
    s.set_defaults(fn=_cmd_import_prices)

    s = sub.add_parser("fields", help="list fields")
    s.set_defaults(fn=_cmd_fields)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
