FROM node:22.14.0-alpine AS builder

ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml /app/
COPY apps/web/package.json /app/apps/web/package.json
RUN pnpm install --frozen-lockfile

COPY apps/web /app/apps/web
RUN pnpm --filter @prediction-market/web build

FROM node:22.14.0-alpine AS runtime

ENV HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production \
    PORT=3000

WORKDIR /app/apps/web
COPY --from=builder --chown=node:node /app/apps/web/.next/standalone /app
COPY --from=builder --chown=node:node /app/apps/web/.next/static /app/apps/web/.next/static
COPY --from=builder --chown=node:node /app/apps/web/public /app/apps/web/public
USER node

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["node", "-e", "fetch('http://127.0.0.1:3000').then((response) => { if (!response.ok) process.exit(1) }).catch(() => process.exit(1))"]
CMD ["node", "server.js"]
