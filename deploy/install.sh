#!/usr/bin/env bash
# deploy/install.sh
# ─────────────────────────────────────────────────────────────────────────────
# Install Minervini AI systemd units — portable across any machine / username.
# Idempotent — safe to run more than once.
#
# Usage:
#   sudo bash deploy/install.sh
#
# What it does:
#   1. Auto-detects PROJECT_DIR from this script's location (no hardcoding)
#   2. Auto-detects the invoking user (via $SUDO_USER)
#   3. Writes patched .service/.timer files → /etc/systemd/system/
#      (replaces @@PROJECT_DIR@@ and @@DEPLOY_USER@@ placeholders)
#   4. Reloads the systemd daemon
#   5. Enables and starts the timer + long-running services
#   6. Prints a status summary
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Auto-detect paths — works on any machine, any username ───────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${SCRIPT_DIR}"
SYSTEMD_DIR="/etc/systemd/system"

# Detect the real (non-root) user who invoked sudo
DEPLOY_USER="${SUDO_USER:-}"
if [[ -z "${DEPLOY_USER}" ]]; then
    echo "ERROR: Could not detect the invoking user." >&2
    echo "       Please run as: sudo bash deploy/install.sh" >&2
    exit 1
fi

UNITS=(
    "minervini-daily.service"
    "minervini-daily.timer"
    "minervini-api.service"
    "minervini-dashboard.service"
)

# ── Preflight checks ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use: sudo bash deploy/install.sh)" >&2
    exit 1
fi

if [[ ! -d "${DEPLOY_DIR}" ]]; then
    echo "ERROR: Deploy directory not found: ${DEPLOY_DIR}" >&2
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    echo "ERROR: .env file not found at ${PROJECT_DIR}/.env" >&2
    echo "       Copy .env.example to .env and fill in your values first." >&2
    exit 1
fi

echo "────────────────────────────────────────────────────────────"
echo " Minervini AI — systemd install"
echo " Project : ${PROJECT_DIR}"
echo " User    : ${DEPLOY_USER}"
echo " Units   : ${SYSTEMD_DIR}"
echo "────────────────────────────────────────────────────────────"

# ── Step 1: Write patched unit files ─────────────────────────────────────────
echo ""
echo "[1/4] Writing patched unit files …"
for unit in "${UNITS[@]}"; do
    src="${DEPLOY_DIR}/${unit}"
    dst="${SYSTEMD_DIR}/${unit}"

    if [[ ! -f "${src}" ]]; then
        echo "  WARNING: Template not found, skipping: ${src}"
        continue
    fi

    # Substitute placeholders with real values detected at deploy time
    sed \
        -e "s|@@PROJECT_DIR@@|${PROJECT_DIR}|g" \
        -e "s|@@DEPLOY_USER@@|${DEPLOY_USER}|g" \
        "${src}" > "${dst}"

    echo "  ✓  ${dst}"
    echo "       PROJECT_DIR → ${PROJECT_DIR}"
    echo "       DEPLOY_USER → ${DEPLOY_USER}"
done

# ── Step 2: Reload daemon ─────────────────────────────────────────────────────
echo ""
echo "[2/4] Reloading systemd daemon …"
systemctl daemon-reload
echo "  ✓  daemon-reload OK"

# ── Step 3: Enable + start units ─────────────────────────────────────────────
echo ""
echo "[3/4] Enabling and starting units …"

systemctl enable --now minervini-daily.timer
echo "  ✓  minervini-daily.timer enabled + started"

systemctl enable --now minervini-api.service
echo "  ✓  minervini-api.service enabled + started"

systemctl enable --now minervini-dashboard.service
echo "  ✓  minervini-dashboard.service enabled + started"

# ── Step 4: Status summary ────────────────────────────────────────────────────
echo ""
echo "[4/4] Status summary …"
echo ""

for unit in minervini-daily.timer minervini-api.service minervini-dashboard.service; do
    echo "── ${unit} ──"
    systemctl status "${unit}" --no-pager --lines=3 || true
    echo ""
done

echo "────────────────────────────────────────────────────────────"
echo " Installation complete."
echo ""
echo " Useful commands:"
echo "   journalctl -u minervini-daily -n 50 -f"
echo "   journalctl -u minervini-api -n 50 -f"
echo "   journalctl -u minervini-dashboard -n 50 -f"
echo "   systemctl list-timers --all | grep minervini"
echo "────────────────────────────────────────────────────────────"
