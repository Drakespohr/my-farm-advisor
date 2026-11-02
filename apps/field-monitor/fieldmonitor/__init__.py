"""Satellite field monitoring: Sentinel-2 vegetation, moisture, and residue indices per field."""

__version__ = "0.1.0"

# Bump whenever index math, masking, or resampling changes. Observations are stored
# under their processing version so old results are never overwritten.
PROCESSING_VERSION = "1"
