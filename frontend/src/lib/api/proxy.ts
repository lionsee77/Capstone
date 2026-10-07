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
