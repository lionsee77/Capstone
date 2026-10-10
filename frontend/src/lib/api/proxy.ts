/**
 * Allowlist for the `/api/pipeline/*` proxy to the taxonomy pipeline API.
 *
 * The pipeline API has no authentication, and `POST /runs` makes the server
 * read whatever absolute dataset paths the caller sends. So the proxy forwards
 * only the endpoints the dashboard actually uses, and run creation is off
 * unless `PIPELINE_ALLOW_RUN_CREATION=true` is set for a trusted environment.
 * Add a route here (with a test) when the dashboard starts using a new one.
 *
 * Writes must also be same-origin JSON (`checkWriteRequest`): a `text/plain`
 * or form POST from another site needs no CORS preflight, so without this a
 * malicious page could make a visitor's browser start runs through the proxy.
 */

export type ProxyEnv = {
  TAXONOMY_API_URL?: string;
  PIPELINE_ALLOW_RUN_CREATION?: string;
};

export type ProxyRejection = { ok: false; status: 403 | 404 | 415; reason: string };
export type ProxyDecision = { ok: true; url: string } | ProxyRejection;

const CROSS_SITE: ProxyRejection = { ok: false, status: 403, reason: "Cross-site requests are not allowed" };

/** The request headers `checkWriteRequest` looks at; `Headers.get` semantics (null when absent). */
export type WriteRequestHeaders = Pick<Headers, "get">;

const DEFAULT_API_URL = "http://localhost:8000";
const RUN_ID = /^[A-Za-z0-9_-]{1,64}$/;
const LIMIT = /^\d{1,4}$/;
const DATASET_NAME = /^[\w.-]{1,128}$/;
const CONCEPT_LEVEL = /^(services|categories|specialities|tags)$/;
// Concept ids are usually a plain integer, but a proposed-but-not-yet-integrated
// concept can carry a temporary id like "skillsfuture:501" or "new:category:<hash>".
const CONCEPT_ID = /^[\w:.-]{1,200}$/;

/** `run_id` + `dataset`, required by both /taxonomy endpoints; null if either is missing or malformed. */
function requireRunAndDataset(searchParams: URLSearchParams): { run_id: string; dataset: string } | null {
  const runId = searchParams.get("run_id");
  const dataset = searchParams.get("dataset");
  if (!runId || !RUN_ID.test(runId) || !dataset || !DATASET_NAME.test(dataset)) return null;
  return { run_id: runId, dataset };
}

export function resolveUpstream(
  method: string,
  segments: string[],
  searchParams: URLSearchParams,
  env: ProxyEnv,
): ProxyDecision {
  const base = (env.TAXONOMY_API_URL ?? DEFAULT_API_URL).replace(/\/+$/, "");
  // Three segments covers every allowed route today (/taxonomy/{level}/{id}
  // is the deepest); a resource with fewer just leaves the later ones undefined.
  const [resource, a, b, ...rest] = segments;
  const notFound = { ok: false, status: 404, reason: "Not an allowed pipeline endpoint" } as const;
  if (rest.length > 0) return notFound;

  if (method === "GET" && resource === "metrics" && a === undefined) {
    return { ok: true, url: `${base}/metrics` };
  }
  if (method === "GET" && resource === "runs" && a === undefined) {
    const limit = searchParams.get("limit");
    return { ok: true, url: limit && LIMIT.test(limit) ? `${base}/runs?limit=${limit}` : `${base}/runs` };
  }
  if (method === "GET" && resource === "runs" && a !== undefined && b === undefined && RUN_ID.test(a)) {
    return { ok: true, url: `${base}/runs/${a}` };
  }
  if (method === "POST" && resource === "runs" && a === undefined) {
    if (env.PIPELINE_ALLOW_RUN_CREATION !== "true") {
      return { ok: false, status: 403, reason: "Run creation is disabled for this dashboard" };
    }
    return { ok: true, url: `${base}/runs` };
  }
  if (method === "GET" && resource === "taxonomy" && a === "search" && b === undefined) {
    const identity = requireRunAndDataset(searchParams);
    if (!identity) return notFound;
    const forwarded = new URLSearchParams(identity);
    const q = searchParams.get("q");
    if (q) forwarded.set("q", q);
    const level = searchParams.get("level");
    if (level) {
      if (!CONCEPT_LEVEL.test(level)) return notFound;
      forwarded.set("level", level);
    }
    const limit = searchParams.get("limit");
    if (limit && LIMIT.test(limit)) forwarded.set("limit", limit);
    return { ok: true, url: `${base}/taxonomy/search?${forwarded}` };
  }
  if (
    method === "GET" && resource === "taxonomy" &&
    a !== undefined && CONCEPT_LEVEL.test(a) && b !== undefined && CONCEPT_ID.test(b)
  ) {
    const identity = requireRunAndDataset(searchParams);
    if (!identity) return notFound;
    return { ok: true, url: `${base}/taxonomy/${a}/${encodeURIComponent(b)}?${new URLSearchParams(identity)}` };
  }
  return notFound;
}

/**
 * Guard for requests with a body (POST). Returns a rejection, or null when the
 * request is a same-origin JSON request.
 *
 * - `Content-Type` must be `application/json`. Cross-site pages can only send
 *   `text/plain`, form or multipart bodies without a preflight.
 * - `Sec-Fetch-Site`, which every current browser sends, must be `same-origin`.
 *   It is authoritative when present: it reflects the page's real origin even
 *   behind a reverse proxy that rewrites `Host`.
 * - Only when `Sec-Fetch-Site` is missing (older browsers) is `Origin`, if
 *   present, compared with the host the request was made to
 *   (`X-Forwarded-Host`, else `Host`). A cross-site page can't set
 *   `X-Forwarded-Host` without triggering a preflight, so trusting it here is
 *   safe. Non-browser clients such as curl send neither header and are allowed.
 */
export function checkWriteRequest(headers: WriteRequestHeaders): ProxyRejection | null {
  const mediaType = (headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== "application/json") {
    return { ok: false, status: 415, reason: "Request body must be application/json" };
  }

  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite !== null) {
    return fetchSite === "same-origin" ? null : CROSS_SITE;
  }

  const origin = headers.get("origin");
  if (origin !== null) {
    let originHost: string | null = null;
    try {
      originHost = new URL(origin).host;
    } catch {
      // `Origin: null` (sandboxed frames, file://) and junk values are treated as cross-site.
    }
    const requestHost = headers.get("x-forwarded-host") ?? headers.get("host");
    if (originHost === null || originHost !== requestHost) return CROSS_SITE;
  }
  return null;
}

/**
 * The full proxy decision for one request. The allowlist runs first, so unknown
 * paths stay 404 and disabled run creation stays 403. The write guard only
 * judges requests that would otherwise be forwarded.
 */
export function decideProxyRequest(
  method: string,
  segments: string[],
  searchParams: URLSearchParams,
  headers: WriteRequestHeaders,
  env: ProxyEnv,
): ProxyDecision {
  const allowed = resolveUpstream(method, segments, searchParams, env);
  if (!allowed.ok || method !== "POST") return allowed;
  return checkWriteRequest(headers) ?? allowed;
}
