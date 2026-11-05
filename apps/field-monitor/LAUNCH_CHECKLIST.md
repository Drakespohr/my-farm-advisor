# Before launch checklist (Overacre)

Everything that must be done before real (paying) farmers use the app. Check items off in PRs as
they're done; add new ones as we find them. Costs are what we've found so far: **confirm on each
provider's site before budgeting.**

## Paid services and keys (set in the server's environment, never in code)

- [ ] **Open-Meteo commercial plan** for soil temperature (planting window). The free API is
      non-commercial only. Reported ~$29/month for 1M calls (Standard). Set `OPEN_METEO_API_KEY`;
      the app switches to the commercial endpoint automatically.
- [ ] **Google Places (New)** for address suggestions: Cloud project with billing, key restricted to
      Places API (New), budget alert, `GOOGLE_MAPS_API_KEY`, and `GOOGLE_PLACES_DAILY_LIMIT`.
      Reported ~10,000 free requests/month, then ~$2.83 per 1,000.
- [ ] **USDA AMS** cash bids key (`USDA_AMS_API_KEY`), already in use.
- [ ] Decide whether Photon (free OpenStreetMap suggestions, fair use) stays as the fallback or is
      self-hosted at higher volume.
- [ ] Check the **Esri World Imagery** basemap terms for commercial use (the map's satellite tiles).

## Rules to verify (they are TODO in the code, never guessed)

- [ ] State planting dates for corn, soybeans, and wheat (`STATE_CALENDAR` in `fieldmonitor/planting.py`).
- [ ] Soybean days/GDD from planting to emergence, and 50°F vs 60°F soybean soil threshold.
- [ ] Winter wheat planting dates (e.g. Hessian fly-free date) by state.
- [ ] Yield estimate accuracy bands: soybean/wheat field counts ±15% and uncalibrated satellite ±20%
      are our assumptions (`fieldmonitor/yields.py`).
- [ ] NRD reporting rules (see `apps/ag-inputs/README.md`, "NRD verification TODOs").
- [ ] Pesticide record windows (14 days private RUP, 48 h commercial/noncommercial) with the Nebraska
      Department of Agriculture.

## Security and hosting

- [ ] Serve over HTTPS and set `FIELD_MONITOR_COOKIE_SECURE=1`.
- [ ] Move from SQLite (`data/app.db`) to AWS RDS Postgres, or at minimum schedule backups of `data/`.
- [ ] Back up `data/app.db` before every upgrade (startup migrations run automatically).
- [ ] Keep `FIELD_MONITOR_MARKETPLACE` off unless the marketplace is meant to launch.
- [ ] ag-inputs: never run `AG_AUTH_MODE=dev` on a public server; finish Cognito (or retire ag-inputs
      once its data is imported into Overacre).

## Legal

- [ ] Lawyer review of licenses before launch (PlantVillage, COLMAP, classifier training data),
      per the project notes.
- [ ] Attribution shown in the app: Copernicus Sentinel-2, ESA WorldCover (CC BY 4.0), OpenStreetMap,
      Google ("Address suggestions by Google"), Esri, Open-Meteo, NOAA/NWS.
- [ ] Privacy policy and terms: farm data is private by default; who can see what (farm roles,
      one-field shares).
