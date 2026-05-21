# ─────────────────────────────────────────────────────────────────────────────
# Memora — frontend image
# Stage 1: build the React app with Vite
# Stage 2: serve the built dist/ via nginx
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first (cached layer — only invalidated when package files change)
COPY package.json package-lock.json* ./
RUN npm ci --prefer-offline

# Copy source
COPY . .

# Build-time environment variables injected by docker-compose build args.
# VITE_API_BASE_URL is intentionally blank: all API calls use relative /api/*
# paths, which nginx proxies to the backend — no hardcoded hostname needed.
ARG VITE_API_BASE_URL=""
ARG VITE_API_KEY=changeme
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_API_KEY=$VITE_API_KEY

RUN npm run build

# ── Stage 2: serve ────────────────────────────────────────────────────────────
FROM nginx:alpine

# Copy the Vite production build
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config as a template — PORT is substituted at container startup
COPY nginx.conf /etc/nginx/conf.d/default.conf.template

# Default port for local/docker-compose use; Railway overrides this with its own PORT
ENV PORT=80
# Default backend URL for docker-compose; Railway/prod overrides with the actual backend service URL
ENV BACKEND_URL=http://backend:8000

# Substitute $PORT into the nginx config then start nginx
CMD ["/bin/sh", "-c", "export NGINX_RESOLVER=$(awk '/^nameserver/ && $2 ~ /^[0-9]/{print $2; exit}' /etc/resolv.conf); export NGINX_RESOLVER=${NGINX_RESOLVER:-8.8.8.8} && envsubst '$PORT $BACKEND_URL $NGINX_RESOLVER' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf && exec nginx -g 'daemon off;'"]
