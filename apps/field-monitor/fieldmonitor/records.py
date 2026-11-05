"""Typed records for the farm and field log (one shared event table).

Each log entry has a `kind` and `details` validated by one of these models. They are the same
record types as apps/ag-inputs (copied from its schemas, including the restricted-use pesticide
rules and record deadlines), so both apps validate records identically.

Farm-level kinds (no field needed): note, rainfall (farm gauge), irrigation_water_sample (a well),
manure_sample (a pit or pile). Every other kind belongs to a field.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Any, Literal, Optional, Union, get_args

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Planting(BaseModel):
    kind: Literal["planting"]
    population_k_per_ac: float = Field(gt=0, le=100, description="Seeds per acre, thousands")
    row_spacing_in: Optional[float] = Field(default=None, gt=0, le=60)
    seed_depth_in: Optional[float] = Field(default=None, gt=0, le=6)
    notes: Optional[str] = None


class StandCount(BaseModel):
    kind: Literal["stand_count"]
    plants_per_ac: float = Field(ge=0, le=500_000)
    notes: Optional[str] = None


class GrowthStage(BaseModel):
    kind: Literal["growth_stage"]
    stage: str = Field(min_length=1, max_length=10, description="e.g. V6, R1 for corn; V3, R3 for soybeans")


class Irrigation(BaseModel):
    kind: Literal["irrigation"]
    amount_in: float = Field(gt=0, le=10)
    method: Literal["pivot", "drip", "furrow", "flood", "sprinkler", "other"]
    source: Optional[str] = Field(default=None, max_length=120, description="Well, canal, etc.")


class Rainfall(BaseModel):
    kind: Literal["rainfall"]
    amount_in: float = Field(ge=0, le=30)
    source: Literal["on_farm_gauge", "weather_feed", "estimate"]


class SoilMoisture(BaseModel):
    kind: Literal["soil_moisture"]
    depth_in: float = Field(gt=0, le=60)
    vwc_pct: Optional[float] = Field(default=None, ge=0, le=100)
    tension_cb: Optional[float] = Field(default=None, ge=0, le=200)

    @model_validator(mode="after")
    def needs_a_reading(self):
        if self.vwc_pct is None and self.tension_cb is None:
            raise ValueError("Provide vwc_pct or tension_cb")
        return self


class Fertilizer(BaseModel):
    kind: Literal["fertilizer"]
    product: str = Field(min_length=1, max_length=120)
    method: Literal["pre_plant", "starter", "broadcast", "banded", "side_dress", "fertigation", "manure"]
    rate_lb_per_ac: float = Field(gt=0, le=1000)
    n_pct: Optional[float] = Field(default=None, ge=0, le=100)
    p2o5_pct: Optional[float] = Field(default=None, ge=0, le=100)
    k2o_pct: Optional[float] = Field(default=None, ge=0, le=100)


class FoliarApplication(BaseModel):
    kind: Literal["foliar"]
    product: str = Field(min_length=1, max_length=120)
    purpose: Literal["fungicide", "insecticide", "herbicide", "micronutrient", "biostimulant", "growth_regulator", "other"]
    active_ingredient: Optional[str] = Field(default=None, max_length=120)
    rate: float = Field(gt=0)
    rate_unit: str = Field(max_length=20, description="e.g. fl_oz/ac, oz/ac, pt/ac, lb/ac")
    water_gal_per_ac: Optional[float] = Field(default=None, gt=0, le=100)
    adjuvant: Optional[str] = Field(default=None, max_length=120)
    target: Optional[str] = Field(default=None, max_length=120, description="Pest, disease or weed targeted")
    growth_stage: Optional[str] = Field(default=None, max_length=10)
    temp_f: Optional[float] = Field(default=None, ge=-40, le=130)
    wind_mph: Optional[float] = Field(default=None, ge=0, le=100)
    humidity_pct: Optional[float] = Field(default=None, ge=0, le=100)
    applicator: Optional[str] = Field(default=None, max_length=120)
    # Restricted-use / applicator record-keeping (added; existing fields above are unchanged).
    epa_reg_number: Optional[str] = Field(default=None, max_length=40, description="EPA Reg. No., e.g. 100-1070 or 100-1070-34704")
    restricted_use: bool = False
    applicator_type: Literal["private", "commercial", "noncommercial"] = "private"
    location: Optional[str] = Field(default=None, max_length=200, description="Where the application was made")
    area_treated_ac: Optional[float] = Field(default=None, gt=0, le=100_000)
    start_time: Optional[str] = Field(default=None, description="Application start time, 24-hour HH:MM")

    @field_validator("epa_reg_number")
    @classmethod
    def epa_number_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        # Format check only: company-product, optionally -distributor (digits).
        if not re.fullmatch(r"\d{1,7}-\d{1,7}(-\d{1,7})?", value):
            raise ValueError("EPA Reg. No. looks like 100-1070 or 100-1070-34704")
        return value

    @field_validator("start_time")
    @classmethod
    def hh_mm(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("start_time must be 24-hour HH:MM, e.g. 07:30")
        return value

    @model_validator(mode="after")
    def compliance(self):
        if self.restricted_use and not self.epa_reg_number:
            raise ValueError("Restricted-use applications require epa_reg_number")
        if self.applicator_type in ("commercial", "noncommercial") and self.temp_f is None:
            raise ValueError("Commercial and noncommercial application records require temp_f")
        return self


class Scouting(BaseModel):
    kind: Literal["scouting"]
    target: str = Field(min_length=1, max_length=120)
    category: Literal["disease", "insect", "weed", "nutrient_deficiency", "abiotic", "other"]
    incidence_pct: float = Field(ge=0, le=100, description="% of plants affected")
    severity_pct: Optional[float] = Field(default=None, ge=0, le=100, description="% of leaf area affected")
    growth_stage: Optional[str] = Field(default=None, max_length=10)
    pattern: Literal["field_wide", "patchy", "edge", "point"]
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    photo_url: Optional[str] = Field(default=None, max_length=500)
    scout: Optional[str] = Field(default=None, max_length=120)


class SoilTest(BaseModel):
    kind: Literal["soil_test"]
    # Field-level date the cores were pulled (occurred_at may be when results came back).
    sampled_on: dt.date
    lab: Optional[str] = Field(default=None, max_length=120)
    depth_in: float = Field(default=6, gt=0, le=60, description="Sampling depth, inches (bottom of the core)")
    ph: Optional[float] = Field(default=None, ge=0, le=14)
    organic_matter_pct: Optional[float] = Field(default=None, ge=0, le=100)
    phosphorus_ppm: Optional[float] = Field(default=None, ge=0)
    potassium_ppm: Optional[float] = Field(default=None, ge=0)
    nitrate_n_ppm: Optional[float] = Field(default=None, ge=0)
    sulfur_ppm: Optional[float] = Field(default=None, ge=0)
    zinc_ppm: Optional[float] = Field(default=None, ge=0)

    @field_validator("sampled_on")
    @classmethod
    def not_in_future(cls, value: dt.date) -> dt.date:
        if value > _utcnow().date() + dt.timedelta(days=1):
            raise ValueError("sampled_on is in the future")
        return value


class IrrigationWaterSample(BaseModel):
    """Nitrate test of irrigation water (for N credits in NUE and NRD reporting)."""
    kind: Literal["irrigation_water_sample"]
    nitrate_n_ppm: float = Field(ge=0, le=500, description="Nitrate-N, ppm (mg/L)")
    source: Optional[str] = Field(default=None, max_length=120, description="Well name, canal, etc.")
    well_registration: Optional[str] = Field(default=None, max_length=40, description="State well registration number, if a well")
    lab: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = None


class ManureSample(BaseModel):
    """Manure nutrient analysis. Solid results are lb per ton; liquid are lb per 1,000 gal."""
    kind: Literal["manure_sample"]
    animal: Literal["swine", "beef", "dairy", "poultry", "other"]
    form: Literal["solid", "liquid"]
    unit: Literal["lb_per_ton", "lb_per_1000_gal"]
    total_n: float = Field(ge=0, le=1000)
    ammonium_n: Optional[float] = Field(default=None, ge=0, le=1000)
    p2o5: Optional[float] = Field(default=None, ge=0, le=1000)
    k2o: Optional[float] = Field(default=None, ge=0, le=1000)
    dry_matter_pct: Optional[float] = Field(default=None, ge=0, le=100)
    source: Optional[str] = Field(default=None, max_length=120, description="Barn, lagoon, pile, etc.")
    lab: Optional[str] = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def unit_matches_form(self):
        expected = "lb_per_ton" if self.form == "solid" else "lb_per_1000_gal"
        if self.unit != expected:
            raise ValueError(f"{self.form} manure results use {expected}")
        if self.ammonium_n is not None and self.ammonium_n > self.total_n:
            raise ValueError("ammonium_n can't exceed total_n")
        return self


class Harvest(BaseModel):
    kind: Literal["harvest"]
    yield_bu_per_ac: float = Field(ge=0, le=1000)
    moisture_pct: Optional[float] = Field(default=None, ge=0, le=100)


class Note(BaseModel):
    """Anything else worth remembering: equipment, weather events, conversations, plans."""
    kind: Literal["note"]
    text: str = Field(min_length=1, max_length=4000)
    topic: Optional[Literal["general", "equipment", "weather", "labor", "marketing", "plan"]] = "general"


PAYLOAD_MODELS = (
    Planting, StandCount, GrowthStage, Irrigation, Rainfall, SoilMoisture, Fertilizer, FoliarApplication,
    Scouting, SoilTest, Harvest, IrrigationWaterSample, ManureSample, Note,
)
KINDS: tuple[str, ...] = tuple(get_args(m.model_fields["kind"].annotation)[0] for m in PAYLOAD_MODELS)
FARM_LEVEL_KINDS = ("note", "rainfall", "irrigation_water_sample", "manure_sample")
LABELS = {
    "planting": "Planting", "stand_count": "Stand count", "growth_stage": "Growth stage", "irrigation": "Irrigation",
    "rainfall": "Rainfall", "soil_moisture": "Soil moisture", "fertilizer": "Fertilizer / manure applied",
    "foliar": "Spray (foliar application)", "scouting": "Scouting", "soil_test": "Soil test", "harvest": "Harvest",
    "irrigation_water_sample": "Irrigation water test", "manure_sample": "Manure test", "note": "Note",
}

Payload = Annotated[Union[tuple(PAYLOAD_MODELS)], Field(discriminator="kind")]  # type: ignore[valid-type]
_ADAPTER: TypeAdapter[Any] = TypeAdapter(Payload)


def validate(kind: str, details: dict[str, Any]) -> dict[str, Any]:
    """Validated details (JSON-ready, without the kind key). Raises pydantic.ValidationError."""
    obj = _ADAPTER.validate_python({**details, "kind": kind})
    out = obj.model_dump(mode="json")
    out.pop("kind", None)
    return out


def schemas() -> dict[str, Any]:
    """JSON schema per kind, for building the log forms."""
    out = {}
    for kind, model in zip(KINDS, PAYLOAD_MODELS):
        out[kind] = {**model.model_json_schema(), "label": LABELS[kind], "farm_level": kind in FARM_LEVEL_KINDS}
    return out


# ---- record-keeping deadlines (restricted-use and commercial applications) ----------------
PRIVATE_RUP_RECORD_WINDOW = dt.timedelta(days=14)
COMMERCIAL_RECORD_WINDOW = dt.timedelta(hours=48)


def record_due_by(kind: str, payload: dict[str, Any], occurred_at: dt.datetime) -> Optional[dt.datetime]:
    """When the application record must be completed, or None if no deadline applies.

    - Private applicators, restricted-use products: 14 days after the application.
    - Commercial and noncommercial applicators: 48 hours after the application.
    The application time is the event's occurred_at.
    """
    if kind != "foliar":
        return None
    applicator_type = payload.get("applicator_type") or "private"
    if applicator_type in ("commercial", "noncommercial"):
        return occurred_at + COMMERCIAL_RECORD_WINDOW
    if payload.get("restricted_use"):
        return occurred_at + PRIVATE_RUP_RECORD_WINDOW
    return None
