"""The browser-side crossroads finder (static/crossroads.js) gives the same answers as the server."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_intersections import GRID

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_browser_finder_matches_server_rules():
    script = Path(__file__).with_name("crossroads_check.cjs")
    out = json.loads(subprocess.run([NODE, str(script), json.dumps(GRID)], capture_output=True, text=True, check=True).stdout)
    first = out["exact"][0]
    assert first["main"] == "North 250th Street & Waverly Road" and first["kind"] == "intersection"
    assert first["lat"] == pytest.approx(40.92, abs=1e-4) and first["lon"] == pytest.approx(-96.5, abs=1e-4)
    assert len(out["exact"]) == 2 and out["exact"][1]["lat"] == pytest.approx(41.55, abs=1e-4)  # nearest first, one per corner
    assert out["misspaced"][0]["lat"] == pytest.approx(40.92, abs=1e-4)
    assert out["tee"][0]["lat"] == pytest.approx(40.9199, abs=2e-4)  # T-junction drawn short
    assert out["notCross"] is None and out["regex2500"] is False
