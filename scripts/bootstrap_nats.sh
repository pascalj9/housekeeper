#!/usr/bin/env bash
# =============================================================================
# bootstrap_nats.sh — create state directories NATS needs and print next steps.
#
# Idempotent. Does NOT install nats-server or start the service — that's the
# operator's job (see runbook §6).
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VAR_DIR="${HOME}/.housekeeper/var"
NATS_VAR="${VAR_DIR}/nats/jetstream"
LOG_DIR="${VAR_DIR}/log"

mkdir -p "${NATS_VAR}" "${LOG_DIR}"
echo "Created:"
echo "  ${NATS_VAR}/"
echo "  ${LOG_DIR}/"
echo
echo "NATS config (committed): ${REPO_ROOT}/configs/nats/server.conf"
echo
echo "Next steps:"
case "$(uname -s)" in
  Darwin)
    cat <<EOF
  1. Install nats-server:
       brew install nats-server
  2. Install plist:
       sed "s|__HOUSEKEEPER_REPO__|${REPO_ROOT}|g; s|__HOME__|${HOME}|g" \\
         "${REPO_ROOT}/launchd/com.housekeeper.nats.plist" \\
         > ~/Library/LaunchAgents/com.housekeeper.nats.plist
       launchctl bootstrap gui/\$UID \\
         ~/Library/LaunchAgents/com.housekeeper.nats.plist
  3. Verify:           uv run housekeeper bus verify
  4. Init JetStream:   uv run housekeeper bus init
EOF
    ;;
  Linux)
    cat <<EOF
  1. Install nats-server (release tarball — most reliable):
       VER=2.10.20
       ARCH=\$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
       curl -fsSL "https://github.com/nats-io/nats-server/releases/download/v\${VER}/nats-server-v\${VER}-linux-\${ARCH}.tar.gz" \\
         | sudo tar -xz -C /tmp
       sudo install -m 0755 /tmp/nats-server-v\${VER}-linux-\${ARCH}/nats-server /usr/local/bin/nats-server
  2. Install systemd user unit:
       mkdir -p ~/.config/systemd/user
       cp "${REPO_ROOT}/systemd/nats.service" \\
          ~/.config/systemd/user/housekeeper-nats.service
       systemctl --user daemon-reload
       systemctl --user enable --now housekeeper-nats
       systemctl --user status housekeeper-nats --no-pager
  3. Verify:           uv run housekeeper bus verify
  4. Init JetStream:   uv run housekeeper bus init
EOF
    ;;
  *)
    echo "  Manual install required for $(uname -s)."
    ;;
esac
