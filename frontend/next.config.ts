import type { NextConfig } from "next";

// Where the taxonomy pipeline FastAPI service (`taxonomy-pipeline serve`) is running.
const TAXONOMY_API_URL = process.env.TAXONOMY_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Proxy the pipeline API under our own origin so the browser never needs CORS.
  async rewrites() {
    return [{ source: "/api/pipeline/:path*", destination: `${TAXONOMY_API_URL}/:path*` }];
  },
};

export default nextConfig;
