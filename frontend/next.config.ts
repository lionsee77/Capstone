import type { NextConfig } from "next";

// The pipeline API is reached through the allowlisted route handler at
// `src/app/api/pipeline/[...path]/route.ts`, not a blanket rewrite.
const nextConfig: NextConfig = {};

export default nextConfig;
