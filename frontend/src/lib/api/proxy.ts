/**
 * Allowlist for the `/api/pipeline/*` proxy to the taxonomy pipeline API.
 *
 * The pipeline API requires a shared token (`TAXONOMY_API_TOKEN`). The proxy
 * adds it server-side (`upstreamHeaders`), so it never reaches the browser,
 * which means anyone who can load the dashboard acts with that token. So the
 * proxy still forwards only the endpoints the dashboard actually uses, and run
 * creation stays off unless `PIPELINE_ALLOW_RUN_CREATION=true` is set for a
 * trusted environment. Add a route here (with a test) when the dashboard starts
 * using a new one.
 *
 * Writes must also be same-origin JSON (`checkWriteRequest`): a `text/plain`
 * or form POST from another site needs no CORS preflight, so without this a
 * malicious page could make a visitor's browser start runs through the proxy.
 */

export type ProxyEnv = {
  TAXONOMY_API_URL?: string;
  PIPELINE_ALLOW_RUN_CREATION?: string;
  TAXONOMY_API_TOKEN?: string;
};

export type ProxyRejection = { ok: false; status: 403 | 404 | 415; reason: string };
export type ProxyDecision = { ok: true; url: string } | ProxyRejection;

const CROSS_SITE: ProxyRejection = { ok: false, status: 403, reason: "Cross-site requests are not allowed" };

/** The request headers `checkWriteRequest` looks at; `Headers.get` semantics (null when absent). */
export type WriteRequestHeaders = Pick<Headers, "get">;

const DEFAULT_API_URL = "http://localhost:8000";
const RUN_ID = /^[A-Za-z0-9_-]{1,64}$/;
const LIMIT = /^\d{1,4}$/;
// Dataset names come straight from the pipeline config (`name: str`, no
// charset restriction - e.g. "Original (NUS)" is valid), so this only bounds
// length and excludes control characters. It's forwarded as a query value
// via `URLSearchParams`, which percent-encodes it, not placed in the path.
const DATASET_NAME = /^[^\x00-\x1f\x7f]{1,128}$/;
const CONCEPT_LEVEL = /^(services|categories|specialities|tags)$/;
// Concept ids are usually a plain integer, but a proposed-but-not-yet-integrated
// concept can carry a temporary id like "skillsfuture:501" or "new:category:<hash>".
const CONCEPT_ID = /^[\w:.-]{1,200}$/;
const ROUND_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;
// An uploaded file's own name; the API checks its type, and it is forwarded percent-encoded as one path segment.
const ROUND_FILE = /^(?!\.)[^/\\\x00-\x1f\x7f]{1,200}$/;
const LOOKUP_TERM_MAX = 200;

/** The query `GET /lookup` may forward, or null if any of it is malformed. Unknown parameters are dropped. */
function lookupQuery(searchParams: URLSearchParams): URLSearchParams | null {
  const q = searchParams.get("q");
  if (!q || q.length > LOOKUP_TERM_MAX) return null;
  const forwarded = new URLSearchParams({ q });
  const level = searchParams.get("level");
  if (level) {
    if (!CONCEPT_LEVEL.test(level)) return null;
    forwarded.set("level", level);
  }
  const semantic = searchParams.get("semantic");
  if (semantic) {
    if (semantic !== "true") return null;
    forwarded.set("semantic", semantic);
  }
  return forwarded;
}

/** `run_id` + `dataset`, required by both /taxonomy endpoints; null if either is missing or malformed. */
function requireRunAndDataset(searchParams: URLSearchParams): { run_id: string; dataset: string } | null {
  const runId = searchParams.get("run_id");
  const dataset = searchParams.get("dataset");
  if (!runId || !RUN_ID.test(runId) || !dataset || !DATASET_NAME.test(dataset)) return null;
  return { run_id: runId, dataset };
}

/**
 * `/rounds` routes. Reads are always forwarded; anything that changes a round needs the same switch as
 * creating a run, since it can start stages that write files and, if the server allows it, spend LLM credits.
 * Null means not an allowed route.
 */
function resolveRound(
  method: string, base: string, id: string | undefined, action: string | undefined, rest: string[],
  searchParams: URLSearchParams, env: ProxyEnv,
): ProxyDecision | null {
  if (id !== undefined && id !== "options" && !ROUND_ID.test(id)) return null;
  const root = `${base}/rounds`;
  const encoded = rest[0];
  if (encoded !== undefined && (rest.length !== 1 || !ROUND_FILE.test(encoded))) return null;
  let url: string | null = null;
  if (method === "GET") {
    if (id === undefined || id === "options") url = action === undefined ? `${root}${id ? "/options" : ""}` : null;
    else if (action === undefined) url = `${root}/${id}`;
    else if (action === "config" && !encoded) url = `${root}/${id}/config`;
    else if (action === "files" && !encoded) url = `${root}/${id}/files`;
    else if (action === "files" && encoded) {
      const sheet = searchParams.get("sheet");
      const query = sheet && DATASET_NAME.test(sheet) ? `?${new URLSearchParams({ sheet })}` : "";
      url = `${root}/${id}/files/${encodeURIComponent(encoded)}${query}`;
    }
  } else if (id !== undefined && id !== "options") {
    if (method === "POST" && action === "run") url = `${root}/${id}/run`;
    else if (method === "PUT" && action === "config" && !encoded) url = `${root}/${id}/config`;
    else if ((method === "PUT" || method === "DELETE") && action === "files" && encoded) {
      url = `${root}/${id}/files/${encodeURIComponent(encoded)}`;
    } else if (method === "DELETE" && action === undefined) url = `${root}/${id}`;
  } else if (method === "POST" && id === undefined && action === undefined) url = root;
  if (url === null) return null;
  if (method !== "GET" && env.PIPELINE_ALLOW_RUN_CREATION !== "true") {
    return { ok: false, status: 403, reason: "Changing rounds is disabled for this dashboard" };
  }
  return { ok: true, url };
}

export function resolveUpstream(
  method: string,
  segments: string[],
  searchParams: URLSearchParams,
  env: ProxyEnv,
): ProxyDecision {
  const base = (env.TAXONOMY_API_URL ?? DEFAULT_API_URL).replace(/\/+$/, "");
  // Three segments covers every allowed route except /runs/{before}/compare/{after}, which checks its
  // fourth itself; a resource with fewer just leaves the later ones undefined.
  const [resource, a, b, ...rest] = segments;
  const notFound = { ok: false, status: 404, reason: "Not an allowed pipeline endpoint" } as const;
  if (rest.length > 0 && !(resource === "runs" && b === "compare") && !(resource === "rounds" && b === "files")) {
    return notFound;
  }

  if (method === "GET" && resource === "proposals" && b === undefined) {
    const mergeRun = searchParams.get("merge_run");
    if (!mergeRun || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(mergeRun)) return notFound;
    if (a !== undefined && !/^[A-Za-z0-9][A-Za-z0-9:._-]{0,199}$/.test(a)) return notFound;
    const forwarded = new URLSearchParams({ merge_run: mergeRun });
    if (a === undefined) {
      const level = searchParams.get("level");
      if (level !== null) {
        if (level !== "category" && level !== "speciality") return notFound;
        forwarded.set("level", level);
      }
      for (const key of ["offset", "limit"]) {
        const value = searchParams.get(key);
        if (value !== null) {
          if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) return notFound;
          if (key === "limit" && (Number(value) < 1 || Number(value) > 200)) return notFound;
          forwarded.set(key, value);
        }
      }
    }
    const path = a === undefined ? "proposals" : `proposals/${encodeURIComponent(a)}`;
    return { ok: true, url: `${base}/${path}?${forwarded}` };
  }

  if (method === "GET" && resource === "metrics" && a === undefined) {
    return { ok: true, url: `${base}/metrics` };
  }
  if (method === "GET" && resource === "runs" && a === undefined) {
    const limit = searchParams.get("limit");
    return { ok: true, url: limit && LIMIT.test(limit) ? `${base}/runs?limit=${limit}` : `${base}/runs` };
  }
  if (method === "GET" && resource === "runs" && a !== undefined && b === undefined && RUN_ID.test(a)) {
    // `details=false` is the only query forwarded: it trims a report the dashboard only summarises.
    const light = searchParams.get("details") === "false";
    return { ok: true, url: light ? `${base}/runs/${a}?details=false` : `${base}/runs/${a}` };
  }
  if (resource === "rounds") return resolveRound(method, base, a, b, rest, searchParams, env) ?? notFound;
  if (method === "GET" && resource === "lookup" && a === undefined) {
    const forwarded = lookupQuery(searchParams);
    return forwarded ? { ok: true, url: `${base}/lookup?${forwarded}` } : notFound;
  }
  if (method === "GET" && resource === "lineage" && a === undefined) {
    return { ok: true, url: `${base}/lineage` };
  }
  // /runs/{before}/compare/{after}: four segments, the only route that deep. Subjects are optional
  // dataset names, forwarded as query values (percent-encoded), never as path segments.
  if (method === "GET" && resource === "runs" && b === "compare") {
    const after = rest[0];
    if (rest.length !== 1 || !RUN_ID.test(a) || !RUN_ID.test(after)) return notFound;
    const forwarded = new URLSearchParams();
    for (const key of ["before_subject", "after_subject"]) {
      const value = searchParams.get(key);
      if (value !== null) {
        if (!DATASET_NAME.test(value)) return notFound;
        forwarded.set(key, value);
      }
    }
    const query = forwarded.size > 0 ? `?${forwarded}` : "";
    return { ok: true, url: `${base}/runs/${a}/compare/${after}${query}` };
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
export function checkWriteRequest(
  headers: WriteRequestHeaders, body: "json" | "binary" | "none" = "json",
): ProxyRejection | null {
  const mediaType = (headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
  if (body === "json" && mediaType !== "application/json") {
    return { ok: false, status: 415, reason: "Request body must be application/json" };
  }
  // A cross-site page can't send either without a preflight, and the same-origin check below still applies.
  if (body === "binary" && mediaType !== "application/octet-stream") {
    return { ok: false, status: 415, reason: "An upload must be application/octet-stream" };
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
 * Headers for the forwarded request: the caller's `Content-Type` on a body
 * (already checked by `checkWriteRequest`), and the API token when one is
 * configured. Nothing else from the browser's request is passed on.
 */
export function upstreamHeaders(contentType: string | null, env: ProxyEnv): Record<string, string> {
  const headers: Record<string, string> = {};
  if (contentType !== null) headers["Content-Type"] = contentType;
  if (env.TAXONOMY_API_TOKEN) headers.Authorization = `Bearer ${env.TAXONOMY_API_TOKEN}`;
  return headers;
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
  if (!allowed.ok || method === "GET") return allowed;
  const isUpload = method === "PUT" && segments[2] === "files";
  const isBare = method === "DELETE" || (segments[0] === "rounds" && segments[2] === "run");
  return checkWriteRequest(headers, isUpload ? "binary" : isBare ? "none" : "json") ?? allowed;
}
