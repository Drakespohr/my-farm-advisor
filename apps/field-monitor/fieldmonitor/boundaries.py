"""Read field boundaries from GeoJSON, KML, and KMZ (DJI Pilot / Google Earth exports)."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any
from xml.etree import ElementTree

Feature = tuple[dict[str, Any], dict[str, Any]]  # (geometry, properties)

MAX_KMZ_BYTES = 20 * 1024 * 1024


def parse_geojson(data: dict[str, Any]) -> list[Feature]:
    t = data.get("type")
    if t == "FeatureCollection":
        return [(f["geometry"], f.get("properties") or {}) for f in data.get("features", []) if f.get("geometry")]
    if t == "Feature":
        return [(data["geometry"], data.get("properties") or {})]
    if t in ("Polygon", "MultiPolygon"):
        return [(data, {})]
    raise ValueError("Expected a GeoJSON Polygon, MultiPolygon, Feature, or FeatureCollection.")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [c for c in el.iter() if _local(c.tag) == name]


def _ring(coords_text: str) -> list[list[float]]:
    pts = []
    for tok in coords_text.split():
        parts = tok.split(",")
        if len(parts) >= 2:
            pts.append([float(parts[0]), float(parts[1])])
    if len(pts) >= 3 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _polygon(poly: ElementTree.Element) -> list[list[list[float]]] | None:
    outer = [b for b in poly if _local(b.tag) == "outerBoundaryIs"]
    if not outer:
        return None
    coords = _children(outer[0], "coordinates")
    if not coords or not coords[0].text:
        return None
    rings = [_ring(coords[0].text)]
    for inner in (b for b in poly if _local(b.tag) == "innerBoundaryIs"):
        c = _children(inner, "coordinates")
        if c and c[0].text:
            rings.append(_ring(c[0].text))
    return rings if len(rings[0]) >= 4 else None


def parse_kml(text: str | bytes) -> list[Feature]:
    # ElementTree does not resolve external entities, and the bundled expat (2.4+)
    # limits entity-expansion attacks.
    root = ElementTree.fromstring(text)
    feats: list[Feature] = []
    for pm in _children(root, "Placemark"):
        name_el = next((c for c in pm if _local(c.tag) == "name"), None)
        name = (name_el.text or "").strip() if name_el is not None else ""
        polys = [p for p in (_polygon(e) for e in _children(pm, "Polygon")) if p]
        if not polys:
            continue
        geom = {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}
        feats.append((geom, {"name": name} if name else {}))
    if not feats:
        raise ValueError("No polygons found in the KML. Export the field outline as a polygon (area/mapping mission), not a waypoint route.")
    return feats


def parse_kmz(data: bytes) -> list[Feature]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        kmls = [i for i in zf.infolist() if i.filename.lower().endswith(".kml")]
        if not kmls:
            raise ValueError("No .kml file inside the KMZ.")
        if sum(i.file_size for i in kmls) > MAX_KMZ_BYTES:
            raise ValueError("KMZ is too large.")
        feats: list[Feature] = []
        errors: list[str] = []
        for info in kmls:
            try:
                feats += parse_kml(zf.read(info))
            except (ValueError, ElementTree.ParseError) as exc:
                errors.append(f"{info.filename}: {exc}")
    if not feats:
        raise ValueError("; ".join(errors) or "No polygons found in the KMZ.")
    return feats


def parse_file(filename: str, data: bytes) -> list[Feature]:
    name = filename.lower()
    if name.endswith(".kmz"):
        return parse_kmz(data)
    if name.endswith(".kml"):
        return parse_kml(data)
    if name.endswith((".geojson", ".json")):
        return parse_geojson(json.loads(data))
    raise ValueError("Unsupported file type. Use .kml, .kmz, .geojson, or .json.")
