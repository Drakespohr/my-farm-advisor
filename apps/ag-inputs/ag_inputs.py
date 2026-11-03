"""
ag_inputs.py - farmer-entered field, season and management records for the
ag monitoring app.

Each record is a "field event" attached to a season. The payload is validated
against a typed schema for its kind (irrigation, fertilizer, foliar, ...) and
stored as JSON, so new kinds can be added without a database migration.

Add to the existing FastAPI app:
    from ag_inputs import router as inputs_router, init_db
    app.include_router(inputs_router)

Schema changes are managed with Alembic (see README). Apply them with
`alembic upgrade head` (or call init_db(), which does the same) before serving.

Run on its own for testing:
    alembic upgrade head && uvicorn ag_inputs:app --reload

Set AG_DATABASE_URL to a Postgres URL (e.g. AWS RDS) for production.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Annotated, Any, Literal, Optional, Union, get_args

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import (JSON, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, String,
                        UniqueConstraint, create_engine, event, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from auth import get_auth_provider
import nrd_rules

DATABASE_URL = os.getenv("AG_DATABASE_URL", "sqlite:///ag_monitor.db")
engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _to_naive_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


# ---------------------------------------------------------------------------
# Database tables
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


ROLES = ("owner", "operator", "applicator", "scout", "viewer")
WRITE_ROLES = ("owner", "operator", "applicator", "scout")  # can log events and drafts
MANAGE_ROLES = ("owner", "operator")  # can create fields and seasons
MEMBER_ADMIN_ROLES = ("owner",)  # can add/remove members


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # identity-provider id
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class FarmRow(Base):
    __tablename__ = "farms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class MembershipRow(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("farm_id", "user_id", name="uq_membership_farm_user"),
        CheckConstraint("role IN ('owner','operator','applicator','scout','viewer')", name="ck_membership_role"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class FieldRow(Base):
    __tablename__ = "fields"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    acres: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Nullable so fields created before farms existed are kept (assign them with manage.py).
    farm_id: Mapped[Optional[int]] = mapped_column(ForeignKey("farms.id"), nullable=True, index=True)
    # NRD reporting context (optional): which NRD, its management-area phase, and soil texture.
    nrd: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    nrd_phase: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    soil_texture: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)


class SeasonRow(Base):
    __tablename__ = "seasons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("fields.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    crop: Mapped[str] = mapped_column(String(20))
    variety: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    planting_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)


class EventRow(Base):
    __tablename__ = "field_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    recorded_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Append-only: a correction is a new row pointing at the row it replaces.
    supersedes_id: Mapped[Optional[int]] = mapped_column(ForeignKey("field_events.id"), nullable=True, index=True)
    entered_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class DraftRow(Base):
    """Work-in-progress form content, one per user, form type and season."""
    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("user_id", "form_type", "season_id", name="uq_draft_user_form_season"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    form_type: Mapped[str] = mapped_column(String(40))
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


@event.listens_for(Session, "before_flush")
def _events_are_append_only(session: Session, flush_context: Any, instances: Any) -> None:
    """Never update or delete an event row (the database also enforces this with triggers)."""
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, EventRow) and (obj in session.deleted or session.is_modified(obj)):
            raise ValueError("field_events is append-only: record a correction with supersedes_id instead")


# ---------------------------------------------------------------------------
# Event payload schemas (one per kind, discriminated on "kind")
# ---------------------------------------------------------------------------

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


PAYLOAD_MODELS = (
    Planting, StandCount, GrowthStage, Irrigation, Rainfall, SoilMoisture,
    Fertilizer, FoliarApplication, Scouting, SoilTest, Harvest,
    IrrigationWaterSample, ManureSample,
)

EventPayload = Annotated[
    Union[
        Planting, StandCount, GrowthStage, Irrigation, Rainfall, SoilMoisture,
        Fertilizer, FoliarApplication, Scouting, SoilTest, Harvest,
        IrrigationWaterSample, ManureSample,
    ],
    Field(discriminator="kind"),
]

EVENT_KINDS: tuple[str, ...] = tuple(get_args(m.model_fields["kind"].annotation)[0] for m in PAYLOAD_MODELS)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class FarmIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberIn(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)
    subject: Optional[str] = Field(default=None, max_length=255)
    role: Literal["owner", "operator", "applicator", "scout", "viewer"]

    @model_validator(mode="after")
    def one_identifier(self):
        if not (self.email or self.subject):
            raise ValueError("Provide the member's email or subject")
        return self


class DraftIn(BaseModel):
    content: dict[str, Any]


SOIL_TEXTURES = ("sand", "loamy sand", "sandy loam", "loam", "silt loam", "silt", "sandy clay loam",
                 "clay loam", "silty clay loam", "sandy clay", "silty clay", "clay")  # USDA texture classes


class FieldIn(BaseModel):
    farm_id: int
    name: str = Field(min_length=1, max_length=120)
    centroid_lat: float = Field(ge=-90, le=90)
    centroid_lon: float = Field(ge=-180, le=180)
    acres: Optional[float] = Field(default=None, gt=0)
    nrd: Optional[str] = Field(default=None, description="NRD key from nrd_rules.NRDS")
    nrd_phase: Optional[int] = Field(default=None, ge=1, le=4, description="Groundwater management area phase")
    soil_texture: Optional[Literal[SOIL_TEXTURES]] = None  # type: ignore[valid-type]

    @field_validator("nrd")
    @classmethod
    def known_nrd(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in nrd_rules.NRDS:
            raise ValueError(f"Unknown NRD; choose one of {', '.join(nrd_rules.NRDS)}")
        return value


class SeasonIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    crop: Literal["corn", "soybeans", "wheat"]
    variety: Optional[str] = Field(default=None, max_length=120)
    planting_date: Optional[dt.date] = None


class EventIn(BaseModel):
    occurred_at: dt.datetime
    payload: EventPayload
    recorded_by: Optional[str] = Field(default=None, max_length=120, description="Person who made this record")
    supersedes_id: Optional[int] = Field(default=None, description="Id of the event this record corrects")

    @field_validator("occurred_at")
    @classmethod
    def normalize_and_check(cls, value: dt.datetime) -> dt.datetime:
        value = _to_naive_utc(value)
        if value > _utcnow() + dt.timedelta(hours=24):
            raise ValueError("occurred_at cannot be in the future")
        return value


# ---------------------------------------------------------------------------
# Record-keeping deadlines
# ---------------------------------------------------------------------------

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


def deadline_info(row: "EventRow") -> dict[str, Any]:
    """{"record_due_by", "recorded_late"} for events with a record deadline, else {}."""
    due = record_due_by(row.kind, row.payload, row.occurred_at)
    if due is None:
        return {}
    return {"record_due_by": due.isoformat(), "recorded_late": row.created_at > due}


# ---------------------------------------------------------------------------
# Diagnosis context
# ---------------------------------------------------------------------------

_CONTEXT_SOIL_KEYS = ("lab", "depth_in", "ph", "organic_matter_pct", "phosphorus_ppm", "potassium_ppm",
                      "nitrate_n_ppm", "sulfur_ppm", "zinc_ppm")


def build_context(season: SeasonRow, rows: list[EventRow], as_of: dt.datetime) -> dict[str, Any]:
    """Summarize a season's logged inputs as of a date, for diagnosis."""
    recent_cutoff = as_of - dt.timedelta(days=14)

    rain_14 = 0.0
    irrigation_14 = 0.0
    nitrogen_season = 0.0
    fertilizer_log: list[dict[str, Any]] = []
    foliar_log: list[dict[str, Any]] = []
    scouting_latest: dict[str, dict[str, Any]] = {}
    growth_stage: Optional[dict[str, Any]] = None
    soil_test: Optional[dict[str, Any]] = None
    stand: Optional[dict[str, Any]] = None

    for row in rows:  # ordered oldest to newest, so later rows overwrite earlier ones
        p = row.payload
        days_ago = (as_of - row.occurred_at).days

        if row.kind == "rainfall" and row.occurred_at >= recent_cutoff:
            rain_14 += p["amount_in"]
        elif row.kind == "irrigation" and row.occurred_at >= recent_cutoff:
            irrigation_14 += p["amount_in"]
        elif row.kind == "fertilizer":
            n_lb = p["rate_lb_per_ac"] * (p.get("n_pct") or 0) / 100
            nitrogen_season += n_lb
            fertilizer_log.append({
                "product": p["product"], "method": p["method"],
                "rate_lb_per_ac": p["rate_lb_per_ac"], "n_lb_per_ac": round(n_lb, 1),
                "days_ago": days_ago,
            })
        elif row.kind == "foliar":
            foliar_log.append({
                "product": p["product"], "purpose": p["purpose"],
                "active_ingredient": p.get("active_ingredient"),
                "rate": p["rate"], "rate_unit": p["rate_unit"],
                "target": p.get("target"), "growth_stage": p.get("growth_stage"),
                "temp_f": p.get("temp_f"), "wind_mph": p.get("wind_mph"),
                "days_ago": days_ago,
            })
        elif row.kind == "scouting":
            scouting_latest[p["target"]] = {
                "category": p["category"], "incidence_pct": p["incidence_pct"],
                "severity_pct": p.get("severity_pct"), "pattern": p["pattern"],
                "growth_stage": p.get("growth_stage"), "days_ago": days_ago,
            }
        elif row.kind == "growth_stage":
            growth_stage = {"stage": p["stage"], "days_ago": days_ago}
        elif row.kind == "stand_count":
            stand = {"plants_per_ac": p["plants_per_ac"], "days_ago": days_ago}
        elif row.kind == "soil_test":
            # Original keys only, so the context shape doesn't change as SoilTest grows.
            soil_test = {k: v for k, v in p.items() if k in _CONTEXT_SOIL_KEYS and v is not None}
            soil_test["days_ago"] = days_ago

    days_since_planting = (as_of.date() - season.planting_date).days if season.planting_date else None

    return {
        "season": {
            "year": season.year, "crop": season.crop,
            "variety": season.variety,
            "planting_date": season.planting_date.isoformat() if season.planting_date else None,
        },
        "as_of": as_of.isoformat(),
        "days_since_planting": days_since_planting,
        "growth_stage": growth_stage,
        "stand_count": stand,
        "last_14_days": {
            "rainfall_in": round(rain_14, 2),
            "irrigation_in": round(irrigation_14, 2),
            "total_water_in": round(rain_14 + irrigation_14, 2),
        },
        "nitrogen_applied_season_lb_per_ac": round(nitrogen_season, 1),
        "recent_fertilizer": fertilizer_log[-5:],
        "recent_foliar": foliar_log[-5:],
        "scouting_latest_by_target": scouting_latest,
        "latest_soil_test": soil_test,
    }


# ---------------------------------------------------------------------------
# Database and API
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Bring the database schema up to date by applying Alembic migrations.

    Kept for callers that import it; it no longer uses create_all. Most deployments
    should run `alembic upgrade head` as a release step instead.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).with_name("alembic.ini")))
    cfg.attributes["url"] = DATABASE_URL
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")


def get_session():
    with SessionLocal() as session:
        yield session


# ---- auth & access ----------------------------------------------------------------

def get_current_user(request: Request, db: Session = Depends(get_session)) -> UserRow:
    """The authenticated user, created on first sight. Identity comes from auth.get_auth_provider()."""
    identity = get_auth_provider().authenticate(request)
    if identity is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.scalars(select(UserRow).where(UserRow.subject == identity.subject)).first()
    if user is None:
        user = UserRow(subject=identity.subject, email=identity.email, name=identity.name, created_at=_utcnow())
        db.add(user)
        db.commit()
        db.refresh(user)
    elif (identity.email and identity.email != user.email) or (identity.name and identity.name != user.name):
        user.email = identity.email or user.email
        user.name = identity.name or user.name
        db.commit()
    return user


def _role(db: Session, user: UserRow, farm_id: Optional[int]) -> Optional[str]:
    if farm_id is None:
        return None
    m = db.scalars(select(MembershipRow).where(MembershipRow.farm_id == farm_id, MembershipRow.user_id == user.id)).first()
    return m.role if m else None


def _require(db: Session, user: UserRow, farm_id: Optional[int], allowed: tuple[str, ...], what: str) -> str:
    """Check the user's role on a farm. Non-members get 404 so ids don't leak."""
    role = _role(db, user, farm_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    if role not in allowed:
        raise HTTPException(status_code=403, detail=f"Your role ({role}) can't do that here")
    return role


def _field_for(db: Session, user: UserRow, field_id: int, allowed: tuple[str, ...] = ROLES) -> FieldRow:
    field = db.get(FieldRow, field_id)
    _require(db, user, field.farm_id if field else None, allowed, "Field")
    assert field is not None
    return field


def _season_for(db: Session, user: UserRow, season_id: int, allowed: tuple[str, ...] = ROLES) -> SeasonRow:
    season = db.get(SeasonRow, season_id)
    field = db.get(FieldRow, season.field_id) if season else None
    _require(db, user, field.farm_id if field else None, allowed, "Season")
    assert season is not None
    return season


def _get_season_or_404(db: Session, season_id: int) -> SeasonRow:
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return season


def effective_events(rows: list[EventRow]) -> list[EventRow]:
    """Drop rows that a later correction superseded (keeps the newest version of each record)."""
    replaced = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return [r for r in rows if r.id not in replaced]


def _event_out(row: EventRow, superseded_by: Optional[int] = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "season_id": row.season_id,
        "kind": row.kind,
        "occurred_at": row.occurred_at.isoformat(),
        "payload": row.payload,
        "recorded_by": row.recorded_by,
        "supersedes_id": row.supersedes_id,
        "superseded_by": superseded_by,
        **deadline_info(row),
    }


router = APIRouter(prefix="/api", tags=["field inputs"])


# ---- farms & members ----------------------------------------------------------------

@router.post("/farms", status_code=201)
def create_farm(body: FarmIn, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    farm = FarmRow(name=body.name, created_at=_utcnow())
    db.add(farm)
    db.flush()
    db.add(MembershipRow(farm_id=farm.id, user_id=user.id, role="owner", created_at=_utcnow()))
    db.commit()
    return {"id": farm.id, "name": farm.name, "role": "owner"}


@router.get("/farms")
def list_farms(db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    rows = db.execute(select(FarmRow, MembershipRow.role).join(MembershipRow, MembershipRow.farm_id == FarmRow.id)
                      .where(MembershipRow.user_id == user.id).order_by(FarmRow.name)).all()
    return [{"id": f.id, "name": f.name, "role": role} for f, role in rows]


@router.get("/farms/{farm_id}/members")
def list_members(farm_id: int, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _require(db, user, farm_id, ROLES, "Farm")
    rows = db.execute(select(MembershipRow, UserRow).join(UserRow, UserRow.id == MembershipRow.user_id)
                      .where(MembershipRow.farm_id == farm_id).order_by(UserRow.name)).all()
    return [{"user_id": u.id, "name": u.name, "email": u.email, "role": m.role} for m, u in rows]


@router.post("/farms/{farm_id}/members", status_code=201)
def set_member(farm_id: int, body: MemberIn, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    """Add a member or change their role. The person must have signed in once."""
    _require(db, user, farm_id, MEMBER_ADMIN_ROLES, "Farm")
    stmt = select(UserRow).where(UserRow.subject == body.subject) if body.subject else select(UserRow).where(UserRow.email == body.email)
    member = db.scalars(stmt).first()
    if member is None:
        raise HTTPException(404, "No user with that email/subject yet; they need to sign in once first")
    m = db.scalars(select(MembershipRow).where(MembershipRow.farm_id == farm_id, MembershipRow.user_id == member.id)).first()
    if m and m.role == "owner" and body.role != "owner" and _owner_count(db, farm_id) == 1:
        raise HTTPException(409, "A farm needs at least one owner")
    if m:
        m.role = body.role
    else:
        db.add(MembershipRow(farm_id=farm_id, user_id=member.id, role=body.role, created_at=_utcnow()))
    db.commit()
    return {"user_id": member.id, "role": body.role}


@router.delete("/farms/{farm_id}/members/{user_id}", status_code=204)
def remove_member(farm_id: int, user_id: int, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _require(db, user, farm_id, MEMBER_ADMIN_ROLES, "Farm")
    m = db.scalars(select(MembershipRow).where(MembershipRow.farm_id == farm_id, MembershipRow.user_id == user_id)).first()
    if m is None:
        raise HTTPException(404, "Not a member")
    if m.role == "owner" and _owner_count(db, farm_id) == 1:
        raise HTTPException(409, "A farm needs at least one owner")
    db.delete(m)
    db.commit()


def _owner_count(db: Session, farm_id: int) -> int:
    return len(db.scalars(select(MembershipRow).where(MembershipRow.farm_id == farm_id, MembershipRow.role == "owner")).all())


# ---- fields & seasons --------------------------------------------------------------

@router.post("/fields", status_code=201)
def create_field(body: FieldIn, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _require(db, user, body.farm_id, MANAGE_ROLES, "Farm")
    row = FieldRow(**body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, **body.model_dump()}


@router.get("/farms/{farm_id}/fields")
def list_fields(farm_id: int, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _require(db, user, farm_id, ROLES, "Farm")
    rows = db.scalars(select(FieldRow).where(FieldRow.farm_id == farm_id).order_by(FieldRow.name))
    return [{"id": f.id, "farm_id": f.farm_id, "name": f.name, "centroid_lat": f.centroid_lat,
             "centroid_lon": f.centroid_lon, "acres": f.acres, "nrd": f.nrd, "nrd_phase": f.nrd_phase,
             "soil_texture": f.soil_texture} for f in rows]


@router.post("/fields/{field_id}/seasons", status_code=201)
def create_season(field_id: int, body: SeasonIn, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _field_for(db, user, field_id, MANAGE_ROLES)
    row = SeasonRow(field_id=field_id, **body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "field_id": field_id, **body.model_dump()}


@router.get("/fields/{field_id}/seasons")
def list_seasons(field_id: int, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _field_for(db, user, field_id)
    rows = db.scalars(select(SeasonRow).where(SeasonRow.field_id == field_id).order_by(SeasonRow.year.desc()))
    return [{"id": r.id, "field_id": r.field_id, "year": r.year, "crop": r.crop, "variety": r.variety,
             "planting_date": r.planting_date.isoformat() if r.planting_date else None} for r in rows]


# ---- events (append-only) -----------------------------------------------------------

@router.post("/seasons/{season_id}/events", status_code=201)
def add_event(season_id: int, body: EventIn, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _season_for(db, user, season_id, WRITE_ROLES)
    if body.supersedes_id is not None:
        original = db.get(EventRow, body.supersedes_id)
        if original is None or original.season_id != season_id:
            raise HTTPException(404, "The event being corrected isn't in this season")
        if original.kind != body.payload.kind:
            raise HTTPException(422, f"A correction must be the same kind as the original ({original.kind})")
        newer = db.scalars(select(EventRow).where(EventRow.supersedes_id == original.id)).first()
        if newer is not None:
            raise HTTPException(409, f"Event {original.id} was already corrected by event {newer.id}; correct that one instead")
    row = EventRow(
        season_id=season_id,
        kind=body.payload.kind,
        occurred_at=body.occurred_at,
        payload=body.payload.model_dump(mode="json"),
        recorded_by=body.recorded_by or user.name or user.email or user.subject,
        supersedes_id=body.supersedes_id,
        entered_by_user_id=user.id,
        created_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _event_out(row)


@router.get("/seasons/{season_id}/events")
def list_events(
    season_id: int,
    kind: Optional[str] = Query(default=None),
    include_superseded: bool = Query(default=True, description="Also list rows that were later corrected"),
    db: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    _season_for(db, user, season_id)
    stmt = select(EventRow).where(EventRow.season_id == season_id).order_by(EventRow.occurred_at, EventRow.id)
    if kind:
        stmt = stmt.where(EventRow.kind == kind)
    rows = list(db.scalars(stmt))
    replaced_by = {r.supersedes_id: r.id for r in rows if r.supersedes_id is not None}
    if not include_superseded:
        rows = effective_events(rows)
    return [_event_out(r, replaced_by.get(r.id)) for r in rows]


@router.get("/seasons/{season_id}/diagnosis-context")
def diagnosis_context(
    season_id: int,
    as_of: Optional[dt.datetime] = Query(default=None),
    db: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    season = _season_for(db, user, season_id)
    cutoff = _to_naive_utc(as_of) if as_of else _utcnow()
    rows = db.scalars(
        select(EventRow)
        .where(EventRow.season_id == season_id, EventRow.occurred_at <= cutoff)
        .order_by(EventRow.occurred_at, EventRow.id)
    ).all()
    # Corrections replace the rows they supersede; the output shape is unchanged.
    return build_context(season, effective_events(list(rows)), cutoff)


@router.get("/seasons/{season_id}/nrd-check")
def nrd_check(season_id: int, nrd: Optional[str] = Query(default=None, description="Defaults to the field's NRD"),
              db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    """Missing records for an NRD's configured requirements (configs are unverified; see README)."""
    season = _season_for(db, user, season_id)
    field = db.get(FieldRow, season.field_id)
    key = nrd or (field.nrd if field else None)
    if not key:
        raise HTTPException(422, "Pass ?nrd=... or set the field's NRD")
    if key not in nrd_rules.NRDS:
        raise HTTPException(422, f"Unknown NRD; choose one of {', '.join(nrd_rules.NRDS)}")
    rows = list(db.scalars(select(EventRow).where(EventRow.season_id == season_id).order_by(EventRow.occurred_at)))
    info = {"nrd_phase": field.nrd_phase, "soil_texture": field.soil_texture} if field else {}
    return nrd_rules.check_season(season, rows, key, info)


# ---- drafts ----------------------------------------------------------------------------

FORM_TYPES = EVENT_KINDS
MAX_DRAFT_BYTES = 64_000


def _form_type_ok(form_type: str) -> None:
    if form_type not in FORM_TYPES:
        raise HTTPException(422, f"Unknown form type {form_type!r}")


def _draft_out(d: DraftRow, owner: Optional[UserRow] = None, include_content: bool = True) -> dict[str, Any]:
    out = {"form_type": d.form_type, "season_id": d.season_id, "user_id": d.user_id,
           "updated_at": d.updated_at.isoformat()}
    if owner is not None:
        out["user_name"] = owner.name or owner.email or owner.subject
    if include_content:
        out["content"] = d.content
    return out


@router.put("/seasons/{season_id}/drafts/{form_type}")
def save_draft(season_id: int, form_type: str, body: DraftIn, db: Session = Depends(get_session),
               user: UserRow = Depends(get_current_user)):
    _form_type_ok(form_type)
    _season_for(db, user, season_id, WRITE_ROLES)
    if len(json.dumps(body.content)) > MAX_DRAFT_BYTES:
        raise HTTPException(413, "Draft is too large")
    d = db.scalars(select(DraftRow).where(DraftRow.user_id == user.id, DraftRow.season_id == season_id,
                                          DraftRow.form_type == form_type)).first()
    if d is None:
        d = DraftRow(user_id=user.id, season_id=season_id, form_type=form_type, content=body.content, updated_at=_utcnow())
        db.add(d)
    else:
        d.content = body.content
        d.updated_at = _utcnow()
    db.commit()
    return _draft_out(d)


@router.get("/seasons/{season_id}/drafts/{form_type}")
def load_draft(season_id: int, form_type: str, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _form_type_ok(form_type)
    _season_for(db, user, season_id)
    d = db.scalars(select(DraftRow).where(DraftRow.user_id == user.id, DraftRow.season_id == season_id,
                                          DraftRow.form_type == form_type)).first()
    if d is None:
        raise HTTPException(404, "No draft")
    return _draft_out(d)


@router.delete("/seasons/{season_id}/drafts/{form_type}", status_code=204)
def discard_draft(season_id: int, form_type: str, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    _form_type_ok(form_type)
    _season_for(db, user, season_id)
    d = db.scalars(select(DraftRow).where(DraftRow.user_id == user.id, DraftRow.season_id == season_id,
                                          DraftRow.form_type == form_type)).first()
    if d is not None:
        db.delete(d)
        db.commit()


@router.get("/seasons/{season_id}/drafts")
def list_drafts(season_id: int, db: Session = Depends(get_session), user: UserRow = Depends(get_current_user)):
    """Everyone's open drafts for the season (who is working on which form). Content only for your own."""
    _season_for(db, user, season_id)
    rows = db.execute(select(DraftRow, UserRow).join(UserRow, UserRow.id == DraftRow.user_id)
                      .where(DraftRow.season_id == season_id).order_by(DraftRow.updated_at.desc())).all()
    return [_draft_out(d, u, include_content=d.user_id == user.id) for d, u in rows]


app = FastAPI(title="Ag field inputs")
app.include_router(router)
