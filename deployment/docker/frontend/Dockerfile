# syntax=docker/dockerfile:1.6

# Frontend Dockerfile - builds React app and serves with Nginx
# Build from repo root: docker build -f deployment/docker/frontend/Dockerfile .

# Build stage
FROM node:20-alpine AS build
WORKDIR /app

# Install deps (from repo root context)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy source
COPY frontend/index.html ./
COPY frontend/vite.config.js ./
COPY frontend/public ./public
COPY frontend/src ./src
COPY frontend/nginx-default.conf ./nginx-default.conf

# Optional: pass explicit backend base URL at build time (else same-origin is used)
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# Build
RUN npm run build

# Runtime stage
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Replace default server config with our SPA-friendly config
COPY --from=build /app/nginx-default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
