import type { NextRequest } from "next/server";

import { checkWriteRequest, resolveUpstream } from "@/lib/api/proxy";

// Forwards allowlisted requests to the pipeline API; see `lib/api/proxy.ts` for why it is restricted.
async function forward(request: NextRequest, ctx: RouteContext<"/api/pipeline/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  const hasBody = request.method === "POST";
  const rejection = hasBody ? checkWriteRequest(request.headers) : null;
  const decision = rejection ?? resolveUpstream(request.method, path, request.nextUrl.searchParams, {
    TAXONOMY_API_URL: process.env.TAXONOMY_API_URL,
    PIPELINE_ALLOW_RUN_CREATION: process.env.PIPELINE_ALLOW_RUN_CREATION,
  });
  if (!decision.ok) {
    // Same `{detail}` shape as FastAPI errors, so the client reports it the same way.
    return Response.json({ detail: decision.reason }, { status: decision.status });
  }

  try {
    const upstream = await fetch(decision.url, {
      method: request.method,
      // Forward the caller's Content-Type (checked above) rather than vouching for the body ourselves.
      headers: hasBody ? { "Content-Type": request.headers.get("content-type") ?? "" } : undefined,
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return Response.json({ detail: "The taxonomy pipeline API is unreachable" }, { status: 502 });
  }
}

export { forward as GET, forward as POST };
