# Spaces Poker

Play-money No-Limit Texas Hold'em designed to be played while listening to an X Space. The headline feature is **live AI narration** — an event-driven commentator that calls the action in real time, synthesized to speech via ElevenLabs and streamed to the X Space host and to a Twitch broadcast.

This file is the context document for Claude (and any new contributor). It captures architecture, conventions, and the gotchas you'd otherwise discover painfully. Read it before making changes.

---

## At a glance

- **Backend**: Python 3.11, FastAPI + asyncio, SQLAlchemy + Alembic for persistence (optional). Pure-Python NLHE engine.
- **Frontend**: React 18 + Vite + TypeScript. Inline-styled with a small `index.css` for global rules + mobile breakpoints. Framer Motion for table animations.
- **Deploy**: AWS ECS Express Mode (Fargate, 0.25 vCPU / 0.5 GB), ALB-fronted, ECR for images. Single container per task. Currently us-east-1.
- **Live URL**: `https://poker.interesting-times-gang.com`
- **AWS account**: `928170058111` (ROOT login). Cost ~$30/month (ALB + Fargate are most of it).
- **Auth modes**: `fake` (default, query-string `?as=handle` to fake-login), `oauth` (real X), or `both`.

---

## Repository layout

```
poker/
├── README.md
├── CLAUDE.md                         # this file
├── docs/
├── backend/
│   ├── pyproject.toml                # ruff config + pytest config inline
│   ├── alembic.ini, alembic/         # migrations (only used when persistence enabled)
│   ├── app/
│   │   ├── api/                      # FastAPI routes
│   │   │   ├── main.py               # app factory, lifespan, SPA fallback
│   │   │   ├── auth.py               # /auth/me, /auth/fake-login, /auth/x/* (OAuth)
│   │   │   ├── tables.py             # /api/tables, /join, /top_off, /leave
│   │   │   ├── ws.py                 # /ws/tables/{code}, /ws/spectate/{code}, /ws/audio/{code}
│   │   │   └── audio.py              # /api/audio/{code}/{status,transcript,stream}
│   │   ├── core/                     # config, security helpers
│   │   ├── db/                       # SQLAlchemy models, session
│   │   ├── engine/                   # pure-Python NLHE — no async, no I/O
│   │   │   ├── cards.py              # Card, Rank, Suit, Deck
│   │   │   ├── state.py              # GameState, Player, Pot, BettingRound
│   │   │   ├── table.py              # deal_hand, apply_action, legal_actions
│   │   │   ├── pots.py               # side-pot calculation
│   │   │   └── evaluator.py          # evaluate_hand → HandStrength (rank, score, best_five, description)
│   │   └── services/                 # everything else with state or I/O
│   │       ├── table_manager.py      # central run loop per table — long file, lots happens here
│   │       ├── event_bus.py          # public + private pub/sub for a table
│   │       ├── events.py             # event dataclasses (HandStarted, ActionApplied, etc.)
│   │       ├── wire.py               # event → JSON for WebSocket clients
│   │       ├── narrator.py           # stateful per-table commentary generator (text)
│   │       ├── tts.py                # ElevenLabs Flash v2.5 client with LRU cache + budget
│   │       ├── audio_bus.py          # per-table audio fan-out (byte stream + clip stream)
│   │       ├── narrator_consumer.py  # event bus → narrator → tts → audio bus
│   │       ├── persistence.py        # ledger, hand_history (optional)
│   │       ├── persistence_consumer.py
│   │       ├── replay.py             # reconstruct hand from stored events
│   │       ├── oauth.py              # X OAuth 2.0 PKCE flow
│   │       └── recovery.py           # crash-recovery on startup
│   └── tests/
│       ├── unit/                     # pure engine tests
│       └── integration/              # TestClient + WebSocket tests
│           └── conftest.py           # READ THIS — autouse cleanup fixture
├── frontend/
│   ├── package.json
│   ├── vite.config.ts                # dev proxies /api, /auth, /ws to :8000
│   ├── index.html                    # OG/Twitter meta tags here
│   └── src/
│       ├── main.tsx                  # router setup
│       ├── App.tsx                   # login gate + outer layout
│       ├── index.css                 # global styles + mobile breakpoints
│       ├── pages/
│       │   ├── LobbyPage.tsx
│       │   ├── TablePage.tsx         # the big one — owns ws state, table layout
│       │   ├── SpectatePage.tsx
│       │   ├── ReplayPage.tsx
│       │   └── AudioPage.tsx         # /audio/{code} — listener-only page
│       ├── components/
│       │   ├── TableView.tsx         # table + animations (chip flights, card deal, pulse)
│       │   ├── ActionBar.tsx         # bet/raise UI — hooks-order trap here
│       │   ├── ActionTimer.tsx
│       │   ├── EventLog.tsx
│       │   ├── HoleCards.tsx         # private cards + "your best hand right now"
│       │   └── ChipFlight.tsx        # framer-motion overlay for chips → pot
│       └── lib/
│           ├── types.ts              # wire types — PublicState, PrivateState, ServerMessage
│           ├── eventLog.ts           # event → human-readable log line
│           ├── useSession.ts         # /auth/me → handle
│           └── useTableSocket.ts     # WS lifecycle, reconnect, send queue
└── infra/
    ├── aws/
    │   ├── main.tf                   # Terraform: ALB, ECS service, security groups, etc.
    │   └── README.md
    └── docker/
        ├── Dockerfile
        └── docker-compose.yml        # local dev
```

---

## Architecture

### Game flow (high level)

```
Browser  ─── WS ───▶  /ws/tables/{code}  ─── action ───▶  TableManager.run_loop
                                                              │
              ◀── public events (state_update, action_applied,
                  hand_started, hand_complete) ──── event_bus
              ◀── private events (your hole cards, your_turn) ──
```

Each table runs in its own asyncio task inside the FastAPI process. The task:

1. Waits for actions on an inbound queue (one per seat).
2. Applies them via `engine.table.apply_action` — pure function, no side effects.
3. Publishes events (public + private) via `event_bus`.
4. Times out unresponsive players via `action_timer` logic.
5. Auto-deals next hand after a configurable inter-hand pause (3s normally, 10s after showdown).

The engine is strictly pure: `deal_hand(table, seats, button, deck) → state`, `apply_action(state, action, deck) → state`. No I/O, no side effects. This makes engine tests cheap and means the whole game state can be serialized/replayed deterministically from action history + deck seed.

### Narration pipeline

```
Action applied → ActionAppliedEvent → narrator_consumer → narrator.on_action() → text line
                                                              │
                                                              ▼
                                                          tts.synthesize(text)
                                                              │
                                                              ▼
                                                          audio_bus.publish(mp3_bytes, text)
                                                              │
                          ┌───────────────────────────────────┼─────────────────────────────┐
                          ▼                                   ▼                             ▼
       /api/audio/{code}/stream (HTTP)              /ws/audio/{code} (WS clip)      transcript
       Continuous stream with silence              One JSON message per clip        in-memory log
       keepalive. For OBS / VLC.                   with base64 audio. For SPA.
```

- The HTTP stream sends a small silent-MP3 frame every 500 ms to keep the connection alive between commentary lines.
- The WS clip path sends one clip per real publication. **No keepalive silence** — this was the audio-latency fix. Browsers buffer the HTTP stream aggressively, which made latency 30+ seconds. The audio page now uses the WS path. The HTTP path remains for OBS/VLC consumers.
- TTS is gated by a rolling-window budget: 1500 chars/min per table, 100k chars/hour globally (env-tunable).

### Storage modes

- **In-memory only** (the default): no Postgres, no Redis. State lives in the process. Lost on restart. Used in tests and for the live demo.
- **Persistence-enabled** (`PERSISTENCE_ENABLED=1`): SQLAlchemy + Postgres for the ledger and hand history. Doesn't store live state — that's still in-process. Persistence is for replay and bankroll.

Most code paths check `get_settings().persistence_enabled` to branch. The in-memory balance dict (`auth._balances`) is the auth-mode fallback.

---

## Development

### Local dev

```bash
# Backend (in /backend)
pip install -e .
export AUTH_MODE=fake JWT_SECRET=devsecret
uvicorn app.api.main:app --reload --port 8000

# Frontend (in /frontend)
npm install
npm run dev   # → http://localhost:5173, proxies api/auth/ws to :8000
```

Open `http://localhost:5173/?as=alice` and you're logged in as alice. Open another browser as `?as=bob`. Create a table, both join. You're playing.

### Tests

The integration suite has a long-standing pytest harness fragility around TestClient's WebSocket portal teardown when many WS-using tests run together. **Run in four phases:**

```bash
# Phase 1: everything except play_a_hand, spectator, top_off
pytest tests/integration/ \
  --ignore=tests/integration/test_spectator.py \
  --ignore=tests/integration/test_play_a_hand.py \
  --ignore=tests/integration/test_top_off.py \
  --timeout=10 -q

# Phase 2: play_a_hand alone
pytest tests/integration/test_play_a_hand.py --timeout=10 -q

# Phase 3: spectator alone
pytest tests/integration/test_spectator.py --timeout=10 -q

# Phase 4: top_off alone
pytest tests/integration/test_top_off.py --timeout=10 -q

# Unit
pytest tests/unit/ -q
```

Or use a shell script that chains these with `&&`. CI does this. Running `pytest tests/` as one command sometimes hangs at `test_seats_broadcast_after_each_join` — root cause is in starlette/anyio's WebSocket portal teardown race, not product code. Don't waste time debugging it.

Total test count is around 154 across phases. Lint: `ruff check app/ tests/`.

### Build / deploy

```bash
# Frontend production build is bundled into the Docker image via Dockerfile.
# To deploy:
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin 928170058111.dkr.ecr.us-east-1.amazonaws.com
docker build --platform linux/amd64 -f infra/docker/Dockerfile -t spaces-poker:latest .
docker tag spaces-poker:latest 928170058111.dkr.ecr.us-east-1.amazonaws.com/spaces-poker:latest
docker push 928170058111.dkr.ecr.us-east-1.amazonaws.com/spaces-poker:latest
```

Then in the ECS console: `spaces-poker` service → Update → Force new deployment.

ECR auth tokens expire after 12 hours. If `docker push` returns 403, re-run the `aws ecr get-login-password` line first. There's a `deploy.ps1` example in the README notes.

---

## Conventions

### Backend

- **Pure engine layer**. `app/engine/` never imports from `app/services/`. Anything async or I/O-bound lives in `services/`.
- **Wire format goes through `wire.py`.** Don't emit raw dicts from `table_manager` directly to WS clients. `wire.event_to_json(event)` is the centralized converter.
- **Events are dataclasses in `events.py`.** Add new wire fields by extending the event, then extending the matching branch in `wire.py`, then extending the `_public_view` / `_private_view` helpers in `table_manager.py` if it's a state field. Forgetting any one of these silently drops the field on the wire.
- **`_public_view` and `_private_view` in `table_manager.py` are the source of truth** for what fields ship to clients. When you add a state field, this is where to plumb it. Several historical bugs came from adding a field at the engine layer and forgetting to surface it (e.g. `hand_number` was on `rt.hand_number` but missing from `_public_view`, so the narrator read `0` from every event).
- **Type hints are real, not decoration**. mypy isn't run in CI but ruff catches many type-related issues. New code follows the style of nearby code: `from __future__ import annotations`, `list[dict]` not `List[Dict]`, slots dataclasses where possible.
- **Lint passes cleanly on main.** Don't merge changes with `ruff check` failures. `# noqa` is acceptable for the rare false positive but should be commented.

### Frontend

- **Inline styles for component-local layout.** A small `index.css` holds global rules (button reset, mobile breakpoints, base typography). Don't introduce a CSS-in-JS library — what we have is enough.
- **Mobile breakpoint at 640px**, very narrow at 380px. Class names like `.table-frame`, `.table-players`, `.action-bar`, `.event-log`, `.top-off-bar`, `.lobby-create-form`, `.page-header` are the hooks. New responsive behavior goes in `index.css`.
- **All hooks before any conditional return.** React error #310 (rendered fewer/more hooks than previous render) is the #1 frontend bug we've hit. If you add a `useEffect` to a component with an early-return guard, make sure it's above the guard. ActionBar.tsx has a comment flagging this — read it.
- **`useTableSocket` is the WS lifecycle.** Components don't call `new WebSocket()`. They use the hook and trust it to handle reconnect, queueing during disconnect, etc. The hook had two subtle bugs we fixed (don't open until handle is non-empty; only null `wsRef.current` on close if it still points at the closing socket). Don't re-introduce those.
- **Framer Motion for animations.** Cards animate in via `<motion.div initial={...} animate={...}>`. Chip flights overlay the whole screen via `ChipFlight.tsx`. Avoid sprinkling motion across many places — TableView is the animation hub.
- **`PublicState.to_act_deadline_unix_ms`** is the absolute deadline for the to-act player. Clients render countdowns by computing remaining time from `Date.now()`. This is intentional — relative durations would skew with network jitter.

### Tests

- **The `client` fixture in `tests/integration/`** uses TestClient + a default fake auth setup. It auto-cleans the table manager singleton between tests AND clears the in-memory balance dict. The autouse fixture in `conftest.py` handles that.
- **Don't share state between tests via module globals.** If you find yourself needing to, add a cleanup to `conftest.py`'s autouse fixture instead.
- **`_drain_until(ws, [types])`** is a TestClient WS helper for blocking until a specific message type appears. Default cap is 30 messages; bump it for tests that have a lot of churn before the message of interest.

---

## Frequently-hit gotchas

These are the bugs we've actually shipped and fixed. They reappear if you don't know.

1. **Hooks after conditional return → React #310**. Production minification masks the original error name. If users see "WebSocket disconnects on every action," the actual cause is almost always a hook violation in ActionBar or another player-facing component. The disconnect happens because the page crashes, which unmounts the WS hook.

2. **Stale `wsRef.current` on close**. Two WS connections in flight (e.g. during reconnect): the older one's `onclose` fires after the newer one has been installed in `wsRef.current`, and if it unconditionally clears the ref, the newer working socket becomes unreachable. The fix is `if (wsRef.current === ws) wsRef.current = null`. Don't simplify this.

3. **Audio latency**. Browsers buffer HTTP audio streams aggressively. If you find yourself debugging "narration plays late," check that the listener page is using `/ws/audio/{code}` (WebSocket per-clip) and not `/api/audio/{code}/stream` (continuous HTTP). The HTTP stream is for OBS/VLC, not browsers.

4. **`_public_view` field omission**. Adding a runtime field on `TableRuntime` but forgetting to thread it through `_public_view` means clients get the default value (often `0` or `null`). The narrator's "Hand number 0" bug was this — `hand_number` lived on `rt` but wasn't in the wire payload.

5. **WebSocket portal teardown race in tests**. See the four-phase test invocation above. Not a product bug, a starlette/anyio test-harness quirk.

6. **ECR auth expires every 12 hours**. If `docker push` returns 403, re-auth before assuming IAM issues.

7. **Controlled-input value snap-back**. Number inputs that bind `value={clampedAmount}` clobber what the user is typing. The fix is to track a separate `inputText` while focused, only clamping on blur / submit. ActionBar.tsx has the full pattern.

8. **Edge in-app browser cookie isolation**. Links opened from the X app's WebView don't share cookies with Safari/Chrome. Users may see a fresh login screen there when they expect to be already signed in. Not a bug we've fixed — known limitation. OG meta tags in `index.html` at least make the link preview look right.

9. **Edge audio routing for OBS / X Spaces**. The host-side broadcast setup is documented separately in `docs/broadcast-setup-windows.md` (if present) or in the most recent zip output. VoiceMeeter Banana + OBS + Edge for the Space + Chrome for narration. Two virtual outputs (B1 → Space, B2 → OBS) carrying the same mix.

---

## Recent feature areas

These were built over many sessions and have specific design decisions worth knowing.

### Audio/narration
- `narrator.py` is stateful per-table. It recognizes 3-bets, 4-bets, BB option, phase transitions, auto-folds. 25 tests in `test_narrator.py`. Voice variations (e.g. `["shows", "turns over", "tables"]`) are RNG-selected with a seed for determinism in tests.
- Narrator output examples: `"Alice three-bets to ninety."`, `"Bob tables queen of clubs, queen of diamonds."`, `"Alice takes it with two pair, aces and kings, jack kicker, two twenty."`
- Action narration uses the OLD phase context. When `bob_calls` ends pre-flop and the engine advances to flop, the narration reads as "Bob calls. Flop. king of clubs, king of spades, four of hearts." — action under pre-flop, then phase announce.
- Showdown reveals are ordered clockwise from button so they read naturally.
- Hand descriptions are specific: "Pair of aces, king kicker", "Full house, kings full of fours", "Ace-high flush" — computed by `_describe_hand` in `evaluator.py` from the actual `best_five` cards. Wheel straights name 5 as the high card, not the ace.

### Chips / pot animations
- Two-phase resolution at hand-complete: any unresolved river chips fly to the pot first, then with a 500 ms delay chips fly from the pot to each winner.
- Chip flights are transient elements overlaid via `ChipFlightOverlay` (fixed-positioned, screen-coordinate based on `getBoundingClientRect` of `tileRefs`).
- Phase-flash overlay pulses behind the board each time a new street comes out.
- The to-act player has a breathing gold glow (1.6s cycle).
- Stack numbers flash gold when they change.

### Action timers
- Server publishes `to_act_deadline_unix_ms` on every public state event that includes a new actor.
- Clients render a countdown badge next to the active player's name. Red under 5 seconds.
- Time-bank: a per-seat reserve that kicks in when the base 25s elapses, up to 60s additional. Stored on `SeatRuntimeState.time_bank_seconds`.

### Re-buy / top-off
- `/api/tables/top_off` POST endpoint. Validates seated + not-mid-hand + total ≤ 200×BB.
- Frontend `TopOffBar` shows current stack vs. cap with quick "+ buy-in" and "Fill to max" buttons. Hidden when at cap. Disabled with explanatory text when mid-hand-active.
- `RebuyCTA` separately handles the "you just busted, want your seat back?" flow via `lastSeatRef` tracking the player's most recent seat.

---

## Tools / external services

- **ElevenLabs**: TTS. Flash v2.5 model, voice ID `21m00Tcm4TlvDq8ikWAM` (Rachel). Required env `ELEVENLABS_API_KEY`. Tunable: `ELEVENLABS_VOICE_ID`, `TTS_MAX_CHARS_PER_MIN_PER_TABLE`, `TTS_MAX_CHARS_PER_HOUR`.
- **X OAuth 2.0 PKCE**: real auth path. Required env `X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_REDIRECT_URI`. Gated by `AUTH_MODE=oauth` or `both`.
- **AWS**: ECS Express Mode in us-east-1. ECR repo `spaces-poker`. ALB `ecs-express-gateway-alb-309f7923`. Route 53 in `interesting-times-gang.com`. Listener rule matches both `sp-e90fd69e82b64c568df7c9e10e8aaac0.ecs.us-east-1.on.aws` and `poker.interesting-times-gang.com`.

## Moderation: IP ban list

Set `BANNED_IPS` to a comma-separated list of IPs and/or CIDR ranges (IPv4 and IPv6 both supported):

```
BANNED_IPS=1.2.3.4, 10.0.0.0/24, 2001:db8::/32
```

Matching clients receive `403 forbidden` on HTTP routes and a `1008 policy violation` close on WebSocket — before auth, table lookup, or any other handler logic runs. The list is loaded once at process start; to ban at runtime, update the env var in the ECS task definition and force a new deployment (~5 min cycle).

The check uses `request.client.host`, which only reflects the real client when uvicorn is launched with `--proxy-headers --forwarded-allow-ips "*"` (set in [infra/docker/Dockerfile](infra/docker/Dockerfile)). Without those flags every client looks like the ALB.

To find a griefer's IP: WebSocket connect events are logged with `ws/tables: handle=<X> code=<Y> ip=<Z>` (see [backend/app/api/ws.py](backend/app/api/ws.py)). Grep CloudWatch logs for the handle, copy the IP into `BANNED_IPS`, redeploy.

---

## Open questions / what's next

These are the candidate features that have come up but aren't built yet, in rough priority order:

1. **Narrator memory across hands**. Currently the narrator resets every hand. Add `_SessionContext` so it can reference orbit-level patterns ("Bob's third 3-bet this orbit", "Alice's been quiet").
2. **ElevenLabs v3 with audio tags**. `[whispers]`, `[laughs]`, `[excited]` on big moments. v3 is ~2-3x cost per character but tag-gated so average impact is small.
3. **Path B production deploy**. Real X OAuth on live, RDS Postgres, ElastiCache Redis, persistence enabled. Currently runs in-memory-only.
4. **Redis pub/sub for multi-task spectators**. Today one Fargate task owns each table. Scaling spectators beyond one task needs Redis fan-out.
5. **Replay-page parity**. Replay doesn't currently re-derive `pot_distributions`, so older recorded hands don't show winner highlights. Fixable by running `_compute_pot_distributions` on the reconstructed final state.
6. **Side-pot reveal narration**. Currently says "Multiple pots split among the winners" — should narrate each pot with its winner's hand description.
7. **Real avatars / profile pictures**. Currently just handles. Avatars would need an upload flow + asset hosting.

---

## Pointers for new contributors

If you're picking this up cold:

- Read `app/services/table_manager.py` first. It's the heart of the system. Long, but each method has a single responsibility.
- Then `app/engine/table.py` — the pure rules engine.
- Then `app/services/narrator.py` if you care about commentary, or `frontend/src/components/TableView.tsx` if you care about the UI.
- Skim `app/services/wire.py` to see all the wire-format mappings in one place.
- The test files are good documentation of intended behavior. `test_play_a_hand.py` walks two players through a full hand.

Don't worry about reading `recovery.py` or the Alembic migrations unless you're working on persistence.

---

## Style and review notes

- Brevity over completeness in code comments. A comment should explain *why*, not what — the code says what.
- Function and method names use snake_case in Python, camelCase in TS.
- Components in TSX are PascalCase. Files match: `TableView.tsx` exports `TableView`.
- Long files are fine if the structure is clear. `table_manager.py` is 1000+ lines and that's the right unit.
- We don't over-engineer for changes that aren't on the roadmap. The codebase has stayed legible because each feature stops when it's done, not when it's "complete."
- When fixing a bug, write a regression test if the bug is in product code. If the bug is in test harness fragility, document it and move on.
