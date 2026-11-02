"""Spectral index definitions for Sentinel-2 L2A surface reflectance.

Every index takes a dict of band arrays (float32 surface reflectance, 0-1 scale,
NaN for nodata) keyed by Earth Search asset names and returns a float32 array.

Band key -> Sentinel-2 band (center wavelength, native resolution):
    blue     B02  490 nm  10 m
    green    B03  560 nm  10 m
    red      B04  665 nm  10 m
    rededge1 B05  705 nm  20 m
    rededge2 B06  740 nm  20 m
    rededge3 B07  783 nm  20 m
    nir      B08  842 nm  10 m (broad NIR)
    nir08    B8A  865 nm  20 m (narrow NIR)
    swir16   B11 1610 nm  20 m
    swir22   B12 2190 nm  20 m
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

Bands = dict[str, np.ndarray]


def _nd(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Normalized difference (a - b) / (a + b) with safe division."""
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = a + b
        out = np.where(np.abs(denom) > 1e-6, (a - b) / denom, np.nan)
    return out.astype(np.float32)


def _ndvi(b: Bands) -> np.ndarray:
    return _nd(b["nir"], b["red"])


def _ndre(b: Bands) -> np.ndarray:
    return _nd(b["nir08"], b["rededge1"])


def _gndvi(b: Bands) -> np.ndarray:
    return _nd(b["nir"], b["green"])


def _evi(b: Bands) -> np.ndarray:
    nir, red, blue = b["nir"], b["red"], b["blue"]
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = nir + 6.0 * red - 7.5 * blue + 1.0
        out = np.where(np.abs(denom) > 1e-6, 2.5 * (nir - red) / denom, np.nan)
    # EVI blows up over bright targets (clouds, snow) that slip through masking.
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def _savi(b: Bands) -> np.ndarray:
    nir, red = b["nir"], b["red"]
    soil_l = 0.5
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (1.0 + soil_l) * (nir - red) / (nir + red + soil_l)
    return out.astype(np.float32)


def _msavi(b: Bands) -> np.ndarray:
    nir, red = b["nir"], b["red"]
    term = (2.0 * nir + 1.0) ** 2 - 8.0 * (nir - red)
    with np.errstate(invalid="ignore"):
        out = (2.0 * nir + 1.0 - np.sqrt(np.clip(term, 0.0, None))) / 2.0
    return out.astype(np.float32)


def _ndmi(b: Bands) -> np.ndarray:
    return _nd(b["nir08"], b["swir16"])


def _ndwi(b: Bands) -> np.ndarray:
    return _nd(b["green"], b["nir"])


def _cire(b: Bands) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(b["rededge1"] > 1e-4, b["rededge3"] / b["rededge1"] - 1.0, np.nan)
    return np.clip(out, -1.0, 15.0).astype(np.float32)


def _ndti(b: Bands) -> np.ndarray:
    return _nd(b["swir16"], b["swir22"])


# Color ramps as (position 0-1, (r, g, b)) stops.
RDYLGN = [
    (0.0, (165, 0, 38)),
    (0.2, (244, 109, 67)),
    (0.4, (254, 224, 139)),
    (0.6, (217, 239, 139)),
    (0.8, (102, 189, 99)),
    (1.0, (0, 104, 55)),
]
BROWN_BLUE = [
    (0.0, (140, 81, 10)),
    (0.25, (216, 179, 101)),
    (0.5, (246, 232, 195)),
    (0.75, (90, 180, 172)),
    (1.0, (1, 102, 94)),
]
WATER = [
    (0.0, (247, 244, 235)),
    (0.5, (198, 219, 239)),
    (0.75, (66, 146, 198)),
    (1.0, (8, 48, 107)),
]
RESIDUE = [
    (0.0, (90, 70, 50)),
    (0.4, (166, 128, 82)),
    (0.7, (222, 196, 140)),
    (1.0, (250, 240, 200)),
]
DIVERGING = [
    (0.0, (178, 24, 43)),
    (0.25, (239, 138, 98)),
    (0.5, (247, 247, 247)),
    (0.75, (103, 169, 207)),
    (1.0, (33, 102, 172)),
]
ZONES = [
    (0.0, (215, 48, 39)),
    (0.33, (252, 141, 89)),
    (0.66, (145, 207, 96)),
    (1.0, (26, 152, 80)),
]


@dataclass(frozen=True)
class SpectralIndex:
    key: str
    name: str
    formula: str
    bands: tuple[str, ...]
    compute: Callable[[Bands], np.ndarray]
    display_range: tuple[float, float]
    colormap: list[tuple[float, tuple[int, int, int]]]
    measures: str
    best_for: str
    caveats: str
    # Rough interpretation bands for row crops at the stage named in best_for.
    # These are orientation aids, not agronomic thresholds; scout to confirm.
    guide: list[tuple[float, str]] = field(default_factory=list)
    higher_is: str = "more vigorous canopy"


INDICES: dict[str, SpectralIndex] = {
    i.key: i
    for i in [
        SpectralIndex(
            key="ndvi",
            name="NDVI",
            formula="(B08 − B04) / (B08 + B04)",
            bands=("nir", "red"),
            compute=_ndvi,
            display_range=(0.0, 0.9),
            colormap=RDYLGN,
            measures="Green biomass and canopy cover.",
            best_for="Emergence through canopy closure; stand-establishment problems, drowned-out or hail-damaged areas, weed escapes.",
            caveats="Saturates above ~0.8 once the canopy closes, so mid-season differences in dense corn/soy are compressed. Use NDRE then.",
            guide=[(0.2, "bare soil / residue"), (0.4, "sparse or emerging crop"), (0.6, "moderate canopy"), (0.8, "dense canopy")],
        ),
        SpectralIndex(
            key="ndre",
            name="NDRE",
            formula="(B8A − B05) / (B8A + B05)",
            bands=("nir08", "rededge1"),
            compute=_ndre,
            display_range=(0.1, 0.8),
            colormap=RDYLGN,
            measures="Leaf chlorophyll in a closed canopy; a nitrogen-status proxy.",
            best_for="Mid to late season (corn V10+, soybean R1+) when NDVI has saturated; in-season N decisions.",
            caveats="20 m native resolution. Sentinel-2 B05 (705 nm) reads higher than drone red-edge sensors (~717 nm), so values aren't interchangeable. Compare within a field or against a well-fertilized reference strip.",
            guide=[(0.2, "bare / sparse"), (0.45, "moderate chlorophyll"), (0.65, "high chlorophyll")],
            higher_is="more chlorophyll",
        ),
        SpectralIndex(
            key="cire",
            name="CIre",
            formula="B07 / B05 − 1",
            bands=("rededge3", "rededge1"),
            compute=_cire,
            display_range=(0.0, 7.0),
            colormap=RDYLGN,
            measures="Canopy chlorophyll content (Gitelson red-edge chlorophyll index); roughly linear with chlorophyll.",
            best_for="Quantitative N-sufficiency comparisons against a reference strip; late-season greenness.",
            caveats="20 m resolution. Unbounded scale; use ratios to a reference area (sufficiency index) rather than absolute values.",
            guide=[(1.0, "low chlorophyll"), (3.0, "moderate"), (5.0, "high chlorophyll")],
            higher_is="more chlorophyll",
        ),
        SpectralIndex(
            key="gndvi",
            name="GNDVI",
            formula="(B08 − B03) / (B08 + B03)",
            bands=("nir", "green"),
            compute=_gndvi,
            display_range=(0.2, 0.9),
            colormap=RDYLGN,
            measures="Chlorophyll concentration; more sensitive than NDVI at higher canopy density.",
            best_for="Mid-season N and chlorophyll variation at full 10 m resolution.",
            caveats="Responds to both biomass and chlorophyll, so pair with NDVI to tell them apart.",
            guide=[(0.4, "sparse"), (0.6, "moderate"), (0.75, "dense / high chlorophyll")],
        ),
        SpectralIndex(
            key="evi",
            name="EVI",
            formula="2.5 (B08 − B04) / (B08 + 6 B04 − 7.5 B02 + 1)",
            bands=("nir", "red", "blue"),
            compute=_evi,
            display_range=(0.0, 0.9),
            colormap=RDYLGN,
            measures="Biomass with less saturation and less atmospheric noise than NDVI.",
            best_for="High-biomass canopies and comparisons across dates with haze.",
            caveats="Sensitive to residual cloud or snow. Values are clipped to [-1, 1].",
            guide=[(0.2, "sparse"), (0.4, "moderate"), (0.6, "dense canopy")],
        ),
        SpectralIndex(
            key="savi",
            name="SAVI",
            formula="1.5 (B08 − B04) / (B08 + B04 + 0.5)",
            bands=("nir", "red"),
            compute=_savi,
            display_range=(0.0, 0.8),
            colormap=RDYLGN,
            measures="Vegetation cover corrected for soil-brightness background.",
            best_for="Early season (<40% cover); stand counts and replant decisions.",
            caveats="Fixed soil factor L = 0.5. Use MSAVI2 when cover is very low.",
            guide=[(0.15, "bare soil"), (0.3, "emerging"), (0.5, "established cover")],
        ),
        SpectralIndex(
            key="msavi",
            name="MSAVI2",
            formula="(2 B08 + 1 − √((2 B08 + 1)² − 8 (B08 − B04))) / 2",
            bands=("nir", "red"),
            compute=_msavi,
            display_range=(0.0, 0.8),
            colormap=RDYLGN,
            measures="Vegetation cover with a self-adjusting soil correction.",
            best_for="Emergence and very sparse canopy, cover-crop establishment.",
            caveats="Little extra value over NDVI once the canopy closes.",
            guide=[(0.15, "bare soil"), (0.3, "emerging"), (0.5, "established cover")],
        ),
        SpectralIndex(
            key="ndmi",
            name="NDMI",
            formula="(B8A − B11) / (B8A + B11)",
            bands=("nir08", "swir16"),
            compute=_ndmi,
            display_range=(-0.3, 0.5),
            colormap=BROWN_BLUE,
            measures="Canopy water content (SWIR is absorbed by leaf water).",
            best_for="Drought and heat stress, irrigation uniformity checks, and dry-down monitoring before harvest.",
            caveats="Reflects plant water, not soil moisture directly. 20 m resolution. Bare soil reads low regardless of moisture.",
            guide=[(-0.1, "dry / bare / senescent"), (0.1, "moderate water stress possible"), (0.3, "well-watered canopy")],
            higher_is="more canopy water",
        ),
        SpectralIndex(
            key="ndwi",
            name="NDWI",
            formula="(B03 − B08) / (B03 + B08)",
            bands=("green", "nir"),
            compute=_ndwi,
            display_range=(-0.6, 0.4),
            colormap=WATER,
            measures="Open surface water (McFeeters).",
            best_for="Ponding, flooding, and drainage-tile failure after heavy rain.",
            caveats="Values > 0 usually mean standing water. Shallow water under canopy is not detected.",
            guide=[(-0.3, "vegetation / dry soil"), (0.0, "wet soil"), (0.2, "standing water")],
            higher_is="wetter / open water",
        ),
        SpectralIndex(
            key="ndti",
            name="NDTI",
            formula="(B11 − B12) / (B11 + B12)",
            bands=("swir16", "swir22"),
            compute=_ndti,
            display_range=(0.0, 0.25),
            colormap=RESIDUE,
            measures="Crop residue cover on bare ground (cellulose/lignin absorption near 2.1 µm).",
            best_for="Post-harvest and pre-plant: verifying tillage and conservation practices.",
            caveats="Only meaningful with no green canopy. Wet soil and residue both affect it; compare dates with similar moisture.",
            guide=[(0.05, "conventional tillage / bare"), (0.1, "reduced tillage"), (0.15, "high residue (no-till)")],
            higher_is="more residue cover",
        ),
    ]
}


REQUIRED_BANDS: tuple[str, ...] = tuple(sorted({b for i in INDICES.values() for b in i.bands} | {"blue", "green", "red"}))


def compute_index(key: str, bands: Bands) -> np.ndarray:
    if key not in INDICES:
        raise KeyError(f"Unknown index {key!r}. Known: {', '.join(INDICES)}")
    return INDICES[key].compute(bands)


def describe(index: SpectralIndex) -> dict[str, object]:
    return {
        "key": index.key,
        "name": index.name,
        "formula": index.formula,
        "bands": list(index.bands),
        "display_range": list(index.display_range),
        "colormap": [[p, list(c)] for p, c in index.colormap],
        "measures": index.measures,
        "best_for": index.best_for,
        "caveats": index.caveats,
        "guide": [[v, label] for v, label in index.guide],
        "higher_is": index.higher_is,
    }
