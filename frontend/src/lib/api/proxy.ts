/**
 * Allowlist for the `/api/pipeline/*` proxy to the taxonomy pipeline API.
 *
 * The pipeline API has no authentication, and `POST /runs` makes the server
 * read whatever absolute dataset paths the caller sends. So the proxy forwards
 * only the endpoints the dashboard actually uses, and run creation is off
 * unless `PIPELINE_ALLOW_RUN_CREATION=true` is set for a trusted environment.
 * Add a route here (with a test) when the dashboard starts using a new one.
 */

export type ProxyEnv = {
  TAXONOMY_API_URL?: string;
  PIPELINE_ALLOW_RUN_CREATION?: string;
};

export type ProxyDecision = { ok: true; url: string } | { ok: false; status: 403 | 404; reason: string };

const DEFAULT_API_URL = "http://localhost:8000";
const RUN_ID = /^[A-Za-z0-9_-]{1,64}$/;
const LIMIT = /^\d{1,4}$/;

export function resolveUpstream(
  method: string,
  segments: string[],
  searchParams: URLSearchParams,
  env: ProxyEnv,
): ProxyDecision {
  const base = (env.TAXONOMY_API_URL ?? DEFAULT_API_URL).replace(/\/+$/, "");
  const [resource, id, ...rest] = segments;
  const notFound = { ok: false, status: 404, reason: "Not an allowed pipeline endpoint" } as const;
  if (rest.length > 0) return notFound;

  if (method === "GET" && resource === "metrics" && id === undefined) {
    return { ok: true, url: `${base}/metrics` };
  }
  if (method === "GET" && resource === "runs" && id === undefined) {
    const limit = searchParams.get("limit");
    return { ok: true, url: limit && LIMIT.test(limit) ? `${base}/runs?limit=${limit}` : `${base}/runs` };
  }
  if (method === "GET" && resource === "runs" && id !== undefined && RUN_ID.test(id)) {
    return { ok: true, url: `${base}/runs/${id}` };
  }
  if (method === "POST" && resource === "runs" && id === undefined) {
    if (env.PIPELINE_ALLOW_RUN_CREATION !== "true") {
      return { ok: false, status: 403, reason: "Run creation is disabled for this dashboard" };
    }
    return { ok: true, url: `${base}/runs` };
  }
  return notFound;
}
