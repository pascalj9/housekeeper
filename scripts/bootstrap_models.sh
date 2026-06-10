#!/usr/bin/env bash
# =============================================================================
# bootstrap_models.sh — pull every model in a Housekeeper profile.
#
# Idempotent: re-running pulls only what's missing or updated.
#
# Usage:
#   ./scripts/bootstrap_models.sh              # default profile for this host
#   ./scripts/bootstrap_models.sh -p minimal
#   ./scripts/bootstrap_models.sh -p full
#   ./scripts/bootstrap_models.sh --dry-run    # print what would be pulled
#
# Requirements:
#   - bash 4+
#   - python 3.11+ (uses the project venv via `uv run`)
#   - ollama on PATH (https://ollama.com/download)
#   - uv (https://docs.astral.sh/uv/)
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${REPO_ROOT}/configs/models.yaml"
OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"

PROFILE=""
DRY_RUN=0

# ---- arg parsing ------------------------------------------------------------
usage() {
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--profile) PROFILE="${2:?--profile needs a value}"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    usage 0 ;;
    *) echo "unknown arg: $1" >&2; usage 1 ;;
  esac
done

# ---- platform detection -----------------------------------------------------
detect_os() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux)
      if grep -qi microsoft /proc/version 2>/dev/null; then echo "wsl"
      else echo "linux"
      fi
      ;;
    *) echo "other" ;;
  esac
}
OS="$(detect_os)"

# ---- prerequisites ----------------------------------------------------------
need() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 — $2" >&2; exit 127; }
}

need uv     "install: https://docs.astral.sh/uv/"
need python3 "install Python 3.11+"

if ! command -v ollama >/dev/null 2>&1; then
  cat >&2 <<EOF
missing: ollama

Install instructions:
  macOS (Homebrew):   brew install ollama && brew services start ollama
  macOS (direct):     download from https://ollama.com/download
  Linux / WSL:        curl -fsSL https://ollama.com/install.sh | sh

Once installed, ensure 'ollama serve' is running (Homebrew/Linux services start
it automatically; otherwise run 'ollama serve &' in a separate shell).
EOF
  exit 127
fi

# ---- discover the profile via the Python loader ----------------------------
if [[ -z "$PROFILE" ]]; then
  PROFILE="$(cd "$REPO_ROOT" && uv run --quiet python -c \
    'from housekeeper.models import default_profile; print(default_profile())')"
fi

echo "host    : $OS"
echo "profile : $PROFILE"
echo "config  : $CONFIG_FILE"
echo "ollama  : $OLLAMA_HOST"
echo

# Emit "backend<TAB>name" for each model in the profile.
PROFILE_OUT="$(cd "$REPO_ROOT" && uv run --quiet python -c "
from housekeeper.models import load_config, resolve_profile
cfg = load_config('${CONFIG_FILE}')
for _key, spec in resolve_profile(cfg, '${PROFILE}'):
    print(f'{spec.backend}\t{spec.name}')
")"

# ---- verify ollama daemon ---------------------------------------------------
if ! curl -fsS "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
  cat >&2 <<EOF
ollama daemon not reachable at ${OLLAMA_HOST}.

Start it:
  macOS (Homebrew):  brew services start ollama
  Linux / WSL:       systemctl --user start ollama  (or: ollama serve &)

If you customised the host, export OLLAMA_HOST before running this script.
EOF
  exit 1
fi

# ---- pull loop --------------------------------------------------------------
mlx_skipped=0
ollama_done=0
ollama_total=0

while IFS=$'\t' read -r backend name; do
  case "$backend" in
    ollama)
      ollama_total=$((ollama_total + 1))
      if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] ollama pull ${name}"
      else
        echo "→ ollama pull ${name}"
        ollama pull "${name}"
      fi
      ollama_done=$((ollama_done + 1))
      ;;
    mlx)
      if [[ "$OS" == "macos" && "$(uname -m)" == "arm64" ]]; then
        echo "  [defer] MLX model '${name}' — pull handled in Phase 6"
      else
        echo "  [skip]  MLX model '${name}' — requires Apple Silicon"
      fi
      mlx_skipped=$((mlx_skipped + 1))
      ;;
    *)
      echo "  [warn] unknown backend '${backend}' for '${name}'" >&2
      ;;
  esac
done <<< "$PROFILE_OUT"

# ---- summary ----------------------------------------------------------------
echo
echo "ollama: ${ollama_done}/${ollama_total} processed"
if [[ "$mlx_skipped" -gt 0 ]]; then
  echo "mlx   : ${mlx_skipped} deferred/skipped"
fi
echo
echo "Verify with:"
echo "  uv run housekeeper models verify --profile ${PROFILE}"
