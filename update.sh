#!/usr/bin/env bash
# update.sh — Deploy updated code to an existing production installation.
#
# Run from the repository root on the target machine after pulling new commits:
#   git pull
#   sudo ./update.sh
#
# What it does:
#   - Syncs changed project files to /opt/jeelink-davis/
#     (config.toml is never overwritten)
#   - Reinstalls Python dependencies if pyproject.toml changed
#   - Restarts the davis-weather service
#
# Static files (web/static/) take effect immediately on browser refresh;
# the service restart is still performed to pick up any Python changes.

set -euo pipefail

INSTALL_DIR=/opt/jeelink-davis
SERVICE_USER=davis
SERVICE_FILE=davis-weather.service

# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root or with sudo." >&2
    exit 1
fi

if [[ ! -f pyproject.toml ]]; then
    echo "ERROR: run this script from the repository root." >&2
    exit 1
fi

if [[ ! -d "$INSTALL_DIR" ]]; then
    echo "ERROR: $INSTALL_DIR does not exist. Run deploy.sh first." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Detect dependency changes before overwriting
# ---------------------------------------------------------------------------

REINSTALL=false
if ! diff -q pyproject.toml "$INSTALL_DIR/pyproject.toml" > /dev/null 2>&1; then
    echo "pyproject.toml changed — will reinstall dependencies."
    REINSTALL=true
fi

# The systemd unit lives outside INSTALL_DIR, so the rsync below never reaches
# it. Until 2026-08-06 this script did not touch it at all: a change to
# davis-weather.service was copied into /opt as an inert file while the running
# unit kept its old content — a deploy that reports success and changes nothing.
# Found when After=network-online.target silently failed to take effect.
UNIT_TARGET=/etc/systemd/system/$SERVICE_FILE
UNIT_CHANGED=false
if [[ -f $SERVICE_FILE ]] && ! diff -q "$SERVICE_FILE" "$UNIT_TARGET" > /dev/null 2>&1; then
    echo "$SERVICE_FILE changed — will install it and reload systemd."
    UNIT_CHANGED=true
fi

# ---------------------------------------------------------------------------
# Sync project files (preserve production config and database)
# ---------------------------------------------------------------------------

echo "Syncing project files to $INSTALL_DIR …"
rsync -a --delete \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='data/' \
    --exclude='__pycache__/' \
    --exclude='*.egg-info/' \
    --exclude='.pytest_cache/' \
    --exclude='config.toml' \
    ./ "$INSTALL_DIR/"

# ---------------------------------------------------------------------------
# Reinstall Python dependencies if needed
# ---------------------------------------------------------------------------

if $REINSTALL; then
    echo "Reinstalling Python dependencies …"
    "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
    "$INSTALL_DIR/.venv/bin/pip" install --quiet -e "$INSTALL_DIR/[web]"
fi

# ---------------------------------------------------------------------------
# Ownership & service restart
# ---------------------------------------------------------------------------

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Before the restart, not after — a restart with a stale unit would run the old
# ordering and dependencies for one more cycle.
if $UNIT_CHANGED; then
    echo "Installing $SERVICE_FILE and reloading systemd …"
    install -m 644 "$SERVICE_FILE" "$UNIT_TARGET"
    systemctl daemon-reload
fi

echo "Restarting $SERVICE_FILE …"
systemctl restart "$SERVICE_FILE"

if $UNIT_CHANGED; then
    echo "Unit ordering now: $(systemctl show "$SERVICE_FILE" -p After --value)"
fi

echo ""
echo "Update complete. Watching logs for 5 s (Ctrl+C to exit):"
journalctl -u "$SERVICE_FILE" -f --no-pager &
JOURNAL_PID=$!
sleep 5
kill "$JOURNAL_PID" 2>/dev/null || true
