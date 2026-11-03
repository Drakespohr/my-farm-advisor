# Ag field inputs

FastAPI + SQLAlchemy service for logging Nebraska corn, soybean, and wheat field records
(planting, fertilizer, foliar, irrigation, scouting, soil tests, harvest, and more), with
per-farm access control, append-only events, shared drafts, and NRD reporting checks.

Run and test commands are at the end of this file.

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
