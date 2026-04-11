#!/usr/bin/env bash
# deploy/install.sh
# ─────────────────────────────────────────────────────────────────────────────
# Install Minervini AI systemd units on ShreeVault.
# Idempotent — safe to run more than once (symlinks are force-created).
#
# Usage:
#   sudo bash deploy/install.sh
#
# What it does:
#   1. Symlinks all .service and .timer files → /etc/systemd/system/
#   2. Reloads the systemd daemon
#   3. Enables and starts the timer + long-running services
#   4. Prints a status summary
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR="/home/ubuntu/projects/minervini_ai"
DEPLOY_DIR="${PROJECT_DIR}/deploy"
SYSTEMD_DIR="/etc/systemd/system"

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
echo " Units   : ${SYSTEMD_DIR}"
echo "────────────────────────────────────────────────────────────"

# ── Step 1: Symlink unit files ────────────────────────────────────────────────
echo ""
echo "[1/4] Symlinking unit files …"
for unit in "${UNITS[@]}"; do
    src="${DEPLOY_DIR}/${unit}"
    dst="${SYSTEMD_DIR}/${unit}"

    if [[ ! -f "${src}" ]]; then
        echo "  WARNING: Source file not found, skipping: ${src}"
        continue
    fi

    # Force-create symlink (idempotent)
    ln -sf "${src}" "${dst}"
    echo "  ✓  ${dst} → ${src}"
done

# ── Step 2: Reload daemon ─────────────────────────────────────────────────────
echo ""
echo "[2/4] Reloading systemd daemon …"
systemctl daemon-reload
echo "  ✓  daemon-reload OK"

# ── Step 3: Enable + start units ─────────────────────────────────────────────
echo ""
echo "[3/4] Enabling and starting units …"

# Daily timer (runs the one-shot service on schedule)
systemctl enable --now minervini-daily.timer
echo "  ✓  minervini-daily.timer enabled + started"

# API service
systemctl enable --now minervini-api.service
echo "  ✓  minervini-api.service enabled + started"

# Dashboard service
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
