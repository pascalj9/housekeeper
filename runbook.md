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
| Health check (placeholder until Phase 0.5) | `uv run housekeeper doctor` |
| Run tests | `uv run pytest` |
| Coverage report | `uv run pytest --cov` |
| Lint + format check | `uv run ruff check . && uv run ruff format --check .` |
| Auto-fix lint + format | `uv run ruff check --fix . && uv run ruff format .` |
| Run all pre-commit hooks | `uv run pre-commit run --all-files` |
| Add a runtime dependency | `uv add <package>` |
| Add a dev dependency | `uv add --optional dev <package>` |

---

## 4. Project layout (current)

```
housekeeper/
├── src/housekeeper/        # package source
│   ├── __init__.py         # exports __version__
│   ├── cli.py              # Typer entry point (housekeeper ...)
│   └── platform_info.py    # OS / arch detection helpers
├── tests/                  # pytest suite (mirrors src/)
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

## 5. Cross-platform notes

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

## 6. Phase status checklist

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

## 7. Operational tasks **you** own

I (the agent) will not run these for you. They require accounts, hardware, or
machine-level installs.

- [ ] Install `uv` on your Mac (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
- [ ] Install Homebrew on the Mac if you don't have it.
- [ ] Install Tailscale (Phase 0.3) and approve the device.
- [ ] Install Ollama (Phase 0.2) and accept its network permissions.
- [ ] Pull model weights (Phase 0.2) — needs internet once.
- [ ] Find your camera's RTSP URL + credentials (Phase 1).
- [ ] Install the `ntfy` app on your phone and subscribe to your topic (Phase 2).
- [ ] Approve macOS permissions for `Messages.app` automation (Phase 5).

---

## 8. Troubleshooting

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
