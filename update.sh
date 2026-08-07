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
#   - Restarts the touch console, if one is running on this machine
#
# Static files (web/static/) take effect on the next browser *load*, which is
# not the same as immediately: the kiosk browser holds its JavaScript for as
# long as it runs, and it runs for weeks. See the console restart at the end.

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
    --exclude='.lgd-nfy*' \
    ./ "$INSTALL_DIR/"

# Ownership, here and not at the end. rsync runs as root and -a preserves the
# *source* owner, so every synced file belongs to whoever owns the checkout.
# When this chown sat after the dependency step, a failed `pip install` aborted
# the script under `set -e` and left the whole tree owned by the wrong user —
# and because lgpio creates its notification FIFO in the working directory, the
# service came up with a lightning thread that could not start, for a reason
# nothing in the deploy output pointed at (2026-08-06). Doing it first means an
# abort further down leaves a tree that is at least consistently owned.
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ---------------------------------------------------------------------------
# Reinstall Python dependencies if needed
# ---------------------------------------------------------------------------

if $REINSTALL; then
    echo "Reinstalling Python dependencies …"
    "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
    "$INSTALL_DIR/.venv/bin/pip" install --quiet -e "$INSTALL_DIR/[web]"
fi

# ---------------------------------------------------------------------------
# AS3935 lightning sensor — only when the installed config asks for it
# ---------------------------------------------------------------------------
#
# Keyed off the installed config rather than a flag: the installation that has
# the sensor configured is exactly the one that needs this, so the two cannot
# fall out of step with what the service will actually start.
if grep -qE '^\s*\[lightning\]' "$INSTALL_DIR/config.toml" 2>/dev/null; then
    echo "[lightning] found in config.toml — checking GPIO support …"

    # The shim comes from apt, not pip. Raspberry Pi OS ships python3-rpi-lgpio
    # and python3-lgpio prebuilt; pip would rebuild the same C extension from
    # source and needs swig, which a weather-station Pi has no reason to carry.
    # Installing here is not this script's business — it says what is missing.
    # Aus einem neutralen Verzeichnis: `import lgpio` legt seine
    # Benachrichtigungs-FIFO `.lgd-nfy<n>` im ARBEITSVERZEICHNIS an, und das ist
    # hier der Git-Checkout. Als root ausgefuehrt hinterliess die Pruefung dort
    # eine root-eigene FIFO, die der Sync anschliessend nach $INSTALL_DIR
    # kopierte -- und die einen `diff` zwischen Checkout und Installation
    # scheitern liess, weil diff FIFOs nicht vergleichen kann (06.08.2026).
    if ! (cd /tmp && python3 -c 'import RPi.GPIO') 2>/dev/null; then
        echo "ERROR: the AS3935 needs the RPi.GPIO shim, which is not installed." >&2
        echo "       sudo apt install python3-rpi-lgpio" >&2
        echo "       (do NOT install RPi.GPIO or pip-install rpi-lgpio alongside it)" >&2
        exit 1
    fi

    # The venv is isolated, so it cannot see those system packages. This .pth
    # appends the system directory to sys.path — the same thing
    # `--system-site-packages` does, without recreating the venv. Appended, so
    # the venv's own packages keep precedence; verified on 2026-08-06 with
    # smbus2, which exists in both and resolves to the venv copy.
    SITE_PACKAGES=$("$INSTALL_DIR/.venv/bin/python" -c 'import site; print(site.getsitepackages()[0])')
    printf '%s\n' /usr/lib/python3/dist-packages > "$SITE_PACKAGES/zz-system-gpio.pth"
    chown "$SERVICE_USER:$SERVICE_USER" "$SITE_PACKAGES/zz-system-gpio.pth"

    if ! (cd /tmp && "$INSTALL_DIR/.venv/bin/python" -c 'import RPi.GPIO') 2>/dev/null; then
        echo "ERROR: the venv still cannot import RPi.GPIO after linking the" >&2
        echo "       system packages. Do the venv and /usr/bin/python3 have the" >&2
        echo "       same Python version?" >&2
        exit 1
    fi
    echo "  GPIO support OK (system packages visible to the venv)"

    # /dev/gpiochip* is root:gpio mode 660. Without the group the lightning
    # thread dies at GPIO.setup() while everything else comes up fine. The
    # restart below is when systemd re-reads the group list.
    if ! id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx gpio; then
        echo "  Adding '$SERVICE_USER' to the 'gpio' group …"
        usermod -aG gpio "$SERVICE_USER"
    fi
fi

# ---------------------------------------------------------------------------
# Ownership & service restart
# ---------------------------------------------------------------------------

# Second pass, deliberately. The first one above runs right after the sync so
# an abort cannot leave a half-owned tree; this one picks up what pip wrote
# into .venv afterwards, which the sync never touches.
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

# The console is a second service running a browser, and a browser keeps the
# JavaScript it loaded for as long as the process lives — here, weeks. Without
# this restart a front-end deploy reaches every visitor except the one display
# that is actually on the wall, and nothing ever reports the discrepancy.
#
# Measured 2026-08-07: the panel was serving the build of 2026-08-06 08:55,
# missing the lightning page that had gone into production the evening before.
# Neither the deploy nor the service restart would have corrected it.
#
# This works because the origin sends `Cache-Control: no-cache` for static
# files (web/app.py) — the browser revalidates on load rather than trusting a
# heuristic freshness lifetime it invented for itself. Removing that header
# makes this restart silently ineffective again: the browser would start,
# consult its own disk cache and never ask. That is exactly how the 2026-08-07
# case survived a restart.
#
# Conditional: the console is optional and most installations do not have it.
CONSOLE_SERVICE=weather-console.service
if systemctl is-active --quiet "$CONSOLE_SERVICE"; then
    echo "Restarting $CONSOLE_SERVICE (the kiosk browser holds the old front end) …"
    systemctl restart "$CONSOLE_SERVICE"
fi

echo ""
echo "Update complete. Watching logs for 5 s (Ctrl+C to exit):"
journalctl -u "$SERVICE_FILE" -f --no-pager &
JOURNAL_PID=$!
sleep 5
kill "$JOURNAL_PID" 2>/dev/null || true
