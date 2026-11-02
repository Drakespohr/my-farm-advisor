"""Satellite field monitoring: Sentinel-2 vegetation, moisture, and residue indices per field."""

__version__ = "0.1.0"

# Bump whenever index math, masking, or resampling changes. Observations are stored
# under their processing version so old results are never overwritten.
PROCESSING_VERSION = "2"
# v2: adds per-date haze QA (aerosol optical thickness + blue reflectance) and neighbor-ring
#     statistics outside the field; context window widened to 160 m. Index math unchanged.
