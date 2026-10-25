# syntax=docker/dockerfile:1
# Stage 1: build the static React app.
FROM node:22.23.4-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
# Optional corporate-proxy CA bundle (see backend/Dockerfile); never stored in the image.
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY index.html tsconfig.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npx tsc --noEmit && npx vite build

# Stage 2: serve it with an unprivileged Nginx that also proxies /api to the backend.
FROM nginxinc/nginx-unprivileged:1.28.2-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
