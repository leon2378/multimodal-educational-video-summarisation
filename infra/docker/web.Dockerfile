# syntax=docker/dockerfile:1
# Web app: the Next.js standalone server. Build from the repo root:
#   docker build -f infra/docker/web.Dockerfile --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 .
# NEXT_PUBLIC_API_URL is the API's address as the browser sees it, baked in at build time, and so
# is NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY (public by design; without it, sign-in is off). With the
# key, the container also needs CLERK_SECRET_KEY in its environment when it runs.

FROM node:24-slim AS build
ENV NEXT_TELEMETRY_DISABLED=1 \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /app

# Dependencies first, so source edits don't invalidate this layer.
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm config set store-dir /pnpm-store && pnpm install --frozen-lockfile

COPY apps/web ./
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=""
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
RUN pnpm run build


FROM node:24-slim
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
