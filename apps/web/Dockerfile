FROM node:24.16.0-bookworm-slim@sha256:2c87ef9bd3c6a3bd4b472b4bec2ce9d16354b0c574f736c476489d09f560a203 AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.10.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json tsconfig.base.json ./
COPY apps/web/ apps/web/
COPY packages/ packages/
RUN --mount=type=cache,id=sec-web-pnpm,target=/pnpm/store pnpm install --frozen-lockfile --store-dir=/pnpm/store
RUN pnpm --filter @sec-filing-verification/web build

FROM nginx:stable-alpine@sha256:f2e97a6801f504129e8027ff7d49e27fa59ef4f1ebfd97197dac8b194831cf3d
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
COPY infra/local/nginx.conf.template /etc/nginx/templates/default.conf.template
ENV NGINX_ENVSUBST_FILTER=MINIO_BUCKET
EXPOSE 8443
