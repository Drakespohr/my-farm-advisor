import numpy as np
import pytest

from fieldmonitor.catalog import Scene, dedupe, mgrs_tiles_for, scene_from_item
from fieldmonitor.raster import _snapped_bounds
from shapely.geometry import shape
from tests.conftest import FIELD


def scene(id_, tile, cloud=10.0):
    date = id_.split("_")[2]
    return Scene(id=id_, datetime=f"{date[:4]}-{date[4:6]}-{date[6:]}T17:00:00Z", platform="", mgrs_tile=tile,
                 cloud_cover=cloud, epsg=32615, assets={}, self_href=None, source="test")


def test_dedupe_prefers_reprocessed_and_home_tile():
    s = [
        scene("S2A_15TVG_20250719_0_L2A", "15TVG"),
        scene("S2A_15TVG_20250719_1_L2A", "15TVG"),
        scene("S2A_15TVH_20250719_1_L2A", "15TVH"),
        scene("S2B_15TVG_20250722_0_L2A", "15TVG"),
    ]
    out = dedupe(s, "15TVG")
    assert [x.id for x in out] == ["S2A_15TVG_20250719_1_L2A", "S2B_15TVG_20250722_0_L2A"]


def test_mgrs_tile_for_ames():
    assert mgrs_tiles_for(shape(FIELD))[0] == "15TVG"


def test_boa_offset_flag_parsed():
    item = {"id": "x", "properties": {"datetime": "2025-07-01T00:00:00Z", "earthsearch:boa_offset_applied": True, "grid:code": "MGRS-15TVG"}, "assets": {}}
    s = scene_from_item(item, "test")
    assert s.boa_offset_applied and s.mgrs_tile == "15TVG"


def test_snapped_bounds_align_to_20m():
    left, bottom, right, top = _snapped_bounds((400013, 4600007, 400991, 4600999), (399960.0, 4700040.0))
    for v, o in ((left, 399960), (right, 399960), (bottom, 4700040), (top, 4700040)):
        assert (v - o) % 20 == 0
    assert left <= 400013 and right >= 400991 and bottom <= 4600007 and top >= 4600999


def test_field_create_is_idempotent_and_geometry_immutable(store):
    rec, created = store.create_field(FIELD, "North 80", crop="corn")
    assert created and rec["area_ac"] > 100
    again, created2 = store.create_field(FIELD, "North 80")
    assert not created2 and again["id"] == rec["id"]
    other = {"type": "Polygon", "coordinates": [[[-93.62, 42.02], [-93.61, 42.02], [-93.61, 42.03], [-93.62, 42.02]]]}
    with pytest.raises(FileExistsError):
        store.create_field(other, "x", field_id=rec["id"])


def test_rejects_projected_coordinates(store):
    bad = {"type": "Polygon", "coordinates": [[[448000, 4652000], [449000, 4652000], [449000, 4653000], [448000, 4652000]]]}
    with pytest.raises(ValueError):
        store.create_field(bad, "utm")


def test_observations_never_overwritten_and_lineage_logged(store):
    rec, _ = store.create_field(FIELD, "f")
    meta = {"id": "o1", "datetime": "2025-07-01T00:00:00Z", "status": "cloudy", "processing_version": "1"}
    store.write_observation(rec["id"], "o1", meta, {}, {"truecolor": b"png"})
    with pytest.raises(FileExistsError):
        store.write_observation(rec["id"], "o1", meta, {}, {})
    log = (store.root / "lineage.jsonl").read_text().splitlines()
    assert [__import__("json").loads(l)["event"] for l in log] == ["field.created", "observation.created"]


def test_only_metadata_is_editable(store):
    rec, _ = store.create_field(FIELD, "f")
    assert store.update_field_meta(rec["id"], archived=True)["archived"] is True
    with pytest.raises(ValueError):
        store.update_field_meta(rec["id"], geometry={})


def test_path_traversal_rejected(store):
    with pytest.raises(KeyError):
        store.get_field("../../etc")
    rec, _ = store.create_field(FIELD, "f")
    with pytest.raises(KeyError):
        store.png_path(rec["id"], "o1", "../meta")


def test_rejects_oversized_field(store):
    huge = {"type": "Polygon", "coordinates": [[[-94, 41], [-93, 41], [-93, 42], [-94, 42], [-94, 41]]]}
    with pytest.raises(ValueError, match="limited"):
        store.create_field(huge, "county")
