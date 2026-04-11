#!/usr/bin/env bash
# deploy/uninstall.sh
# ─────────────────────────────────────────────────────────────────────────────
# Remove Minervini AI systemd units from ShreeVault.
# Idempotent — safe to run when units are already stopped/removed.
#
# Usage:
#   sudo bash deploy/uninstall.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SYSTEMD_DIR="/etc/systemd/system"

UNITS=(
    "minervini-daily.timer"
    "minervini-daily.service"
    "minervini-api.service"
    "minervini-dashboard.service"
)

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use: sudo bash deploy/uninstall.sh)" >&2
    exit 1
fi

echo "────────────────────────────────────────────────────────────"
echo " Minervini AI — systemd uninstall"
echo "────────────────────────────────────────────────────────────"

# ── Step 1: Stop and disable each unit (gracefully ignore missing) ────────────
echo ""
echo "[1/3] Stopping and disabling units …"
for unit in "${UNITS[@]}"; do
    if systemctl is-active --quiet "${unit}" 2>/dev/null; then
        systemctl stop "${unit}"
        echo "  ✓  stopped  ${unit}"
    else
        echo "  –  not running: ${unit}"
    fi

    if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
        systemctl disable "${unit}"
        echo "  ✓  disabled ${unit}"
    else
        echo "  –  not enabled: ${unit}"
    fi
done

# ── Step 2: Remove symlinks ───────────────────────────────────────────────────
echo ""
echo "[2/3] Removing symlinks from ${SYSTEMD_DIR} …"
for unit in "${UNITS[@]}"; do
    target="${SYSTEMD_DIR}/${unit}"
    if [[ -L "${target}" ]]; then
        rm "${target}"
        echo "  ✓  removed ${target}"
    elif [[ -e "${target}" ]]; then
        echo "  WARNING: ${target} exists but is not a symlink — not removing."
    else
        echo "  –  not found: ${target}"
    fi
done

# ── Step 3: Reload daemon ─────────────────────────────────────────────────────
echo ""
echo "[3/3] Reloading systemd daemon …"
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true
echo "  ✓  daemon-reload OK"

echo ""
echo "────────────────────────────────────────────────────────────"
echo " Uninstall complete. Unit files in deploy/ are untouched."
echo " Re-run deploy/install.sh to reinstall."
echo "────────────────────────────────────────────────────────────"
