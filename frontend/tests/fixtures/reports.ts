/** Payloads shaped exactly like the FastAPI responses, for schema/client tests. */
export const doneRunReport = {
  run_id: "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b",
  created_at: "2026-10-04T10:00:00Z",
  finished_at: "2026-10-04T10:00:05Z",
  status: "done",
  metric_definitions_version: 1,
  datasets: [{ name: "original", counts: { categories: 110, specialities: 900 } }],
  results: [
    {
      metric_id: "integrity",
      status: "ok",
      summary: "2 duplicate categories",
      details: { skipped_checks: {} },
    },
  ],
  error: null,
};

export const metricCatalogue = {
  integrity: { description: "Duplicates, orphans, dead ends", default_params: { target: null } },
};
