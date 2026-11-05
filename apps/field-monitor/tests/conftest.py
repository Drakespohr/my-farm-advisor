from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from affine import Affine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fieldmonitor.store import Store  # noqa: E402

# A ~1 km square field near Ames, Iowa (UTM 15N).
FIELD = {
    "type": "Polygon",
    "coordinates": [[[-93.62, 42.02], [-93.608, 42.02], [-93.608, 42.029], [-93.62, 42.029], [-93.62, 42.02]]],
}
CRS = "EPSG:32615"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


def synthetic_grid(shape: tuple[int, int] = (60, 60)) -> tuple[Affine, str]:
    # Top-left corner placed inside the field polygon's UTM bounds.
    return Affine(10.0, 0, 448500.0, 0, -10.0, 4652500.0), CRS


def ndvi_field(shape: tuple[int, int] = (60, 60), base: float = 0.8, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    arr = base + rng.normal(0, 0.01, shape)
    # Gentle west-east gradient so zones have structure.
    arr += np.linspace(-0.05, 0.05, shape[1])[None, :]
    return arr.astype(np.float32)


@pytest.fixture(autouse=True)
def _no_live_soil_weather(monkeypatch):
    """Tests never call Open-Meteo; tests that need soil data install a fake feed."""
    import httpx

    def offline(self, lat, lon):
        raise httpx.ConnectError("offline in tests")

    monkeypatch.setattr("fieldmonitor.planting.SoilWeather.daily", offline)
