# Housekeeper Runbook

Operational guide for setting up, running, and maintaining Housekeeper. The
**target host** is a MacBook Pro (Apple Silicon). **Development** also works
on Linux / WSL2, with platform-specific bits clearly called out.

This document grows phase by phase alongside the code. If a section says
"Phase X — not yet built", it's expected.

---

## 1. Prerequisites

### All hosts
- **Git** ≥ 2.40.
- **Python 3.11+** (3.12 recommended).
- **`uv`** — modern Python package manager. Install:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  Make sure `~/.local/bin` is on your `$PATH`.

### macOS (production host)
- macOS 14+ on Apple Silicon (M-series).
- **Homebrew** (`brew`) for system packages.
- Later phases will add: `ffmpeg`, `nats-server`, `ntfy`, Ollama or MLX,
  Tailscale. Install instructions appear in the phase that introduces them.

### Linux / WSL2 (dev host)
- WSL2 with Ubuntu 22.04+ is what we develop on.
- MLX-based inference is **not** available on WSL — Phase 1+ will run with
  Ollama or mock backends here. The CLI, business logic, and tests all work.

---

## 2. Repository setup

```bash
git clone git@github.com:pascalj9/housekeeper.git
cd housekeeper

# Create the project venv and install dev dependencies
uv sync --extra dev

# Install git hooks
uv run pre-commit install
```

The first `uv sync` will:
- Create `.venv/` in the repo root.
- Install Housekeeper in editable mode plus the `dev` extras (ruff, pytest,
  pre-commit, pytest-cov).

### One-time git identity for this repo
The project ships a `.gitconfig` with the desired author identity and aliases,
but git does **not** read repo-root `.gitconfig` files automatically. Wire it
in once per clone:

```bash
git config --local include.path ../.gitconfig
```

Verify with:
```bash
git config --show-origin user.email   # should resolve from .gitconfig
```

---

## 3. Daily commands

| Task | Command |
|---|---|
| Run the CLI | `uv run housekeeper --help` |
| Print version | `uv run housekeeper version` |
| Health check (placeholder until Phase 0.6) | `uv run housekeeper doctor` |
| Run tests | `uv run pytest` |
| Coverage report | `uv run pytest --cov` |
| Lint + format check | `uv run ruff check . && uv run ruff format --check .` |
| Auto-fix lint + format | `uv run ruff check --fix . && uv run ruff format .` |
| Run all pre-commit hooks | `uv run pre-commit run --all-files` |
| Add a runtime dependency | `uv add <package>` |
| Add a dev dependency | `uv add --optional dev <package>` |

---

## 4. Local models (Phase 0.2)

Housekeeper relies on locally-running models served by **Ollama** (every host)
and **MLX** (Apple Silicon only, Phase 6+). See [`docs/models.md`](docs/models.md)
for the model catalogue, profile breakdown, and memory budget.

### 4.1 Install Ollama

**macOS** (production host):
```bash
brew install ollama
brew services start ollama         # auto-starts on login
```

**WSL2 / Linux** (dev host):
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

The installer normally starts the service for you. Verify it's reachable:
```bash
curl -fsS http://127.0.0.1:11434/api/tags
# Expect JSON, e.g. {"models":[]} on a fresh install.
```

**Only if** the curl above fails ("Connection refused"), Ollama isn't
running yet. Pick the option that matches your WSL setup:
```bash
systemctl status ollama   # modern WSL2 with systemd enabled — should be 'active (running)'
sudo systemctl start ollama   # if it's installed but not running
ollama serve &            # fallback: foreground daemon in the current shell
```

Re-run the `curl /api/tags` check until it returns JSON.

### 4.2 Pull the models

The bootstrap script picks a sensible profile for your host (`minimal` on WSL,
`standard` on Apple Silicon Mac) and pulls only what's missing.

```bash
./scripts/bootstrap_models.sh                # default profile for this host
./scripts/bootstrap_models.sh -p minimal     # smallest viable set (~4 GB)
./scripts/bootstrap_models.sh -p standard    # adds the 7B brain (~9 GB)
./scripts/bootstrap_models.sh -p full        # adds Tier-2 VLM (Mac only)
./scripts/bootstrap_models.sh --dry-run      # show plan without downloading
```

**WSL dev recommendation**: stick with `minimal`. It's enough to exercise the
perception pipeline (Phase 1+) and to run the small NL-rule evaluator.
Tier-2 perception and the 14B brain only make sense on the Mac.

The script is idempotent — re-running pulls only new/updated tags.

### 4.3 Verify

```bash
uv run housekeeper models list               # show what's registered
uv run housekeeper models verify             # check the default profile
uv run housekeeper models verify -p full     # check a specific profile
```

Exit codes:
- `0`: all models in the profile are available (or skipped for not-applicable backends).
- `1`: at least one model is missing or Ollama is unreachable.
- `2`: bad arguments (e.g. unknown profile).

---

## 5. Notifications · ntfy (Phase 0.3)

Housekeeper pushes notifications through a **self-hosted ntfy** server. The
ntfy phone app subscribes to a private topic and gets pushes.

### 5.1 Install ntfy

**macOS** (production host):
```bash
brew install ntfy
```

**WSL2 / Linux** (dev host) — official apt repo:
```bash
curl -sSL https://archive.heckel.io/apt/pubkey.txt | sudo apt-key add -
sudo apt-add-repository 'deb https://archive.heckel.io/apt /'
sudo apt update && sudo apt install ntfy
```

### 5.2 Create state directories and read the next-step hints

```bash
./scripts/bootstrap_ntfy.sh
```

This is idempotent — it creates `~/.housekeeper/var/ntfy/` and
`~/.housekeeper/var/log/` and prints the install/launchctl/systemctl commands
for your platform.

### 5.3 Install the service unit

**macOS** (launchd, runs on login):
```bash
# rewrite the placeholder paths and copy into LaunchAgents
sed "s|__HOUSEKEEPER_REPO__|$PWD|g; s|__HOME__|$HOME|g" \
    launchd/com.housekeeper.ntfy.plist \
    > ~/Library/LaunchAgents/com.housekeeper.ntfy.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.housekeeper.ntfy.plist
```

**WSL2 / Linux** (systemd user unit):
```bash
mkdir -p ~/.config/systemd/user
cp systemd/ntfy.service ~/.config/systemd/user/housekeeper-ntfy.service
systemctl --user daemon-reload
systemctl --user enable --now housekeeper-ntfy
journalctl --user -u housekeeper-ntfy -f   # tail logs
```

Verify the server is up:
```bash
uv run housekeeper notify verify
```

### 5.4 Generate a private topic

ntfy topics are public-by-default — anyone with the topic name can publish.
Use `notify init` to write a random topic into the **gitignored** local
override:

```bash
uv run housekeeper notify init
# Wrote configs/services.yaml.local
#   topic       : housekeeper-AbCdEf012345...
#   publish URL : http://127.0.0.1:8080/housekeeper-AbCdEf012345...
```

Re-run with `--force` to rotate the topic.

### 5.5 Subscribe your phone

1. Install the **ntfy** app (iOS App Store / Google Play).
2. Add a subscription using the publish URL from `notify show`:
   ```bash
   uv run housekeeper notify show
   ```
3. Hit it with a smoke test:
   ```bash
   uv run housekeeper notify send "Hello" "from housekeeper" \
       --priority 4 --tags house,test
   ```
4. The notification should appear on your phone within a second.

> **Reachability**: on the same Wi-Fi, the phone reaches the Mac by IP.
> Once Tailscale (Phase 0.5) is up, point the phone at the Tailnet name so
> notifications arrive from anywhere. Until then, you'll only see them on
> your home network.

### 5.6 CLI cheat-sheet

| Command | Purpose |
|---|---|
| `uv run housekeeper notify init [--force]` | Generate / rotate private topic |
| `uv run housekeeper notify show` | Print resolved endpoint + topic |
| `uv run housekeeper notify send "title" "body" [-p 4] [-t a,b] [-c URL]` | Send one push |
| `uv run housekeeper notify verify` | Probe the local ntfy server |

---

## 6. Project layout (current)

```
housekeeper/
├── src/housekeeper/        # package source
│   ├── __init__.py         # exports __version__
│   ├── cli.py              # Typer entry point (housekeeper ...)
│   ├── models.py           # configs/models.yaml loader + verifier
│   ├── notifier.py         # ntfy notifier (Phase 0.3)
│   ├── services.py         # configs/services.yaml loader (Phase 0.3)
│   └── platform_info.py    # OS / arch detection helpers
├── tests/                  # pytest suite (mirrors src/)
├── configs/                # versioned config (models.yaml, services.yaml, ntfy/, …)
├── scripts/                # ops scripts (bootstrap_models.sh, bootstrap_ntfy.sh)
├── launchd/                # macOS service plists
├── systemd/                # Linux/WSL user units
├── docs/                   # deep-dives that don't belong inline in design.md
├── design.md               # architecture + phased implementation plan
├── runbook.md              # this file
├── pyproject.toml          # project + tool config (ruff, pytest, hatchling)
├── .pre-commit-config.yaml # git hooks
├── .gitignore / .gitattributes / .gitconfig
└── README.md
```

The src layout (`src/housekeeper/...` rather than top-level `housekeeper/`)
prevents accidental imports of the working copy when running tests — tests
must use the installed editable copy via `uv run pytest`.

---

## 7. Cross-platform notes

| Concern | macOS | Linux / WSL |
|---|---|---|
| Inference (MLX) | ✅ preferred | ❌ skipped (use Ollama or mocks) |
| Inference (Ollama) | ✅ | ✅ |
| `launchd` services | ✅ used in prod | n/a — use bare processes or `systemd --user` |
| RTSP camera | reachable on LAN | normally not reachable; use sample files or mock streams in tests |
| Push notifications | ntfy app on iPhone | ntfy CLI for ad-hoc tests |

`src/housekeeper/platform_info.py` exposes `is_macos()`, `is_apple_silicon()`,
`is_wsl()`, `supports_mlx()`. Use those instead of sprinkling `platform.system()`
checks throughout the codebase.

---

## 8. Phase status checklist

This mirrors §10 of `design.md`. Tick items here as you finish them in the
field (vs. ticking them in the design doc, which tracks coding progress).

- [ ] Phase 0 — Bootstrap operational (models pulled, services running, doctor passes)
- [ ] Phase 1 — Video pipeline running against your camera
- [ ] Phase 2 — Notifier wired to your phone
- [ ] Phase 3 — Memory + timeline online
- [ ] Phase 4 — Agent autonomously notifies
- [ ] Phase 5 — Two-way chat working
- [ ] Phase 6 — Tier-2 perception integrated
- [ ] Phase 7 — Natural-language watches operational
- [ ] Phase 8 — Running unattended
- [ ] Phase 9 — Eval harness passing baselines
- [ ] Phase 10 — Stretch goals

---

## 9. Operational tasks **you** own

I (the agent) will not run these for you. They require accounts, hardware, or
machine-level installs.

- [ ] Install `uv` on your Mac (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
- [ ] Install Homebrew on the Mac if you don't have it.
- [ ] **WSL dev (now)**: install Ollama (`curl -fsSL https://ollama.com/install.sh | sh`) and run `./scripts/bootstrap_models.sh -p minimal`.
- [ ] **Mac (later)**: install Ollama (`brew install ollama && brew services start ollama`) and run `./scripts/bootstrap_models.sh`.
- [ ] **WSL dev (now)**: install ntfy + systemd user unit (see §5.1–5.3), then `uv run housekeeper notify init`.
- [ ] **Mac (later)**: install ntfy + launchd plist (see §5.1–5.3), then `uv run housekeeper notify init`.
- [ ] Install the `ntfy` app on your phone and subscribe to your topic (§5.5).
- [ ] Install Tailscale (Phase 0.5) and approve the device.
- [ ] Find your camera's RTSP URL + credentials (Phase 1).
- [ ] Approve macOS permissions for `Messages.app` automation (Phase 5).

---

## 10. Troubleshooting

### `uv: command not found`
`~/.local/bin` is not on your `$PATH`. Add this to `~/.bashrc` / `~/.zshrc`:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

### `housekeeper: command not found`
Either you're not inside the venv, or you didn't install with `uv sync`. Use
`uv run housekeeper ...` (always works) or activate the venv:
```bash
source .venv/bin/activate
```

### Tests can't import `housekeeper`
You probably ran `pytest` directly instead of `uv run pytest`. Direct
invocation uses the system Python, which doesn't have the editable install.

---

More sections will be added as later phases land (model bootstrap, NATS, ntfy,
service plists, camera setup, …).
