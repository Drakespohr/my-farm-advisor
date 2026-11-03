"""
ag_inputs.py - farmer-entered field, season and management records for the
ag monitoring app.

Each record is a "field event" attached to a season. The payload is validated
against a typed schema for its kind (irrigation, fertilizer, foliar, ...) and
stored as JSON, so new kinds can be added without a database migration.

Add to the existing FastAPI app:
    from ag_inputs import router as inputs_router, init_db
    app.include_router(inputs_router)

Run on its own for testing:
    uvicorn ag_inputs:app --reload

Set AG_DATABASE_URL to a Postgres URL (e.g. AWS RDS) for production.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Annotated, Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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


class FieldRow(Base):
    __tablename__ = "fields"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    acres: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


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
    lab: Optional[str] = Field(default=None, max_length=120)
    depth_in: float = Field(default=6, gt=0, le=60)
    ph: Optional[float] = Field(default=None, ge=0, le=14)
    organic_matter_pct: Optional[float] = Field(default=None, ge=0, le=100)
    phosphorus_ppm: Optional[float] = Field(default=None, ge=0)
    potassium_ppm: Optional[float] = Field(default=None, ge=0)
    nitrate_n_ppm: Optional[float] = Field(default=None, ge=0)
    sulfur_ppm: Optional[float] = Field(default=None, ge=0)
    zinc_ppm: Optional[float] = Field(default=None, ge=0)


class Harvest(BaseModel):
    kind: Literal["harvest"]
    yield_bu_per_ac: float = Field(ge=0, le=1000)
    moisture_pct: Optional[float] = Field(default=None, ge=0, le=100)


EventPayload = Annotated[
    Union[
        Planting, StandCount, GrowthStage, Irrigation, Rainfall, SoilMoisture,
        Fertilizer, FoliarApplication, Scouting, SoilTest, Harvest,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class FieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    centroid_lat: float = Field(ge=-90, le=90)
    centroid_lon: float = Field(ge=-180, le=180)
    acres: Optional[float] = Field(default=None, gt=0)


class SeasonIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    crop: Literal["corn", "soybeans", "wheat"]
    variety: Optional[str] = Field(default=None, max_length=120)
    planting_date: Optional[dt.date] = None


class EventIn(BaseModel):
    occurred_at: dt.datetime
    payload: EventPayload

    @field_validator("occurred_at")
    @classmethod
    def normalize_and_check(cls, value: dt.datetime) -> dt.datetime:
        value = _to_naive_utc(value)
        if value > _utcnow() + dt.timedelta(hours=24):
            raise ValueError("occurred_at cannot be in the future")
        return value


# ---------------------------------------------------------------------------
# Diagnosis context
# ---------------------------------------------------------------------------

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
            soil_test = {k: v for k, v in p.items() if k != "kind" and v is not None}
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
    Base.metadata.create_all(engine)


def get_session():
    with SessionLocal() as session:
        yield session


def _get_season_or_404(db: Session, season_id: int) -> SeasonRow:
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return season


def _event_out(row: EventRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "season_id": row.season_id,
        "kind": row.kind,
        "occurred_at": row.occurred_at.isoformat(),
        "payload": row.payload,
    }


router = APIRouter(prefix="/api", tags=["field inputs"])


@router.post("/fields", status_code=201)
def create_field(body: FieldIn, db: Session = Depends(get_session)):
    row = FieldRow(**body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, **body.model_dump()}


@router.post("/fields/{field_id}/seasons", status_code=201)
def create_season(field_id: int, body: SeasonIn, db: Session = Depends(get_session)):
    if db.get(FieldRow, field_id) is None:
        raise HTTPException(status_code=404, detail="Field not found")
    row = SeasonRow(field_id=field_id, **body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "field_id": field_id, **body.model_dump()}


@router.post("/seasons/{season_id}/events", status_code=201)
def add_event(season_id: int, body: EventIn, db: Session = Depends(get_session)):
    _get_season_or_404(db, season_id)
    row = EventRow(
        season_id=season_id,
        kind=body.payload.kind,
        occurred_at=body.occurred_at,
        payload=body.payload.model_dump(mode="json"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _event_out(row)


@router.get("/seasons/{season_id}/events")
def list_events(
    season_id: int,
    kind: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
):
    _get_season_or_404(db, season_id)
    stmt = select(EventRow).where(EventRow.season_id == season_id).order_by(EventRow.occurred_at)
    if kind:
        stmt = stmt.where(EventRow.kind == kind)
    return [_event_out(r) for r in db.scalars(stmt)]


@router.get("/seasons/{season_id}/diagnosis-context")
def diagnosis_context(
    season_id: int,
    as_of: Optional[dt.datetime] = Query(default=None),
    db: Session = Depends(get_session),
):
    season = _get_season_or_404(db, season_id)
    cutoff = _to_naive_utc(as_of) if as_of else _utcnow()
    rows = db.scalars(
        select(EventRow)
        .where(EventRow.season_id == season_id, EventRow.occurred_at <= cutoff)
        .order_by(EventRow.occurred_at)
    ).all()
    return build_context(season, list(rows), cutoff)


init_db()
app = FastAPI(title="Ag field inputs")
app.include_router(router)
