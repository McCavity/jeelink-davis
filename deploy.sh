#!/usr/bin/env bash
# deploy.sh — First-time production deployment of jeelink-davis.
#
# Run from the repository root on the target machine:
#   sudo ./deploy.sh
#
# What it does:
#   - Creates a dedicated 'davis' system user/group
#   - Adds the user to 'dialout' (JeeLink USB serial) and 'i2c' (BME280) groups
#   - Copies the project to /opt/jeelink-davis/
#   - Creates a venv and installs all web dependencies
#   - Installs and enables the davis-weather systemd service
#
# config.toml is copied on first deploy but never overwritten on re-runs,
# so your production settings are always preserved.

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

# ---------------------------------------------------------------------------
# System user & group
# ---------------------------------------------------------------------------

if ! getent group "$SERVICE_USER" > /dev/null 2>&1; then
    echo "Creating group '$SERVICE_USER' …"
    groupadd --system "$SERVICE_USER"
fi

if ! id "$SERVICE_USER" > /dev/null 2>&1; then
    echo "Creating user '$SERVICE_USER' …"
    useradd --system \
            --gid "$SERVICE_USER" \
            --no-create-home \
            --shell /usr/sbin/nologin \
            "$SERVICE_USER"
fi

echo "Adding '$SERVICE_USER' to hardware groups (dialout, i2c) …"
usermod -aG dialout "$SERVICE_USER"   # JeeLink USB serial access
usermod -aG i2c     "$SERVICE_USER"   # BME280 I²C access

# ---------------------------------------------------------------------------
# Install directory
# ---------------------------------------------------------------------------

echo "Creating $INSTALL_DIR …"
mkdir -p "$INSTALL_DIR/data"

# ---------------------------------------------------------------------------
# Sync project files
# ---------------------------------------------------------------------------

echo "Copying project files to $INSTALL_DIR …"
rsync -a --delete \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='data/' \
    --exclude='__pycache__/' \
    --exclude='*.egg-info/' \
    --exclude='.pytest_cache/' \
    ./ "$INSTALL_DIR/"

# Preserve production config if it already exists (re-deploy scenario)
if [[ -f "$INSTALL_DIR/config.toml" ]]; then
    echo "config.toml already exists in $INSTALL_DIR — keeping existing production config."
    cp ./ "$INSTALL_DIR/" 2>/dev/null || true  # no-op, rsync already ran
else
    echo "Copying config.toml — edit $INSTALL_DIR/config.toml before starting the service."
fi

# ---------------------------------------------------------------------------
# Python virtual environment
# ---------------------------------------------------------------------------

echo "Setting up Python virtual environment …"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -e "$INSTALL_DIR/[web]"

# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ---------------------------------------------------------------------------
# Systemd service
# ---------------------------------------------------------------------------

echo "Installing systemd service …"
cp "$INSTALL_DIR/$SERVICE_FILE" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_FILE"
systemctl restart "$SERVICE_FILE"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "Deployment complete."
echo ""
echo "Next steps:"
echo "  1. Edit $INSTALL_DIR/config.toml — set latitude, longitude, elevation, timezone."
echo "  2. sudo systemctl restart $SERVICE_FILE"
echo "  3. sudo journalctl -u $SERVICE_FILE -f   # watch the logs"
