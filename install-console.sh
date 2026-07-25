#!/usr/bin/env bash
# install-console.sh — optional kiosk display for the touch console.
#
# Run from the repository root on the machine with the touch panel attached:
#   sudo ./install-console.sh [--lang de] [--rotate 90] [--output DSI-1]
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
# Verified on the target hardware: 90 is correct for this panel and its
# mounting. The previous default (270) was carried over from the text
# console's fbcon rotation setting and turned out to be 180° off — the
# image appeared upside down.
ROTATE=90
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

# --rotate goes verbatim into a generated autostart shell script, and it also
# selects the libinput touch calibrationMatrix written below — labwc does not
# rotate touch input with the output transform (its own manual: "To rotate
# touch events with output rotation, use the libinput calibrationMatrix
# setting"), so the two must be derived from the same value or the image and
# the touch surface end up rotated against each other. Restricted to the four
# plain rotations wlr-randr and the calibration-matrix derivation below both
# understand — the flipped-* transforms have no corresponding matrix here.
case "$ROTATE" in
    0|90|180|270) ;;
    *)
        echo "ERROR: --rotate '$ROTATE' is not a valid rotation." >&2
        echo "  Valid values: 0 90 180 270" >&2
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

# The group that actually grants seat access is not a fixed name across
# distributions/packagings — on Debian 13 seatd runs as `seatd -g video` and
# there is no dedicated seat group at all (getent group has nothing matching
# "seat"). The authoritative answer is whichever group owns seatd's own
# socket once it is up, so read that rather than guessing from a name list.
# The socket does not appear instantly after `systemctl enable --now`, so
# wait for it briefly — bounded, so a seatd that never creates a socket
# doesn't hang this script forever.
SEAT_SOCKET=/run/seatd.sock
SEAT_GROUP=""
for _ in 1 2 3 4 5; do
    [[ -S "$SEAT_SOCKET" ]] && break
    sleep 1
done

if [[ -S "$SEAT_SOCKET" ]]; then
    SEAT_GROUP="$(stat -c '%G' "$SEAT_SOCKET")"
    echo "Found $SEAT_SOCKET, owned by group '$SEAT_GROUP' …"
else
    echo "WARNING: $SEAT_SOCKET did not appear within 5s; falling back to a candidate group name list." >&2
    for candidate in seat _seatd seatd video; do
        if getent group "$candidate" > /dev/null 2>&1; then
            SEAT_GROUP="$candidate"
            break
        fi
    done
fi

if [[ -z "$SEAT_GROUP" ]]; then
    echo "ERROR: could not find the group seatd uses for seat access." >&2
    echo "  Checked: $SEAT_SOCKET (never appeared), then candidate groups seat, _seatd, seatd, video — none exist on this system." >&2
    echo "  Find the real one with: getent group | grep -i seat" >&2
    echo "  then either add '$SERVICE_USER' to it yourself:" >&2
    echo "    usermod -aG <group> $SERVICE_USER" >&2
    echo "  and replace __SEAT_GROUP__ in $SERVICE_FILE with <group> by hand," >&2
    echo "  or add <group> to the candidate list in this script and re-run." >&2
    exit 1
fi
echo "Using seat group '$SEAT_GROUP' …"

# The render node (/dev/dri/renderD128) is a separate device from the
# card0/card1 nodes seat access covers above, and on a stock Debian install
# it belongs to a different group ('render'). Without membership in it, the
# compositor can get seat access but still fails to open the render node
# and falls back to (or fails at) software rendering. Exactly like
# SEAT_GROUP above, the group name is a distribution convention, not a
# guarantee, so read it from the node's actual group owner instead of
# hardcoding 'render'.
#
# Unlike the seat group, a render node is genuinely optional: a machine
# with no GPU, or one that exposes a differently-named device, still runs
# the kiosk fine under software rendering — so its absence is a warning,
# not the fatal abort SEAT_GROUP gets above.
RENDER_NODE=/dev/dri/renderD128
RENDER_GROUP=""
if [[ -e "$RENDER_NODE" ]]; then
    RENDER_GROUP="$(stat -c '%G' "$RENDER_NODE")"
    echo "Found $RENDER_NODE, owned by group '$RENDER_GROUP' …"
else
    echo "WARNING: $RENDER_NODE not found; skipping render-group setup." >&2
    echo "  The kiosk will still run, falling back to software rendering." >&2
fi

# vcgencmd needs /dev/vcio_gencmd, which udev grants to group 'video'.
# Without this the System page's throttling tiles stay empty. video, the
# seat group and the render group can all turn out to be the same group
# (e.g. Debian 13, where seatd's group *is* 'video') or pairwise distinct —
# collect the set of distinct group names first, then add each exactly
# once, so usermod isn't called redundantly and the generated unit below
# doesn't get a duplicated group name.
GROUPS_TO_ADD=(video)
group_in_list() {
    local needle="$1"
    shift
    local g
    for g in "$@"; do
        [[ "$g" == "$needle" ]] && return 0
    done
    return 1
}
group_in_list "$SEAT_GROUP" "${GROUPS_TO_ADD[@]}" || GROUPS_TO_ADD+=("$SEAT_GROUP")
if [[ -n "$RENDER_GROUP" ]] && ! group_in_list "$RENDER_GROUP" "${GROUPS_TO_ADD[@]}"; then
    GROUPS_TO_ADD+=("$RENDER_GROUP")
fi

echo "Adding '$SERVICE_USER' to group(s): ${GROUPS_TO_ADD[*]} …"
for group in "${GROUPS_TO_ADD[@]}"; do
    usermod -aG "$group" "$SERVICE_USER"
done

echo "Configuring a ${ROTATE}° output transform for $OUTPUT …"
# This has to live where the unit's HOME/XDG_CONFIG_HOME point (see
# weather-console.service): labwc looks its autostart file up relative to
# $XDG_CONFIG_HOME/labwc, falling back to $HOME/.config/labwc. davis has no
# home directory (deploy.sh creates it with --no-create-home), so this uses
# the same StateDirectory the unit does instead of /home/davis.
# install -d's -o/-g flags only guarantee ownership for the directories
# named explicitly as arguments here — GNU install does not propagate them
# to ancestor directories it has to create implicitly along the way (that
# is exactly how .config ended up root-owned on real hardware: it was an
# implicit ancestor of .config/labwc below, not a named argument). Naming
# .config explicitly closes that gap.
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$CONSOLE_STATE_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$CONSOLE_STATE_DIR/.config" "$CONSOLE_STATE_DIR/.config/labwc"
cat > "$CONSOLE_STATE_DIR/.config/labwc/autostart" <<EOF
wlr-randr --output $OUTPUT --transform $ROTATE &
EOF
chown "$SERVICE_USER:$SERVICE_USER" "$CONSOLE_STATE_DIR/.config/labwc/autostart"
chmod +x "$CONSOLE_STATE_DIR/.config/labwc/autostart"

# labwc does not rotate touch input along with the output transform above —
# its manual says so explicitly ("To rotate touch events with output
# rotation, use the libinput calibrationMatrix setting"). Without this, a
# rotated image and an unrotated touch surface disagree about where the
# screen's corners are: taps land on the wrong part of the display. The
# matrix is derived from $ROTATE rather than hardcoded, so the script stays
# correct if the panel is ever mounted a different way. Only the 90 case
# below has actually been verified on real hardware (reproduced the
# mismatch, fixed it by hand with this exact matrix, then confirmed the
# leftmost/rightmost page dots select the first/last page); 0/180/270 follow
# from the same rotation-matrix derivation and carry that reasoning, not
# independent verification.
case "$ROTATE" in
    0)   CALIBRATION_MATRIX="1 0 0 0 1 0" ;;
    90)  CALIBRATION_MATRIX="0 -1 1 1 0 0" ;;   # verified on real hardware
    180) CALIBRATION_MATRIX="-1 0 1 0 -1 1" ;;
    270) CALIBRATION_MATRIX="0 1 0 -1 0 1" ;;
esac
cat > "$CONSOLE_STATE_DIR/.config/labwc/rc.xml" <<EOF
<?xml version="1.0"?>
<labwc_config>
  <libinput>
    <device category="touch">
      <calibrationMatrix>$CALIBRATION_MATRIX</calibrationMatrix>
    </device>
  </libinput>
</labwc_config>
EOF
chown "$SERVICE_USER:$SERVICE_USER" "$CONSOLE_STATE_DIR/.config/labwc/rc.xml"

# Belt and braces: an installation from before this fix (or one interrupted
# partway through) can already have part of this tree owned by root. This
# script is meant to be idempotent, so a re-run must repair that, not just
# avoid making it worse on a fresh install — chown the whole tree
# unconditionally on every run.
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONSOLE_STATE_DIR"

echo "Installing systemd service …"
# GROUPS_TO_ADD above is already the deduplicated set (video, seat group,
# and render group where one was found) — reuse it verbatim rather than
# recomputing which groups the unit needs a second time.
SUPPLEMENTARY_GROUPS="${GROUPS_TO_ADD[*]}"

# Anchored to the specific directive lines, not a blind whole-file replace:
# $SERVICE_FILE also *explains* the __SEAT_GROUP__ placeholder in a comment
# above it, and an unanchored substitution corrupts that comment too.
sed -e "s|^SupplementaryGroups=video __SEAT_GROUP__\$|SupplementaryGroups=$SUPPLEMENTARY_GROUPS|" \
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
echo "The service user was added to group(s): ${GROUPS_TO_ADD[*]}; the"
echo "restart above already picks that up — systemd resolves group"
echo "membership fresh each time it starts the service, unlike an"
echo "interactive login session."
if [[ -z "$RENDER_GROUP" ]]; then
    echo ""
    echo "No render node was found, so no render-group membership was added;"
    echo "the kiosk will run under software rendering."
fi
