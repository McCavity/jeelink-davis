#!/usr/bin/env bash
# install-console.sh — optional kiosk display for the touch console.
#
# Run from the repository root on the machine with the touch panel attached:
#   sudo ./install-console.sh [--lang de] [--rotate 270] [--output DSI-1]
#
# Separate from deploy.sh on purpose: a touch display is optional hardware,
# and an installation without one must not be asked to install a kiosk.
#
# The service user (davis, created by deploy.sh) is a plain system account
# with no login shell and no PAM/logind session. A Wayland compositor still
# needs a "seat" to reach the GPU and touch input without running as root, so
# this installs and enables seatd — a seat-management daemon built for
# exactly that case — and adds the service user to whichever group seatd
# created for seat access on this system. That group name is not the same
# across distributions/packagings, so it is discovered here rather than
# assumed.

set -euo pipefail

SERVICE_USER=davis
SERVICE_FILE=weather-console.service
CONSOLE_STATE_DIR=/var/lib/weather-console
LANG_CODE=en
ROTATE=270
OUTPUT=DSI-1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lang)   LANG_CODE="$2"; shift 2 ;;
        --rotate) ROTATE="$2";    shift 2 ;;
        --output) OUTPUT="$2";    shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root or with sudo." >&2
    exit 1
fi

if [[ ! -f pyproject.toml ]]; then
    echo "ERROR: run this script from the repository root." >&2
    exit 1
fi

if ! id "$SERVICE_USER" > /dev/null 2>&1; then
    echo "ERROR: user '$SERVICE_USER' does not exist — run deploy.sh first." >&2
    exit 1
fi

# --lang ends up both inside a sed replacement and inside a URL query string
# baked into the installed unit file. Reject anything that could break
# either — sed's replacement text treats '&', '\' and the delimiter
# specially, and a stray '&' or '#' in a query string changes its meaning.
if [[ ! "$LANG_CODE" =~ ^[A-Za-z-]{2,10}$ ]]; then
    echo "ERROR: --lang '$LANG_CODE' is not a plain language code (letters/hyphen, 2-10 chars)." >&2
    exit 1
fi

# --rotate goes verbatim into a generated autostart shell script; keep it to
# a transform value wlr-randr actually accepts, not just "not empty".
case "$ROTATE" in
    0|90|180|270|normal|flipped|flipped-90|flipped-180|flipped-270) ;;
    *)
        echo "ERROR: --rotate '$ROTATE' is not a valid wlr-randr transform." >&2
        echo "  Valid values: 0 90 180 270 normal flipped flipped-90 flipped-180 flipped-270" >&2
        exit 1
        ;;
esac

# --output names a connector (e.g. DSI-1, HDMI-A-1); keep it to that shape —
# it also goes verbatim into the generated autostart shell script.
if [[ ! "$OUTPUT" =~ ^[A-Za-z0-9-]+$ ]]; then
    echo "ERROR: --output '$OUTPUT' is not a plain connector name." >&2
    exit 1
fi

echo "Installing labwc, wlr-randr, chromium, seatd, curl and a colour emoji font …"
apt-get update -qq
apt-get install -y --no-install-recommends labwc wlr-randr chromium seatd curl fonts-noto-color-emoji

echo "Enabling seatd …"
systemctl enable --now seatd.service

SEAT_GROUP=""
for candidate in seat _seatd seatd; do
    if getent group "$candidate" > /dev/null 2>&1; then
        SEAT_GROUP="$candidate"
        break
    fi
done
if [[ -z "$SEAT_GROUP" ]]; then
    echo "ERROR: could not find the group seatd uses for seat access." >&2
    echo "  Checked: seat, _seatd, seatd — none exist on this system." >&2
    echo "  Find the real one with: getent group | grep -i seat" >&2
    echo "  then either add '$SERVICE_USER' to it yourself:" >&2
    echo "    usermod -aG <group> $SERVICE_USER" >&2
    echo "  and replace __SEAT_GROUP__ in $SERVICE_FILE with <group> by hand," >&2
    echo "  or add <group> to the candidate list in this script and re-run." >&2
    exit 1
fi
echo "Using seat group '$SEAT_GROUP' …"

# vcgencmd needs /dev/vcio_gencmd, which udev grants to group 'video'.
# Without this the System page's throttling tiles stay empty.
echo "Adding '$SERVICE_USER' to groups 'video' and '$SEAT_GROUP' …"
usermod -aG video "$SERVICE_USER"
usermod -aG "$SEAT_GROUP" "$SERVICE_USER"

echo "Configuring a ${ROTATE}° output transform for $OUTPUT …"
# This has to live where the unit's HOME/XDG_CONFIG_HOME point (see
# weather-console.service): labwc looks its autostart file up relative to
# $XDG_CONFIG_HOME/labwc, falling back to $HOME/.config/labwc. davis has no
# home directory (deploy.sh creates it with --no-create-home), so this uses
# the same StateDirectory the unit does instead of /home/davis.
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CONSOLE_STATE_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$CONSOLE_STATE_DIR/.config/labwc"
cat > "$CONSOLE_STATE_DIR/.config/labwc/autostart" <<EOF
wlr-randr --output $OUTPUT --transform $ROTATE &
EOF
chown "$SERVICE_USER:$SERVICE_USER" "$CONSOLE_STATE_DIR/.config/labwc/autostart"
chmod +x "$CONSOLE_STATE_DIR/.config/labwc/autostart"

echo "Installing systemd service …"
# Anchored to the specific directive lines, not a blind whole-file replace:
# $SERVICE_FILE also *explains* the __SEAT_GROUP__ placeholder in a comment
# above it, and an unanchored substitution corrupts that comment too.
sed -e "s|^SupplementaryGroups=video __SEAT_GROUP__\$|SupplementaryGroups=video $SEAT_GROUP|" \
    -e "s|?lang=en'\$|?lang=$LANG_CODE'|" \
    "$SERVICE_FILE" > "/etc/systemd/system/$SERVICE_FILE"
systemctl daemon-reload
systemctl enable "$SERVICE_FILE"
systemctl restart "$SERVICE_FILE"

echo ""
echo "Console kiosk installed."
echo "  systemctl status $SERVICE_FILE"
echo "  journalctl -u $SERVICE_FILE -f"
echo ""
echo "The service user was added to groups 'video' and '$SEAT_GROUP'; the"
echo "restart above already picks that up — systemd resolves group"
echo "membership fresh each time it starts the service, unlike an"
echo "interactive login session."
