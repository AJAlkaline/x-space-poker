# Architecture

A short living document — see `README.md` for build instructions and `legal.md`
for the legal posture.

## Tiers

**Client tier** — the host's machine runs a browser pointed at the web app and
a host audio relay (browser tab or Electron) that pipes ElevenLabs audio into
the X Spaces input via a virtual audio cable. Players run only the web app.

**Application tier** — one container image. Deployable to AWS ECS Fargate or
Azure Container Apps. Inside the container:

- FastAPI gateway: HTTP routes (auth, tables) and WebSockets (per-table state)
- Table loop tasks: one asyncio task per active table, owns the GameState
- Poker engine: pure Python, no I/O, fully unit-tested
- Ledger service: the only writer to `accounts.balance_minor`
- Narration worker: ElevenLabs TTS queue, pre-synth cache for common phrases

**Data tier** — Postgres for users, ledger, hands, hand actions, table events.
Redis for ephemeral pub/sub and OAuth flow state.

## Concurrency model

One asyncio task per table is the single writer for that table's state. All
player actions arrive via WebSocket, get pushed onto the table's input queue,
and are processed serially. This eliminates entire classes of races (two
players acting simultaneously, action arriving after the timer fired).

State persists to Postgres after every street (flushed action log + final
state on hand-complete). A process restart recovers in-flight hands by
replaying actions from the log against the revealed deck seed.

## Public/private channel split

For each connected client, two logical channels:

- **Public**: full table state minus everyone's hole cards. Broadcast.
- **Private**: this player's hole cards plus per-action prompts (legal actions
  list, action deadline). Sent only to this player.

Audit constraint: a malicious client subscribed to all public messages
must not be able to reconstruct anyone else's hole cards. Tested.

## Currency abstraction

The `accounts.currency_type` column is a string. Today it's always `"PLAY"`.
The poker engine never touches the ledger directly — buy-in moves balance →
seat stack, cash-out moves seat stack → balance. The engine only sees integer
chip stacks. This means a future regulated currency type could be added
behind the same engine without rewriting the engine.

Real-money or crypto types are not implemented and require licensing that
isn't part of this project. See `docs/legal.md`.

## Hand provability

Each hand has a SHA-256 commitment of its deck seed written before the hand
starts (`hands.deck_seed_commit`). The seed itself is revealed when the hand
ends (`hands.deck_seed_reveal`). Combined with the `hand_actions` log, anyone
can replay the hand offline and confirm the outcome matches.

This costs nothing to add now and gives you a credible audit trail if a
player ever disputes an outcome.

## Why these choices

- **AWS first, Azure as escape hatch**: not because Azure is worse but because
  AWS's RDS + ALB + Fargate pricing is cleanest at this scale and team size.
- **Postgres, not DynamoDB / Cosmos**: the data model is relational (FK-heavy)
  and the query patterns are mostly point reads + audit scans, both of which
  Postgres does well; the operational simplicity isn't worth giving up.
- **Asyncio, not multiprocessing**: hand processing is I/O-bound (DB writes,
  WebSocket sends). Single-process is sufficient until you have 50+
  concurrent tables; horizontal scale via more containers when needed.
- **One container image, two deploy targets**: forces clean cloud-agnostic
  code. The discipline costs you a few AWS-only conveniences (IAM-auth'd
  RDS, SQS as a queue) and saves you the lock-in.
