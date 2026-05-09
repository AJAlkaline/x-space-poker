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

## Legal posture

Play money only. No player-to-player transfers. No cash-out. The currency abstraction (`accounts.currency_type`) is structured so a future regulated real-money or crypto variant can be added behind the same engine, but **none of that code exists today** and adding it without proper licensing is not legal. See `docs/legal.md`.
