# Housekeeper

A local-first AI agent that watches a single IP camera on your LAN, interprets
what it sees, and talks to you about it — fully offline on an Apple Silicon
Mac.

- **Design**: see [`design.md`](design.md)
- **Runbook (setup & ops)**: see [`runbook.md`](runbook.md)

## Status

Phase 0 — Bootstrap (in progress). The CLI entry point and project skeleton
are wired; `housekeeper doctor` is a placeholder until Phase 0.5.

## Quick start (dev)

```bash
# 1. Install uv (one-time): https://docs.astral.sh/uv/
# 2. Sync a virtual env and install in editable mode with dev extras:
uv sync --extra dev
# 3. Run the CLI:
uv run housekeeper version
# 4. Run the test suite:
uv run pytest
```

See [`runbook.md`](runbook.md) for the full setup and operational guide.
