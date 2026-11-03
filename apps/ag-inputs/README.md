# Ag field inputs

FastAPI + SQLAlchemy service for logging Nebraska corn, soybean, and wheat field records
(planting, fertilizer, foliar, irrigation, scouting, soil tests, harvest, and more), with
per-farm access control, append-only events, shared drafts, and NRD reporting checks.

Files:

| File | What it holds |
|---|---|
| `ag_inputs.py` | Tables, event payload schemas, API endpoints, record deadlines |
| `auth.py` | `AuthProvider` interface (`disabled`, `dev`, `cognito` placeholder) |
| `nrd_rules.py` | NRD configs (all unverified) and the season check |
| `reports.py` | Season CSV export and N summary |
| `streamlit_app.py`, `ui_client.py` | Streamlit prototype (calls the API over HTTP) |
| `manage.py` | Create a farm and assign legacy fields to it |
| `migrations/` | Alembic migrations (0001 = the original schema) |

## Settings (environment variables)

| Variable | Default | Use |
|---|---|---|
| `AG_DATABASE_URL` | `sqlite:///ag_monitor.db` | e.g. `postgresql+psycopg://...` for RDS. Keep it out of code. |
| `AG_AUTH_MODE` | `disabled` (every request 401) | `dev` = identity from `X-Dev-User` headers (local only); `cognito` = placeholder (501) |
| `AG_COGNITO_REGION`, `AG_COGNITO_USER_POOL_ID`, `AG_COGNITO_APP_CLIENT_ID` | — | Read by `CognitoAuth` once it's implemented |
| `AG_API_URL` | `http://localhost:8000` | Streamlit → API |
| `AG_API_TOKEN` | — | Bearer token the Streamlit page sends (for Cognito later) |
| `AG_UI_DEV_USER` | — | Pre-fills the dev sign-in box |
| `AG_UI_TZ` | `America/Chicago` | Time zone for entered times (`America/Denver` for the Panhandle) |
| `AG_UI_REFRESH_SECONDS` | `10` | Live panel refresh (`st.fragment(run_every=...)`) |

## Access model

- Fields belong to a farm. Roles per farm: `owner`, `operator`, `applicator`, `scout`, `viewer`.
- Read: any member. Write events and drafts: everyone but `viewer`. Fields and seasons: owner, operator.
  Members: owner. Non-members get 404 (farm existence isn't revealed).
- Events are append-only (ORM guard + database triggers). A correction is a new event with
  `supersedes_id`; it must be the same season and kind, and an event can be superseded once.
  Reads, the diagnosis context, NRD check, and exports use the corrected view.
- Drafts: one per user, form type, and season. `GET /api/seasons/{id}/drafts` shows everyone's
  open drafts (who's working on what); content is only returned for your own.

## Record deadlines (foliar)

`record_due_by` = occurred_at + 14 days for private applicators on restricted-use products, and
+48 hours for commercial/noncommercial applicators (all their applications, as written in the
brief). `recorded_late` = the row was created after that. Confirm both windows with the
Nebraska Department of Agriculture before relying on them.

## Upgrading an existing database

The original file called `create_all` at import. That's gone. For a database it already built:

```bash
alembic stamp 0001        # mark the original schema as present (no changes)
alembic upgrade head      # add farms/users/drafts, new columns, append-only triggers
python -c "import manage; f = manage.create_farm('My Farm', 'you@example.com'); manage.assign_fields(f, [1, 2, 3])"
```

No rows are deleted or rewritten. Existing fields keep `farm_id = NULL` (invisible through the API)
until assigned. The `you@example.com` user must have signed in once first.

## Run

```bash
cd apps/ag-inputs
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

export AG_DATABASE_URL=sqlite:///ag_monitor.db   # or your RDS URL
export AG_AUTH_MODE=dev                          # local only
alembic upgrade head
uvicorn ag_inputs:app --reload --port 8000       # API docs at http://localhost:8000/docs

# second terminal
AG_AUTH_MODE=dev AG_API_URL=http://localhost:8000 streamlit run streamlit_app.py
```

## Test

```bash
cd apps/ag-inputs && pytest -q
```

Tests use a temporary SQLite database and dev auth. They cover restricted-use validation, record
deadlines, roles, append-only corrections (ORM and triggers), drafts, NRD checks, CSV export and N
summary, migrations vs models (drift), and the Streamlit page (via `streamlit.testing`).

## NRD verification TODOs

`nrd_rules.py` holds one config per Natural Resources District. **None of it is verified.**
No requirements, due dates, or targets were entered from regulations; every value below is
empty (`None` / `()`) until someone confirms it with the NRD. Until then:

- `/api/seasons/{id}/nrd-check` reports `"verified": false` and `"requirements_configured": false`,
  and returns no missing items (it can't know what's required).
- The N-per-acre summary shows no NUE target.

### Every NRD (Upper Big Blue, Central Platte, Lower Loup, Lower Elkhorn, Republican River)

For each NRD, confirm and fill in:

1. **Required report fields** → `required_items` (keys from `DATA_ITEMS`: `crop`, `planting_date`,
   `fertilizer_n`, `manure_application`, `manure_analysis`, `soil_nitrate_test`,
   `irrigation_amounts`, `irrigation_water_nitrate`, `rainfall`, `yield`). If the NRD needs
   something not in that list, add a `DataItem` first.
2. **Report due date(s)** and how reports are submitted → `report_due`.
3. **Restrictions by management-area phase** (N timing, soil sampling, water sampling,
   certification) → `restrictions`.
4. **NUE or N-rate target**, if the NRD uses one, and its units → `nue_target_lb_n_per_bu`.
5. Set `verified=True` and record the source (document, date, contact) in the config.

### Central Platte NRD: no fall N on sandy soils (Phases 2 and 3)

Entered from your brief, not from NRD text (`source="Product owner, unverified"`). The check
returns `cannot_evaluate` until these are set:

- `fall_window`: the date range that counts as "fall" (and whether it extends into winter).
- `sandy_textures`: which soil textures count as "sandy".
- `exemptions`: any exempt products or practices (for example inhibitors or small rates).
- Whether Phases 2 and 3 are the complete list.

### Republican River

The basin has three NRDs: Upper, Middle, and Lower Republican. All three are configured
(`upper_republican`, `middle_republican`, `lower_republican`). Confirm which ones you farm in;
unused configs can stay as TODO.

### Fields

Each field records `nrd`, `nrd_phase`, and `soil_texture`. Confirm each field's
management-area phase with its NRD; phases change over time.
