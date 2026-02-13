# Docker Frontend Networking Guide

This guide explains how frontend-backend communication works in Docker
deployments, covering both development and production scenarios.

## The Core Problem

When the frontend and backend run in separate Docker containers, the
browser cannot resolve Docker internal hostnames like
`shu-api-dev:8000`. The browser runs on the host machine, not inside
Docker, so API requests must target host-accessible URLs.

## Quick Start

**Development** (hot-reload):

```bash
make up-full-dev
```

**Production** (static build, local testing):

```bash
make up-full
```

## Architecture Patterns

### Development Mode (Vite Dev Server with Proxy)

The Docker dev environment uses a **proxy-based architecture** where
all browser-to-backend communication flows through the Vite dev server:

```text
Browser (localhost:3000)
    |
Vite Dev Server (shu-frontend-dev container)
    |-- /api/*  -> proxied to shu-api-dev:8000 (Docker internal)
    |-- /auth/* -> proxied to shu-api-dev:8000 (OAuth callbacks)
    +-- /*      -> served by Vite (React app with HMR)
```

This architecture:

- **Avoids CORS issues**: Browser makes same-origin requests to `:3000`
- **Enables OAuth**: OAuth callbacks route through the proxy so the
  popup and parent window share the same origin for `postMessage`

### Production Mode (Static Build with Nginx)

The production frontend is a **static build** served by nginx. Unlike
dev mode, there is no server-side proxy — the nginx config only serves
static files with SPA fallback (`try_files $uri /index.html`).

```text
Browser (localhost:3000 mapped to container port 80)
    |
Nginx (shu-frontend container)
    +-- Serves static files from /usr/share/nginx/html
        (no API proxy — nginx does NOT forward /api requests)

Browser makes direct API calls to VITE_API_BASE_URL
    |
Backend (localhost:8000 or external URL)
```

`VITE_API_BASE_URL` is baked into the JavaScript bundle at **build
time** via a Docker build arg. The browser makes direct requests to
this URL. If unset, the frontend uses same-origin (which only works
if a reverse proxy serves both frontend and API on the same host:port).

The docker-compose.yml currently sets
`VITE_API_BASE_URL: http://localhost:8000` as a build arg. This works
for local testing because the API container publishes port 8000 to the
host. For a real deployment:

- Use a reverse proxy (nginx, Traefik, etc.) in front of both
  frontend and backend on a single domain
- Set `VITE_API_BASE_URL` to the public API URL, or leave it unset
  to use same-origin behind the reverse proxy
- See [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) for production
  patterns

## Environment Variables (Dev Mode)

These are already configured in `docker-compose.yml`. They are shown
here for reference — you do not need to set them in `.env`.

### Backend (shu-api-dev)

```bash
# OAuth redirect URI must point to frontend port in Docker dev
OAUTH_REDIRECT_URI=http://localhost:3000/auth/callback
```

In Docker dev, OAuth providers redirect to the **frontend** (`:3000`),
which proxies the callback to the backend. This ensures the OAuth
popup and parent window share the same origin for `postMessage`.

### Frontend (shu-frontend-dev)

```bash
# Server-side proxy target (NOT exposed to browser)
# Uses Docker internal hostname
DEV_SERVER_API_PROXY_TARGET=http://shu-api-dev:8000

# DO NOT set VITE_API_BASE_URL in docker-compose for dev mode.
# If set, the browser will try direct requests, bypassing the proxy.
```

**Key Concept**:

- `DEV_SERVER_API_PROXY_TARGET` — server-side only, read by
  `vite.config.js` via `process.env`
- `VITE_API_BASE_URL` — client-side, exposed to browser via
  `import.meta.env.VITE_*`
- In Docker dev the proxy handles routing, so `VITE_API_BASE_URL`
  should NOT be set

## Vite Proxy Configuration

The Vite dev server (`frontend/vite.config.js`) resolves the proxy
target with a fallback chain:

```javascript
const proxyTarget =
  process.env.DEV_SERVER_API_PROXY_TARGET  // Docker dev
  || env.VITE_API_BASE_URL                 // local .env
  || 'http://localhost:8000';              // final fallback
```

It then proxies these routes:

```javascript
server: {
  proxy: {
    '/api':  { target: proxyTarget, changeOrigin: true },
    '/auth': { target: proxyTarget, changeOrigin: true },
  },
}
```

## OAuth Setup for Docker Dev

Shu uses a unified `OAUTH_REDIRECT_URI` for all providers. The
callback endpoint (`/auth/callback`) identifies the provider via the
`state` parameter, so the Docker networking setup is identical for
Google and Microsoft — no provider-specific configuration differences.

1. **Register the redirect URI with your OAuth provider(s)**:

   **Google** — Google Cloud Console -> APIs & Services -> Credentials
   -> OAuth 2.0 Client ID:
   - Add: `http://localhost:3000/auth/callback`
   - Keep existing: `http://localhost:8000/auth/callback` (for local
     non-Docker dev)

   **Microsoft** — Azure Portal -> App Registrations -> your app ->
   Authentication -> Redirect URIs:
   - Add: `http://localhost:3000/auth/callback`
   - Keep existing: `http://localhost:8000/auth/callback` (for local
     non-Docker dev)

2. **Verify backend configuration**:

   ```bash
   docker exec shu-api-dev env | grep OAUTH_REDIRECT_URI
   # Should show: OAUTH_REDIRECT_URI=http://localhost:3000/auth/callback
   ```

3. **Test OAuth flow**:
   - Open `http://localhost:3000`
   - Try connecting a Google or Microsoft account (Admin -> Plugins ->
     Connect Account)
   - OAuth popup should complete successfully

## Troubleshooting

### "redirect_uri_mismatch" error

**Cause**: OAuth redirect URI not registered in Google Cloud Console.

**Fix**: Add `http://localhost:3000/auth/callback` to authorized
redirect URIs.

### OAuth postMessage cross-origin errors

**Cause**: `VITE_API_BASE_URL` is set in docker-compose, causing the
browser to make direct requests to `:8000`.

**Fix**: Remove `VITE_API_BASE_URL` from `docker-compose.yml`
frontend environment.

### API calls return 404 or CORS errors

**Cause**: Vite proxy not configured correctly.

**Fix**:

1. Check `DEV_SERVER_API_PROXY_TARGET` is set in docker-compose
2. Verify `vite.config.js` has `/api` and `/auth` proxy routes
3. Check browser network tab — requests should go to
   `localhost:3000/api/*`, not `localhost:8000/api/*`

### Frontend can't connect to backend

**Cause**: Docker networking issue or backend not ready yet. Note that
`shu-frontend-dev` does not have a `depends_on` for `shu-api-dev`, so
the API may not be running when the frontend starts. The proxy will
return 502 errors until the backend is healthy.

**Fix**:

```bash
# Check if backend is accessible from frontend container
docker exec shu-frontend-dev node -e \
  "fetch('http://shu-api-dev:8000/api/v1/health/liveness').then(r=>r.text()).then(console.log)"

# Check Vite proxy logs
docker logs shu-frontend-dev
```

## Comparison Table

| Aspect | Local Dev | Docker Dev | Docker Prod |
| --- | --- | --- | --- |
| Backend URL | `localhost:8000` | `shu-api-dev:8000` (internal) | `localhost:8000` (default) |
| Frontend URL | `localhost:3000` | `localhost:3000` | `localhost:3000` (maps to :80) |
| Frontend server | Vite dev server | Vite dev server | nginx (static files) |
| API routing | Vite proxy or direct | Always proxied via Vite | Direct from browser |
| OAuth redirect | `localhost:8000/auth/cb` | `localhost:3000/auth/cb` | Depends on deployment |
| `VITE_API_BASE_URL` | Optional | Do NOT set | Optional build arg |
| `DEV_SERVER_API_PROXY_TARGET` | N/A | Required | N/A |
| Hot reload | Yes | Yes | No (rebuild required) |

Note: In Docker Prod, `VITE_API_BASE_URL` defaults to
`http://localhost:8000` in docker-compose.yml. If unset, the frontend
uses same-origin, which requires a reverse proxy serving both frontend
and API on the same host:port.

## Volume Mounts (Dev Mode)

The dev containers mount source code for hot-reload.

**Backend** (`shu-api-dev`):

- `backend/src:/app/src` — Python source code
- `backend/alembic:/app/alembic` — Alembic migrations
- `backend/scripts:/app/scripts` — Utility scripts
- `data:/app/data` — Data directory
- `plugins:/app/plugins` — Plugin source code

**Frontend** (`shu-frontend-dev`):

- `frontend/src:/app/src` — React source code
- `frontend/public:/app/public` — Static assets
- `frontend/package.json:/app/package.json` — Dependencies
- `frontend/package-lock.json:/app/package-lock.json` — Lock file
- `frontend/vite.config.js:/app/vite.config.js` — Vite config
- `frontend/index.html:/app/index.html` — HTML template
- `frontend-node-modules:/app/node_modules` — Named volume (persisted)

Changes to mounted source files trigger automatic reload.

## See Also

- [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) — Production
  deployment patterns
- [CONFIGURATION.md](../policies/CONFIGURATION.md) — Environment
  variable reference
- [frontend/README.md](../../frontend/README.md) — Frontend
  development guide
