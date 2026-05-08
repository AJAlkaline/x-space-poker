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

## Legal posture

Play money only. No player-to-player transfers. No cash-out. The currency abstraction (`accounts.currency_type`) is structured so a future regulated real-money or crypto variant can be added behind the same engine, but **none of that code exists today** and adding it without proper licensing is not legal. See `docs/legal.md`.
