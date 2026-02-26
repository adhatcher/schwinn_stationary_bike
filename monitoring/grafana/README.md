# Grafana Dashboards

Ready-to-import dashboards for Schwinn app Prometheus metrics (`schwinn_*`).

## Files

- `dashboards/schwinn-service-overview.json`
- `dashboards/schwinn-http-endpoint-performance.json`
- `dashboards/schwinn-import-chart-workload.json`
- `dashboards/schwinn-historical-workouts-api.json` (historical workout data via Flask API)

## Import

1. In Grafana: **Dashboards** -> **New** -> **Import**.
2. Upload one of the JSON files from `monitoring/grafana/dashboards/`.
3. When prompted for `DS_PROMETHEUS`, select your Prometheus datasource.
4. Repeat for the other dashboard files.

These dashboards assume Prometheus is scraping the app's `/metrics` endpoint and collecting the `schwinn_` metric series.

## Historical Data Dashboard (API)

`dashboards/schwinn-historical-workouts-api.json` reads workout history from:

- `GET /api/grafana/workouts`
- `GET /api/grafana/summary`

Requirements:

1. Install Grafana plugin: `yesoreyeram-infinity-datasource`.
2. Create an Infinity datasource in Grafana.
3. Import `dashboards/schwinn-historical-workouts-api.json` and map `DS_INFINITY` to that datasource.
4. Set dashboard variable `API Base URL` (default: `http://localhost:8080`) to your app URL.

Filters:

- `Fields`: multi-select for `Distance`, `Avg_Speed`, `Workout_Time`, `Total_Calories`, `Heart_Rate`, `RPM`, `Level`.
- Date range: use Grafana's global time picker; dashboard passes it to the API as `from` and `to`.
