# Spaces Poker

Automated No-Limit Texas Hold'em for X Spaces. Play-money cash game with future-friendly currency abstraction.

## Architecture at a glance

- **Backend**: Python 3.11, FastAPI, SQLAlchemy + Alembic, asyncio table loops, ElevenLabs TTS
- **Frontend**: React + Vite + TypeScript, WebSockets for live state
- **Data**: Postgres (primary), Redis (pub/sub + sessions)
- **Infra**: One container image, deployable to AWS (ECS Fargate) or Azure (Container Apps) via Terraform
- **Auth**: X OAuth 2.0 PKCE only

## Layout

```
backend/
  app/
    engine/        # Pure-Python poker engine (no I/O)
    api/           # FastAPI routes + WebSocket handlers
    db/            # SQLAlchemy models, repositories
    services/      # Ledger, narration, X OAuth, table manager
    core/          # Config, logging, security
  tests/
    unit/          # Engine + service tests, no DB
    integration/   # DB + API tests
  alembic/         # Migrations
frontend/
  src/             # React app
infra/
  docker/          # Dockerfile + docker-compose for local dev
  aws/             # Terraform module: ECS Fargate + RDS + ElastiCache + ALB
  azure/           # Terraform module: Container Apps + Postgres Flex + Redis + App Gateway
.github/workflows/ # CI: tests, lint, image build
docs/              # Design notes, runbooks
```

## Build order

1. Engine (pure Python, fully tested) — `backend/app/engine/`
2. WebSocket server + table loop — `backend/app/api/ws.py`, `backend/app/services/table_manager.py`
3. Persistence + auth — `backend/app/db/`, `backend/app/services/auth.py`
4. Multi-table + table codes — already supported in the data model
5. ElevenLabs narration — `backend/app/services/narration.py`
6. Polish: timers, sit-out, hand history, chat

## Quickstart (local dev)

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose -f ../infra/docker/docker-compose.yml up -d  # Postgres + Redis
alembic upgrade head
uvicorn app.api.main:app --reload

# Frontend
cd frontend
npm install
npm run dev

# Run tests
cd backend
pytest
```

### Local ports

The docker-compose file maps Postgres to host port **15432** and Redis to host port **16379** (the containers internally still use 5432 and 6379). These non-standard host ports avoid two common Windows + WSL2 issues:

- Local Postgres installs frequently occupy 5432.
- Hyper-V/WSL2 reserves dynamic port ranges that often cover the 6000-7000 area, sometimes more. You can check yours with `netsh interface ipv4 show excludedportrange protocol=tcp` from PowerShell.

15432 and 16379 are well outside any reservation range I've seen reported. If they ever conflict, change both `infra/docker/docker-compose.yml` (host side of the mapping) and `backend/.env` together. CI and cloud deployments use the standard internal ports — only local-host ports differ.

## Authentication

Two modes, switched via the `AUTH_MODE` env var:

- `AUTH_MODE=fake` — `?as=<handle>` query string is the identity. No login needed. For dev and tests.
- `AUTH_MODE=x_oauth` — production. Real X OAuth 2.0 PKCE flow. Cookie-based sessions.
- `AUTH_MODE=both` — default. Tries the JWT cookie first, falls back to `?as=` if no cookie. Useful when you want OAuth working without breaking dev shortcuts.

### Setting up X OAuth (production)

1. Sign up at https://developer.x.com/, create a project + app.
2. App settings → User authentication settings → enable OAuth 2.0. Set:
   - Type of App: **Web App**
   - Callback URI: `https://your-domain.com/auth/callback`
   - Website URL: your frontend domain
3. Scopes needed: `tweet.read users.read offline.access` (the code requests these automatically).
4. Copy the Client ID and Client Secret into env vars:

```
X_CLIENT_ID=<client id>
X_CLIENT_SECRET=<client secret>
X_REDIRECT_URI=https://your-domain.com/auth/callback
JWT_SECRET=<a strong random string, 32+ bytes>
AUTH_MODE=x_oauth
ENV=prod
```

Setting `ENV=prod` enables the `Secure` cookie flag — your callback URL must be HTTPS in prod. In dev with `ENV=dev` (the default), cookies work over HTTP for `localhost`.

For multi-process deployments, set `PERSISTENCE_ENABLED=true`. On startup the app will install a Redis-backed OAuth state store (using `REDIS_URL`), which lets a user start the OAuth flow on one process and complete it on another. Without persistence enabled, the in-memory state store is used and OAuth across multiple processes will fail with "invalid or expired state".

### Sign-in UX

The sign-in screen reads `/auth/config` to decide what to show:

- **`AUTH_MODE=x_oauth`** — only the "Sign in with X" button. Real OAuth required.
- **`AUTH_MODE=fake`** — only a "Continue as &lt;handle&gt;" form. No OAuth attempted; pick any handle and you're in. The server issues a session cookie via `POST /auth/fake-login`.
- **`AUTH_MODE=both`** (default) — both options shown when X credentials are configured. If `X_CLIENT_ID`/`X_CLIENT_SECRET` are unset, only the dev form shows. This is the "I just want to run the app locally without setting up X OAuth" path.

Clicking "Sign in with X" preserves the current page: if you're at `/table/ABC234`, you land back at `/table/ABC234` after authenticating. The frontend passes `?next=<path>` to `/auth/login`; the server stashes that path with the PKCE state and honors it on `/auth/callback`. Only same-origin paths are accepted (`//evil.com`-style absolute URLs are rejected as a defense against open-redirect attacks).

The fake-login path is only reachable when `AUTH_MODE` allows it. In `x_oauth` mode the `/auth/fake-login` endpoint returns 404 even if someone calls it directly — defense in depth so a misconfigured prod deploy can't accidentally accept arbitrary handles.

## Deployment topology

Single-origin: one container, one port (8000), serves both the SPA and the API. No nginx or reverse-proxy needed for routing.

URL structure:

- `/` — SPA shell (React Router takes over client-side)
- `/replay/:id`, `/table/:code`, `/spectate/:code` — SPA client routes, served by the same SPA shell with a 200
- `/assets/*` — Vite-bundled JS/CSS (hashed filenames, cacheable forever)
- `/api/tables/*` — JSON API for table operations
- `/auth/*` — OAuth + fake-login endpoints
- `/ws/tables/{code}`, `/ws/spectate/{code}` — WebSocket endpoints
- `/health`, `/api/health` — health checks

The Dockerfile is multi-stage: stage 1 builds the frontend with `npm run build`, stage 2 copies `frontend/dist` into `/app/static` in the runtime image. The backend reads `STATIC_DIR` (defaulting to `/app/static` in the container) and serves the SPA from there. For any unmatched GET that isn't an API path, the backend returns `index.html` so React Router can handle the route client-side.

In dev, the frontend runs separately via `vite dev` on :5173 with a proxy that forwards `/api/*`, `/auth/*`, and `/ws/*` to the backend on :8000. The backend doesn't serve the SPA in dev (it can, if you point `STATIC_DIR` at `frontend/dist` after a build, but there's no reason to during normal dev).

## AI narration

Tables can opt in to live AI commentary. When `narration_enabled=true` is set on the create-table request, the server spawns a narrator consumer that subscribes to the table's event stream, produces commentary text, generates speech via ElevenLabs Flash v2.5, and broadcasts the resulting MP3 audio to anyone listening at `GET /api/audio/{code}/stream`.

Setup:

- Sign up at `https://elevenlabs.io` and grab an API key from the profile menu.
- Set `ELEVENLABS_API_KEY=<your-key>` on the backend environment.
- Optional: `ELEVENLABS_VOICE_ID=<voice-id>` to pick a non-default voice from the voice library (default is Rachel, ID `21m00Tcm4TlvDq8ikWAM`).
- Optional: `TTS_MAX_CHARS_PER_MIN_PER_TABLE` (default 1500) and `TTS_MAX_CHARS_PER_HOUR` (default 100000) to tune the cost caps.

Without the API key set, narration still works in "text-only" mode — the transcript endpoint captures the commentary, but no audio is generated. Useful for validating narration quality before paying for TTS.

URL surface:

- `GET /api/audio/{code}/status` — JSON with `narration_enabled`, `listener_count`, `tts_configured`, `transcript_lines`
- `GET /api/audio/{code}/transcript` — JSON with the last ~50 commentary lines
- `GET /api/audio/{code}/stream` — chunked `audio/mpeg` live stream. Open in any audio player or feed to OBS/VLC

The SPA route `/audio/{code}` is the listener UI: play/pause controls, a live transcript that auto-scrolls, the direct stream URL for OBS integration. The TablePage header shows a "🔊 narration" badge linking to this page when narration is enabled.

Cost protection: every TTS call goes through a character budget that caps per-table usage (rolling 60s window) and global usage (rolling 1hr window). Bug-induced runaway narration can't drain credits beyond the configured cap. Common phrases ("fold", "check") are cached process-locally so repeat synthesis is free. At default settings (1500 chars/min/table, 100k chars/hour global) the worst case is roughly $0.50/hour across all active tables.

### Routing audio to X Spaces

X doesn't have an API to inject audio into a Space — somebody has to host the Space from a real account, and your generated audio has to flow into that session's microphone. The pattern: run OBS on a laptop, add the `/api/audio/{code}/stream` URL as a Media Source, install VB-Cable (Windows) or BlackHole (Mac) as a virtual audio device, route OBS's output to that device, and select it as the mic input in the browser/app session hosting the Space. The poker audio then plays into the Space as if a person were speaking. This is a manual setup, not automatable.

## Legal posture

Play money only. No player-to-player transfers. No cash-out. The currency abstraction (`accounts.currency_type`) is structured so a future regulated real-money or crypto variant can be added behind the same engine, but **none of that code exists today** and adding it without proper licensing is not legal. See `docs/legal.md`.
