# Grafana Dashboards

Ready-to-import dashboards for Schwinn app Prometheus metrics (`schwinn_*`).

## Files

- `dashboards/schwinn-service-overview.json`
- `dashboards/schwinn-http-endpoint-performance.json`
- `dashboards/schwinn-import-chart-workload.json`

## Import

1. In Grafana: **Dashboards** -> **New** -> **Import**.
2. Upload one of the JSON files from `monitoring/grafana/dashboards/`.
3. When prompted for `DS_PROMETHEUS`, select your Prometheus datasource.
4. Repeat for the other dashboard files.

These dashboards assume Prometheus is scraping the app's `/metrics` endpoint and collecting the `schwinn_` metric series.
