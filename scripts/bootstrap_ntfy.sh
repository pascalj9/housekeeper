#!/usr/bin/env bash
# =============================================================================
# bootstrap_ntfy.sh — create state directories ntfy needs and print next steps.
#
# Idempotent. Does NOT install ntfy or start the service — that's the
# operator's job (see runbook §5).
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VAR_DIR="${HOME}/.housekeeper/var"
NTFY_VAR="${VAR_DIR}/ntfy"
LOG_DIR="${VAR_DIR}/log"

mkdir -p "${NTFY_VAR}/attachments" "${LOG_DIR}"
echo "Created:"
echo "  ${NTFY_VAR}/"
echo "  ${LOG_DIR}/"
echo
echo "ntfy config (committed): ${REPO_ROOT}/configs/ntfy/server.yml"
echo
echo "Next steps:"
case "$(uname -s)" in
  Darwin)
    cat <<EOF
  1. Install ntfy:    brew install ntfy
  2. Install plist:
       sed "s|__HOUSEKEEPER_REPO__|${REPO_ROOT}|g; s|__HOME__|${HOME}|g" \\
         "${REPO_ROOT}/launchd/com.housekeeper.ntfy.plist" \\
         > ~/Library/LaunchAgents/com.housekeeper.ntfy.plist
       launchctl bootstrap gui/\$UID \\
         ~/Library/LaunchAgents/com.housekeeper.ntfy.plist
  3. Verify:          uv run housekeeper notify verify
EOF
    ;;
  Linux)
    cat <<EOF
  1. Install ntfy (release tarball — most reliable):
       VER=2.11.0
       ARCH=\$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
       curl -fsSL "https://github.com/binwiederhier/ntfy/releases/download/v\${VER}/ntfy_\${VER}_linux_\${ARCH}.tar.gz" \\
         | sudo tar -xz -C /tmp
       sudo install -m 0755 /tmp/ntfy_\${VER}_linux_\${ARCH}/ntfy /usr/local/bin/ntfy
  2. Install systemd user unit:
       # Re-copy the unit so it picks up the env-based PATH lookup
       mkdir -p ~/.config/systemd/user
       cp "${REPO_ROOT}/systemd/ntfy.service" \\
          ~/.config/systemd/user/housekeeper-ntfy.service
       systemctl --user daemon-reload
       systemctl --user enable --now housekeeper-ntfy
       systemctl --user status housekeeper-ntfy --no-pager
  3. Verify:          uv run housekeeper notify verify
EOF
    ;;
  *)
    echo "  Manual install required for $(uname -s)."
    ;;
esac

echo
echo "Then initialise your private ntfy topic:"
echo "  uv run housekeeper notify init"
