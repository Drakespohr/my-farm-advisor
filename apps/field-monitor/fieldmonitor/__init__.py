"""Satellite field monitoring: Sentinel-2 vegetation, moisture, and residue indices per field."""

__version__ = "0.1.0"

# Product name shown to users (the code package stays `fieldmonitor`).
APP_NAME = "Overacre"
APP_TAGLINE = "Watch every acre. Trade with your neighbors."
APP_TAGLINE_NO_MARKET = "Watch every acre."


def marketplace_enabled() -> bool:
    """The marketplace is paused by default. Set FIELD_MONITOR_MARKETPLACE=1 to turn it back on.
    Listings already in the database are kept either way."""
    import os

    return os.environ.get("FIELD_MONITOR_MARKETPLACE", "0").strip().lower() in ("1", "true", "yes", "on")

# Bump whenever index math, masking, or resampling changes. Observations are stored
# under their processing version so old results are never overwritten.
PROCESSING_VERSION = "3"
# v2: adds per-date haze QA (aerosol optical thickness + blue reflectance) and neighbor-ring
#     statistics outside the field; context window widened to 160 m. Index math unchanged.
# v3: adds the area baseline (NDVI of WorldCover cropland within 2 km) used by the field score.
