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
    store = Store.from_env()
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
    s.set_defaults(fn=_cmd_import)

    s = sub.add_parser("process", help="fetch and process imagery for a field (or 'all')")
    s.add_argument("field")
    s.add_argument("--start", type=date.fromisoformat, required=True)
    s.add_argument("--end", type=date.fromisoformat, required=True)
    s.add_argument("--max-cloud", type=float, default=60.0)
    s.set_defaults(fn=_cmd_process)

    s = sub.add_parser("fields", help="list fields")
    s.set_defaults(fn=_cmd_fields)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
