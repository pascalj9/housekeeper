# Event bus (NATS)

Housekeeper uses **NATS** as the internal event bus. Every component talks to
every other component through the bus, never directly. This file documents the
subjects the system commits to and the rules around them.

## Why NATS

- Single-binary, fast, well-suited to many-publisher / many-consumer.
- JetStream gives us durable, replay-able streams (useful for `video.events`
  so the agent can catch up after a restart without losing context).
- Loopback-only by default — the bus is internal; nothing about it ever needs
  to leave the host.
- First-class Python client (`nats-py`, async).

## Connection

- Default URL: `nats://127.0.0.1:4222`
- Override in `configs/services.yaml.local` (gitignored) if you ever need to
  bind elsewhere.
- All Housekeeper services open the connection through
  `housekeeper.bus.Bus()`, which wraps reconnect policy and JetStream
  bootstrap. Never `import nats` directly.

## Subjects

Subjects are dot-delimited and lowercase. The first segment is the **domain**.
Subjects in **bold** are durable (backed by a JetStream stream).

| Subject | Producer | Consumer | Durable? | First needed |
|---|---|---|---|---|
| **`video.events`** | `apps/perception` | `apps/agent`, `apps/rules` | yes (`HOUSEKEEPER_VIDEO`) | Phase 1 |
| **`video.events.<camera>`** | `apps/perception` | `apps/agent` | yes (same stream) | Phase 1 |
| `agent.commands` | `apps/chat-web`, CLI | `apps/agent` | no (req/reply) | Phase 4 |
| `chat.inbound` | `apps/chat-web`, iMessage poller | `apps/agent` | no | Phase 5 |
| `chat.outbound` | `apps/agent` | `apps/chat-web`, iMessage bridge | no | Phase 5 |
| `notify.outbound` | `apps/agent`, `apps/rules` | `apps/notifier` | no | Phase 2 |

### Why durability only on `video.events`

Restarts of the agent should not lose recent context — durability lets a
freshly-started agent replay the last N minutes of perception events and
reconstruct its working memory. Chat/notify don't need this; if the agent is
down, those drop on the floor.

### Stream config

| Field | Value |
|---|---|
| Name | `HOUSEKEEPER_VIDEO` |
| Subjects | `video.events`, `video.events.>` |
| Retention | LIMITS |
| Storage | FILE |
| Max age | 24h |
| Max bytes | 500 MB |

Provisioned by `housekeeper bus init` (idempotent). Verify with
`housekeeper bus info`.

## Payload conventions

- All payloads are UTF-8 encoded **JSON** strings.
- Each message must include `ts` (ISO-8601 UTC) and `source` (component name).
- Schema definitions land in `src/housekeeper/schemas/` starting in Phase 1;
  this doc gets updated to point at them.

## CLI cheat-sheet

| Command | Purpose |
|---|---|
| `uv run housekeeper bus verify` | Probe TCP + ping — used by `doctor` |
| `uv run housekeeper bus init` | Create/update the JetStream stream |
| `uv run housekeeper bus info` | Show stream messages/bytes/seqs |
| `uv run housekeeper bus publish <subject> <body>` | One-off publish |
| `uv run housekeeper bus sub <subject> [-n N]` | Subscribe, print, exit after N |

## Bus + JetStream sizing notes (M3, 36 GB)

JetStream uses a tiny amount of RAM (file-backed). Disk:

- Event payload is ~300 bytes typical (caption + tags + frame_ref).
- ~10 motion events / minute under load → ~4 MB / day.
- 500 MB cap = ~125 days of headroom; in practice we age out after 24h.

If you ever turn on heavy multi-camera capture (Phase 10 stretch), revisit the
`max_bytes` ceiling.

## Future work (out of scope for v1)

- Auth: NATS NKey or accounts, plus subject-level ACLs (Phase 8).
- Cross-host replication (e.g. mirror to a backup Mac) — not needed.
- KV / object-store buckets for snapshots (currently flat files in
  `~/.housekeeper/var/snapshots/`).
