# Housekeeper — Design Document

## 1. Overview

**Housekeeper** is a fully local, offline-capable AI agent that runs on an Apple Silicon Mac (M3 MacBook Pro, 36 GB unified memory). It:

1. Ingests a live RTSP/ONVIF stream from an IP camera on the local network.
2. Continuously interprets the video with a local vision-language model (VLM).
3. Reasons about events using a local LLM "brain" with tools and memory.
4. Communicates with the user via a chat/messaging interface (two-way text, plus push notifications).

Latency target: end-to-end event-to-notification within **~5 seconds**. No cloud dependency for inference, transport, or storage.

---

## 2. Goals & Non-Goals

### Goals
- 100% offline inference (models, vector store, message broker all local).
- Real-time-ish reaction (a few seconds is acceptable).
- Two-way conversational interface ("Did the dog go outside today?", "Tell me when the package arrives").
- Configurable rules and standing instructions in natural language.
- Push notifications to phone (iOS/macOS).
- Privacy: video frames never leave the LAN.

### Non-Goals (v1)
- Multi-camera fusion (single camera first).
- Audio understanding from camera (video only first).
- Cloud sync / multi-user.
- Mobile-native app (use existing messaging app + ntfy/Pushover-self-hosted).

---

## 3. Hardware & Runtime Constraints

- **Host**: MacBook Pro M3, 36 GB unified memory, macOS.
- **Inference stack**: prefer MLX (Apple-native) or `llama.cpp` with Metal; fall back to Ollama for ease.
- **Memory budget** (rough):
  - Vision-language model: 8–14 GB (e.g., Qwen2.5-VL 7B, MiniCPM-V 2.6, or Moondream2).
  - Text LLM "brain": 6–10 GB (e.g., Qwen2.5 7B / 14B Instruct, Llama 3.1 8B).
  - Embeddings + ASR (optional): ~1–2 GB.
  - System + buffers: leave 8 GB headroom.
- **Network**: camera over RTSP on LAN; phone reachable on same LAN or via Tailscale.

---

## 4. High-Level Architecture

```
                 ┌────────────────────────────────────────────────┐
                 │                 Housekeeper Host (Mac)         │
                 │                                                │
  IP Camera ───► │  Video Ingest  ─►  Frame Sampler  ─►  VLM      │
   (RTSP)        │   (ffmpeg)         (motion/keyframe)  Worker   │
                 │                                        │       │
                 │                                        ▼       │
                 │                                  Event Bus     │
                 │                                  (NATS/Redis)  │
                 │                                        │       │
                 │   ┌────────────────────────────────────┤       │
                 │   ▼                                    ▼       │
                 │  Agent Core  ◄────► Memory Store   Rules Engine│
                 │ (LLM + tools)       (SQLite +                  │
                 │                      vector DB)                │
                 │   │                                            │
                 │   ├─► Notifier (ntfy / APNs-via-ntfy)          │
                 │   ├─► Chat Gateway (Matrix / Signal-cli /      │
                 │   │     Telegram-LAN-bot / Web UI)             │
                 │   └─► Snapshot Store (clips & frames)          │
                 └────────────────────────────────────────────────┘
                              ▲                       │
                              │                       ▼
                         User chats               Push to phone
```

### Core components
1. **Video Ingest** — pulls RTSP, decodes with ffmpeg/PyAV.
2. **Frame Sampler** — adaptive: motion-triggered + N fps cap.
3. **VLM Worker** — runs the vision model on sampled frames, produces structured scene descriptions.
4. **Event Bus** — lightweight pub/sub (NATS or Redis Streams) decoupling perception from reasoning.
5. **Agent Core** — LLM orchestrator with tools, standing instructions, conversation memory.
6. **Memory Store** — SQLite for events/timeline, a local vector DB (LanceDB or sqlite-vec) for semantic recall.
7. **Rules Engine** — declarative + LLM-evaluated triggers ("when X, do Y").
8. **Notifier** — self-hosted ntfy server → push to phone.
9. **Chat Gateway** — user-facing messaging interface.
10. **Snapshot Store** — disk-backed ring buffer of frames + short clips for context and review.

---

## 5. Component Details

### 5.1 Video Ingest
- **Input**: RTSP URL (ONVIF discovery optional in v2).
- **Tool**: `ffmpeg` subprocess or `PyAV`. Decode to raw frames at native fps, but throw away most.
- **Output**: shared memory ring buffer of recent frames (e.g., last 30 s at 5 fps) + last-keyframe pointer.
- **Resilience**: auto-reconnect with exponential backoff; expose health metric.

### 5.2 Frame Sampler
- Two triggers:
  1. **Motion gate** — cheap background subtraction (OpenCV MOG2) or frame-difference threshold.
  2. **Heartbeat** — one frame every N seconds even without motion (for "nothing changed" confirmations).
- Adaptive: when motion is sustained, sample at ~1–2 fps; when idle, drop to 1 / 30 s.
- Deduplicate near-identical frames via perceptual hash (pHash) before sending to VLM.

### 5.3 VLM Worker
- **Model candidates** (4-bit / MLX where possible):
  - Qwen2.5-VL-7B-Instruct (strong general VLM).
  - MiniCPM-V 2.6 (efficient, good OCR).
  - Moondream2 (tiny, fast, low quality — useful as a cheap pre-filter).
- **Two-tier perception** (optimization):
  - **Tier-1 (fast)**: Moondream2 / tiny VLM tags every motion event with short caption + objects.
  - **Tier-2 (smart)**: Qwen2.5-VL invoked only when Tier-1 caption matches a watched concept, or on demand from the Agent Core.
- **Output schema** (JSON, published to event bus):
  ```json
  {
    "ts": "2026-05-16T12:34:56Z",
    "camera": "front_door",
    "caption": "A delivery person places a box on the porch.",
    "objects": ["person", "package", "uniform"],
    "tags": ["delivery", "package_arrival"],
    "frame_ref": "snapshots/2026/05/16/123456.jpg",
    "confidence": 0.82
  }
  ```

### 5.4 Event Bus
- Local **NATS** server (single binary, lightweight) or **Redis Streams**.
- Topics: `video.events`, `agent.commands`, `notify.outbound`, `chat.inbound`, `chat.outbound`.
- Justification: lets the VLM, agent, and chat front-end run as independent processes; easier to restart/replace any one.

### 5.5 Agent Core
- **Runtime**: Python, with the LLM served locally (Ollama or `mlx_lm` server exposing an OpenAI-compatible API).
- **Brain model**: Qwen2.5-14B-Instruct (4-bit) if memory allows, else Qwen2.5-7B or Llama-3.1-8B-Instruct.
- **Loop**:
  1. Subscribe to `video.events` + `chat.inbound`.
  2. On each event, build a prompt = standing instructions + recent timeline summary + relevant retrieved memories + new event.
  3. LLM produces a tool call or a chat reply.
  4. Execute tool, push results back through bus.
- **Tools** (function-calling):
  - `send_message(text)`
  - `send_notification(title, body, priority, image?)`
  - `query_timeline(time_range, filter)`
  - `request_detailed_look(camera, reason)` → forces Tier-2 VLM on latest frames.
  - `set_watch(rule_nl, expires_at?)` → register a new standing rule.
  - `list_watches() / cancel_watch(id)`
  - `save_memory(text, tags)`
  - `recall(query, k)`
- **Context management**: rolling summary of last hour + vector-retrieved older memories; keep effective context ≤ 8k tokens.

### 5.6 Memory Store
- **SQLite** tables:
  - `events(id, ts, camera, caption, objects[json], tags[json], frame_ref, confidence)`
  - `messages(id, ts, direction, channel, text)`
  - `watches(id, created_at, rule, status, expires_at)`
  - `notes(id, ts, text, tags[json])`
- **Vector index**: `sqlite-vec` or LanceDB on `events.caption` + `notes.text`. Embeddings via `bge-small-en` or `nomic-embed-text` locally.

### 5.7 Rules Engine
- Two flavors:
  - **Declarative** (fast): YAML rules like `when tags include "package_arrival" → notify(high, "Package on porch")`.
  - **Natural-language watches** (flexible): stored as text, evaluated each event by a small fast LLM pass ("Does this event satisfy: '<rule>'? yes/no + reason"). Use a cheap model (e.g., Qwen2.5-3B) for this gate to keep cost down.
- Debounce + cooldown per rule to avoid spam.

### 5.8 Notifier
- Self-host **ntfy** on the Mac (single binary). Phone has the ntfy app; subscribes to a private topic.
- Supports title, body, priority, attached image (snapshot thumbnail).
- Optional: local-only via Tailscale when away from home.

### 5.9 Chat Gateway
- Pick **one** in v1; abstract behind an interface for later swaps.
- Options (offline-friendly):
  1. **Local web UI** (FastAPI + simple React/HTMX) reachable via Tailscale — simplest, no third party.
  2. **Matrix** via a local Synapse/Conduit server + matrix-nio bot — true messaging app feel.
  3. **Telegram bot** — easiest UX, but requires internet to Telegram servers (violates "offline"); skip for v1.
  4. **signal-cli** — possible but heavier; v2.
- **Recommended v1**: local web UI + ntfy push. **v2**: add Matrix.

### 5.10 Snapshot Store
- File layout: `snapshots/YYYY/MM/DD/HHMMSS_<event_id>.jpg` plus optional 5-second mp4 clips for high-priority events.
- Retention policy: e.g., keep clips 7 days, thumbnails 30 days, event metadata indefinitely.

---

## 6. Data & Control Flow (typical event)

1. ffmpeg decodes RTSP → ring buffer.
2. Motion detected → sampler picks frame, computes pHash, dedupes.
3. Tier-1 VLM tags frame → publishes `video.events`.
4. Rules Engine matches tags / NL watches → may directly emit `notify.outbound`.
5. Agent Core also consumes the event:
   - Updates timeline summary + vector store.
   - If ambiguous or interesting, calls `request_detailed_look` → Tier-2 VLM re-examines.
   - Decides whether to message the user, ask a clarifying question, or stay silent.
6. Notifier delivers push; Chat Gateway shows message; user can reply.
7. User reply → `chat.inbound` → Agent Core → tool calls (e.g., set new watch) → reply.

---

## 7. Tech Stack Summary

| Layer | Choice (v1) |
|---|---|
| Language | Python 3.11+ (workers), TypeScript (web UI) |
| Video | ffmpeg, PyAV, OpenCV |
| Inference | MLX (`mlx-vlm`, `mlx-lm`) preferred; Ollama as fallback |
| VLM | Qwen2.5-VL-7B (Tier-2), Moondream2 (Tier-1) |
| LLM | Qwen2.5-14B-Instruct-4bit (or 7B) |
| Embeddings | `nomic-embed-text` via Ollama |
| Bus | NATS (single binary) |
| Storage | SQLite + sqlite-vec; flat files for media |
| Notifications | self-hosted ntfy |
| Chat UI | FastAPI + HTMX |
| Process mgmt | `launchd` user agents (or `tmux`/`pm2` in dev) |
| Remote access | Tailscale |

---

## 8. Repository Layout (proposed)

```
housekeeper/
  apps/
    ingest/          # rtsp → frames
    perception/      # VLM tier-1 + tier-2 workers
    agent/           # LLM orchestrator + tools
    rules/           # declarative + NL rule evaluator
    notifier/        # ntfy client
    chat-web/        # FastAPI + HTMX chat UI
  packages/
    bus/             # NATS client wrappers + schemas
    memory/          # SQLite + vector store helpers
    models/          # model loading (MLX/Ollama abstractions)
    schemas/         # pydantic event schemas
  configs/
    cameras.yaml
    rules.yaml
    models.yaml
  scripts/
    bootstrap_models.sh
    run_dev.sh
  launchd/           # plists for autostart
  docs/
    design.md
  tests/
```

---

## 9. Configuration (sketch)

`configs/cameras.yaml`
```yaml
cameras:
  - id: front_door
    rtsp: rtsp://user:pass@192.168.1.50:554/stream1
    fps_cap: 5
    motion: { method: mog2, min_area: 1500 }
```

`configs/models.yaml`
```yaml
vlm_fast: { provider: mlx, name: moondream2-int8 }
vlm_smart: { provider: mlx, name: qwen2.5-vl-7b-4bit }
llm: { provider: mlx, name: qwen2.5-14b-instruct-4bit, ctx: 8192 }
embed: { provider: ollama, name: nomic-embed-text }
```

`configs/rules.yaml`
```yaml
- id: package
  when_tags_any: [package_arrival, package_on_porch]
  cooldown_s: 600
  notify: { priority: high, title: "Package arrived" }
```

---

## 10. Implementation Plan — Phased

Each phase ends with a **runnable, demoable** slice. No time estimates. Check items off as you complete them.

### Phase 0 — Bootstrap
- [ ] Initialize repo, Python project (uv or poetry), pre-commit, ruff, pytest.
- [ ] Install Ollama + MLX, pull baseline models.
- [ ] Write `bootstrap_models.sh`.
- [ ] Set up Tailscale as a background service.
- [ ] Set up self-hosted ntfy server via `launchd`.
- [ ] Set up NATS server via `launchd`.
- [ ] Build `housekeeper doctor` CLI (verifies camera reachability, model load, bus connectivity, ntfy push).
- [ ] **Deliverable**: `housekeeper doctor` passes end-to-end.

### Phase 1 — Video Pipeline MVP
- [ ] `apps/ingest`: RTSP → frame ring buffer (ffmpeg/PyAV).
- [ ] Auto-reconnect with exponential backoff + health metric.
- [ ] `apps/perception`: motion gate (MOG2) on ring buffer.
- [ ] pHash dedup for near-identical frames.
- [ ] Tier-1 VLM (Moondream2) generates captions + tags.
- [ ] Publish events to NATS (`video.events`).
- [ ] Persist events to SQLite.
- [ ] CLI viewer that tails events (`housekeeper tail`).
- [ ] **Deliverable**: live captions of camera scene streaming in terminal.

### Phase 2 — Notifier + Static Rules
- [ ] `apps/rules`: YAML-driven matcher (tags / keyword regex).
- [ ] Cooldowns and per-rule throttling.
- [ ] `apps/notifier`: post to local ntfy with snapshot thumbnail.
- [ ] Priority levels wired through ntfy.
- [ ] Snapshot store directory layout + write path.
- [ ] **Deliverable**: phone gets push when configured concepts appear (e.g., "person at door").

### Phase 3 — Memory & Timeline
- [ ] SQLite schema + migrations (hand-rolled or alembic-lite).
- [ ] Tables: `events`, `messages`, `watches`, `notes`.
- [ ] Embeddings via `nomic-embed-text` (Ollama).
- [ ] sqlite-vec index over `events.caption` + `notes.text`.
- [ ] Rolling hourly/daily summary job (small LLM call).
- [ ] `housekeeper query "did anyone come by today?"` CLI.
- [ ] **Deliverable**: queryable timeline + basic semantic search.

### Phase 4 — Agent Core (read-only)
- [ ] LLM server up (MLX or Ollama) with OpenAI-compatible function-calling.
- [ ] Agent loop: subscribe to `video.events` + `chat.inbound`.
- [ ] Tool: `query_timeline(time_range, filter)`.
- [ ] Tool: `recall(query, k)`.
- [ ] Tool: `send_message(text)`.
- [ ] Tool: `send_notification(title, body, priority, image?)`.
- [ ] Standing system prompt + persona.
- [ ] Rolling context summary keeper (≤ 8k tokens).
- [ ] **Deliverable**: agent autonomously messages user about interesting events.

### Phase 5 — Chat Gateway (two-way)
- [ ] Define `chat.inbound` / `chat.outbound` schemas and topics.
- [ ] `apps/chat-web`: FastAPI + HTMX chat UI.
- [ ] Per-user conversation thread + history view.
- [ ] Inline snapshot rendering in messages.
- [ ] Expose via Tailscale only.
- [ ] **iMessage channel** (outbound via `osascript`; inbound poller over `~/Library/Messages/chat.db`).
- [ ] **Deliverable**: full two-way chat from phone browser **and** iMessage.

### Phase 6 — Smart Perception (Tier-2 + on-demand looks)
- [ ] Add Qwen2.5-VL-7B as Tier-2.
- [ ] Lazy-load / unload Tier-2 based on idle timeout.
- [ ] Tool: `request_detailed_look(camera, reason)` → re-runs Tier-2 on recent buffer.
- [ ] Extend event schema (objects, attributes, relations).
- [ ] High-priority notifications require Tier-2 confirmation.
- [ ] **Deliverable**: agent can answer "what is the person wearing right now?" by triggering a closer look.

### Phase 7 — Natural-Language Watches
- [ ] Tool: `set_watch(rule_nl, expires_at?)`.
- [ ] Tool: `list_watches()` / `cancel_watch(id)`.
- [ ] Per-event NL-rule evaluator using small/fast model (e.g., Qwen2.5-3B).
- [ ] Watch debounce + cooldown.
- [ ] **Deliverable**: user can set/cancel arbitrary standing instructions in chat.

### Phase 8 — Robustness & Ops
- [ ] Health endpoints for every service.
- [ ] `housekeeper status` dashboard.
- [ ] Auto-restart via `launchd KeepAlive`.
- [ ] Backpressure + queue-length metrics.
- [ ] Drop-policy when VLM falls behind.
- [ ] Log rotation.
- [ ] Snapshot retention policy job.
- [ ] Outbound-network-policy integration test (no third-party hosts).
- [ ] **Deliverable**: runs unattended for a week.

### Phase 9 — Quality Pass
- [ ] Build golden set of labeled snippets (porch, kitchen, etc.).
- [ ] Perception regression harness against golden set.
- [ ] Conversation eval set (over-notify / under-notify).
- [ ] Tune motion thresholds and cooldowns per camera.
- [ ] Prompt tuning pass on system prompt + tool descriptions.
- [ ] **Deliverable**: precision/recall numbers, baseline metrics.

### Phase 10 — Stretch
- [ ] Evaluate OpenClaw as Chat Gateway / Agent Core replacement (see §14).
- [ ] Multi-camera + cross-camera identity ("same person at side gate then front door").
- [ ] Audio events (doorbell, dog bark) via local ASR/keyword spotter.
- [ ] Voice replies via TTS (e.g., Piper) when phone is on Wi-Fi.
- [ ] Matrix bridge for chat (self-hosted Conduit/Synapse).
- [ ] Encrypted backup of memory store.

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| VLM throughput too low | Two-tier perception; aggressive dedup; cap fps; downscale to 512px. |
| Memory pressure (14B + VLM together) | Lazy-load Tier-2; unload after idle; or downgrade brain to 7B. |
| Over-notification (alert fatigue) | Cooldowns, priority levels, confidence thresholds, agent self-suppression. |
| Hallucinated events | Require Tier-2 confirmation before high-priority notifications. |
| Camera offline | Health checks + notify user once with cooldown. |
| Privacy / accidental upload | Hard rule: no outbound HTTP except to ntfy/Tailscale; CI lint to block. |
| Prompt injection via OCR / scene text | Sanitize VLM outputs before feeding into tool-calling prompts; never execute strings from frames. |

---

## 12. Security & Privacy

- All services bind to `127.0.0.1` or Tailscale interface only.
- Camera credentials in `~/.housekeeper/secrets` (chmod 600), loaded via env.
- ntfy topic uses long random token; access-controlled.
- No third-party telemetry. Outbound network policy verified by an integration test.

---

## 13. Open Questions

- [ ] Exact VLM choice once benchmarked on the M3 (latency vs. quality).
- [ ] Single brain LLM vs. specialized small models per task.
- [ ] Whether to add a lightweight object detector (YOLO) as a Tier-0 pre-filter for cost.
- [ ] Chat UX: web-only v1, or invest in Matrix earlier.
- [ ] Adopt OpenClaw as the Chat Gateway / Agent Core (see §14) — defer decision until after Phase 5.

---

## 14. Integration Option — OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) is an MIT-licensed local-first "personal AI assistant Gateway" (TypeScript/Node) that provides a multi-channel messaging layer, agent runtime with tools/skills, sessions, sandboxing, cron/webhooks, and companion apps for macOS/iOS/Android. Channels include iMessage, WhatsApp, Telegram, Signal, Slack, Discord, Matrix, WebChat, and more.

### What it could replace in Housekeeper
- Most of **Chat Gateway (§5.9, Phase 5)** — channels, sessions, history, push to phone.
- Parts of **Agent Core (§5.5, Phase 4)** — agent shell, tool/skill invocation, system prompt management, conversation memory.
- Parts of **Phase 8 ops** — daemon via launchd, `doctor` checks, remote access patterns.

### What Housekeeper still owns
- RTSP ingest, motion gating, frame sampling, snapshot store.
- Tier-1 / Tier-2 VLM pipeline.
- Event bus (`video.events`) and timeline / memory store.
- Rules engine and natural-language watches.

Housekeeper's perception stack would expose itself to OpenClaw as either:
1. An **OpenClaw skill / tool set** the agent can call (`query_timeline`, `request_detailed_look`, `set_watch`, …), or
2. A **webhook** that pushes events into an OpenClaw session ("a person just appeared at the front door").

### Pros
- Saves building N channel integrations (iMessage, Matrix, Signal, WebChat) and the agent shell.
- Mature pairing / sandboxing / DM-policy model already exists.
- macOS menu bar app + iOS/Android nodes give push, voice wake, and ad-hoc phone camera/screen.

### Cons / risks to verify
- [ ] Confirm first-class **local-model support** (Ollama / MLX / llama.cpp via OpenAI-compatible endpoint). OpenAI is a sponsor; cloud is the default path in the README.
- [ ] Confirm channels and tools can carry **image attachments** (snapshot frames into agent context).
- [ ] Adds a **Node 24 runtime** alongside the Python perception stack — extra surface area.
- [ ] Fast-moving project (157+ releases). Pin versions; expect breaking changes.
- [ ] Single-user assumption matches us, but multi-tenant features are unused weight.

### Decision rule
**Do not depend on OpenClaw for v1.** Ship Phases 1–5 with the local web UI + ntfy + iMessage. In Phase 10 (or earlier if Phase 5 friction is high), evaluate OpenClaw as a drop-in replacement behind the `chat.inbound` / `chat.outbound` bus abstraction — perception code is untouched either way.

### Spike checklist (when we evaluate)
- [ ] Stand up OpenClaw Gateway locally with an Ollama/MLX model.
- [ ] Wire iMessage channel via OpenClaw; compare to our direct AppleScript bridge.
- [ ] Build a minimal OpenClaw skill that calls `query_timeline` and renders a snapshot.
- [ ] Push a synthetic `video.events` payload as a webhook → agent reply on phone.
- [ ] Measure cold/warm latency vs. our native chat path.
- [ ] Decide: adopt, partial adopt (channels only), or stay native.
