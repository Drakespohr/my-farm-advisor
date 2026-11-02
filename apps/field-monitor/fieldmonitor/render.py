"""Render index arrays to web-map PNG overlays (reprojected to Web Mercator)."""

from __future__ import annotations

import io
from typing import Sequence

import numpy as np
from affine import Affine
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject

Stops = Sequence[tuple[float, tuple[int, int, int]]]
Bounds = list[list[float]]  # [[south, west], [north, east]] for Leaflet


def colorize(arr: np.ndarray, valid: np.ndarray, stops: Stops, vmin: float, vmax: float) -> np.ndarray:
    """Map values to RGBA through linear color stops; invalid pixels are transparent."""
    t = np.clip((arr - vmin) / (vmax - vmin), 0.0, 1.0)
    t = np.where(np.isfinite(t), t, 0.0)
    pos = np.array([p for p, _ in stops])
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    for ch in range(3):
        rgba[..., ch] = np.interp(t, pos, np.array([c[ch] for _, c in stops], dtype=float)).astype(np.uint8)
    rgba[..., 3] = np.where(valid & np.isfinite(arr), 255, 0)
    return rgba


def classes_rgba(classes: np.ndarray, n: int, stops: Stops) -> np.ndarray:
    t = np.where(classes > 0, (classes.astype(float) - 1) / max(1, n - 1), 0.0)
    return colorize(t, classes > 0, stops, 0.0, 1.0)


def true_color(red: np.ndarray, green: np.ndarray, blue: np.ndarray, gain: float = 3.5, gamma: float = 0.8) -> np.ndarray:
    rgb = np.stack([red, green, blue], axis=-1)
    with np.errstate(invalid="ignore"):
        scaled = np.clip(rgb * gain, 0.0, 1.0) ** gamma
    ok = np.all(np.isfinite(rgb), axis=-1)
    out = np.zeros((*red.shape, 4), dtype=np.uint8)
    out[..., :3] = np.where(ok[..., None], scaled * 255, 0).astype(np.uint8)
    out[..., 3] = np.where(ok, 255, 0)
    return out


def to_web_png(rgba: np.ndarray, transform: Affine, crs: str) -> tuple[bytes, Bounds]:
    """Warp an RGBA image from its UTM grid to EPSG:3857 so a Leaflet ImageOverlay is exact."""
    h, w = rgba.shape[:2]
    left, top = transform * (0, 0)
    right, bottom = transform * (w, h)
    dst_t, dst_w, dst_h = calculate_default_transform(crs, "EPSG:3857", w, h, left, bottom, right, top)
    assert dst_w is not None and dst_h is not None
    dst = np.zeros((4, dst_h, dst_w), dtype=np.uint8)
    for ch in range(4):
        reproject(
            source=np.ascontiguousarray(rgba[..., ch]),
            destination=dst[ch],
            src_transform=transform,
            src_crs=crs,
            dst_transform=dst_t,
            dst_crs="EPSG:3857",
            resampling=Resampling.nearest,
            src_nodata=None,
            dst_nodata=None,
        )
    img = Image.fromarray(np.moveaxis(dst, 0, -1), mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)

    to_ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    x0, y0 = dst_t * (0, 0)
    x1, y1 = dst_t * (dst_w, dst_h)
    west, north = to_ll.transform(x0, y0)
    east, south = to_ll.transform(x1, y1)
    return buf.getvalue(), [[south, west], [north, east]]
