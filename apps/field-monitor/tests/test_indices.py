import numpy as np
import pytest

from fieldmonitor.indices import INDICES, REQUIRED_BANDS, compute_index


def bands(**kw):
    base = dict(blue=0.04, green=0.08, red=0.05, rededge1=0.1, rededge2=0.3, rededge3=0.4, nir=0.45, nir08=0.47, swir16=0.2, swir22=0.1)
    base.update(kw)
    return {k: np.array([v], dtype=np.float32) for k, v in base.items()}


def test_required_bands_cover_all_indices():
    for idx in INDICES.values():
        assert set(idx.bands) <= set(REQUIRED_BANDS)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("ndvi", (0.45 - 0.05) / (0.45 + 0.05)),
        ("ndre", (0.47 - 0.1) / (0.47 + 0.1)),
        ("gndvi", (0.45 - 0.08) / (0.45 + 0.08)),
        ("evi", 2.5 * (0.45 - 0.05) / (0.45 + 6 * 0.05 - 7.5 * 0.04 + 1)),
        ("savi", 1.5 * (0.45 - 0.05) / (0.45 + 0.05 + 0.5)),
        ("ndmi", (0.47 - 0.2) / (0.47 + 0.2)),
        ("ndwi", (0.08 - 0.45) / (0.08 + 0.45)),
        ("cire", 0.4 / 0.1 - 1),
        ("ndti", (0.2 - 0.1) / (0.2 + 0.1)),
    ],
)
def test_formulas(key, expected):
    assert compute_index(key, bands())[0] == pytest.approx(expected, rel=1e-5)


def test_msavi_matches_closed_form():
    nir, red = 0.45, 0.05
    expected = (2 * nir + 1 - np.sqrt((2 * nir + 1) ** 2 - 8 * (nir - red))) / 2
    assert compute_index("msavi", bands())[0] == pytest.approx(expected, rel=1e-5)


def test_vegetation_indices_low_on_bare_soil_high_on_canopy():
    soil = bands(red=0.2, nir=0.25, green=0.15, blue=0.12, rededge1=0.22, nir08=0.26)
    for key in ("ndvi", "savi", "msavi", "evi", "ndre"):
        assert compute_index(key, soil)[0] < compute_index(key, bands())[0]


def test_nan_and_zero_denominator_are_safe():
    b = bands(nir=np.nan, red=0.0)
    assert np.isnan(compute_index("ndvi", b)[0])
    z = bands(nir=0.0, red=0.0)
    assert np.isnan(compute_index("ndvi", z)[0])


def test_unknown_index():
    with pytest.raises(KeyError):
        compute_index("nope", bands())
