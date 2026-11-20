# Taxonomy dashboard. Targets: `runtime` (production server) and `test` (vitest).
FROM oven/bun:1.2.6 AS deps
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

FROM node:22-slim AS source
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .

FROM source AS test
CMD ["node_modules/.bin/vitest", "run"]

FROM source AS build
RUN node_modules/.bin/next build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app ./
EXPOSE 3000
# TAXONOMY_API_URL, TAXONOMY_API_TOKEN and PIPELINE_ALLOW_RUN_CREATION are read at request time.
CMD ["node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000"]
