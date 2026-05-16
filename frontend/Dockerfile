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

# Replace the default nginx config with ours (includes /api/ proxy + SPA fallback)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Remove the default nginx config to avoid conflicts
RUN rm -f /etc/nginx/conf.d/default.conf.bak 2>/dev/null; exit 0

# nginx listens on 80; docker-compose maps this to the host port (3000)
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
