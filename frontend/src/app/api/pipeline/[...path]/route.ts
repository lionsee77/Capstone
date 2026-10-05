import type { NextRequest } from "next/server";

import { resolveUpstream } from "@/lib/api/proxy";

// Forwards allowlisted requests to the pipeline API; see `lib/api/proxy.ts` for why it is restricted.
async function forward(request: NextRequest, ctx: RouteContext<"/api/pipeline/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  const decision = resolveUpstream(request.method, path, request.nextUrl.searchParams, {
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
      headers: { "Content-Type": "application/json" },
      body: request.method === "POST" ? await request.text() : undefined,
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
