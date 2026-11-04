#!/usr/bin/env bash
set -euo pipefail

RUN_USER=""
STAGE=""
RELEASE_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) RUN_USER="${2:-}"; shift 2 ;;
    --stage) STAGE="${2:-}"; shift 2 ;;
    --release-id) RELEASE_ID="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "run through sudo from provision.ps1" >&2
  exit 1
fi
if [[ ! "$RUN_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "invalid runtime user" >&2
  exit 2
fi
if [[ ! "$STAGE" =~ ^/tmp/anastylos-mic-node-[a-f0-9]{32}$ || ! -d "$STAGE" || -L "$STAGE" ]]; then
  echo "refusing unexpected staging path: $STAGE" >&2
  exit 2
fi
if [[ ! "$RELEASE_ID" =~ ^[a-f0-9]{12}$ ]]; then
  echo "release id must be 12 lowercase hexadecimal characters" >&2
  exit 2
fi
id "$RUN_USER" >/dev/null 2>&1 || { echo "runtime user does not exist: $RUN_USER" >&2; exit 1; }
command -v apt-get >/dev/null || { echo "apt-get is required on Raspberry Pi OS" >&2; exit 1; }

ARCH="$(uname -m)"
case "$ARCH" in
  armv7l|aarch64) ;;
  *) echo "unsupported target architecture: $ARCH" >&2; exit 1 ;;
esac
if [[ ! -r /etc/os-release ]]; then
  echo "cannot identify target operating system" >&2
  exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != "raspbian" && "${ID:-}" != "debian" ]]; then
  echo "unsupported operating system: ${PRETTY_NAME:-unknown}" >&2
  exit 1
fi
if [[ -r /proc/device-tree/model ]] && ! tr '\000' ' ' </proc/device-tree/model | grep -q 'Raspberry Pi'; then
  echo "target is not identified as a Raspberry Pi" >&2
  exit 1
fi
printf 'Target: %s (%s)\n' "${PRETTY_NAME:-Debian}" "$ARCH"

for path in \
  "$STAGE/node/src/anastylos_mic_node/__init__.py" \
  "$STAGE/node/assets/anastylos_logo.png" \
  "$STAGE/src/anastylos_microscopy/capture.py" \
  "$STAGE/schemas/capture-manifest.v1.schema.json" \
  "$STAGE/deploy/anastylos-mic-node.service"; do
  [[ -f "$path" ]] || { echo "staged payload is incomplete: $path" >&2; exit 1; }
done

REQUIRED_PACKAGES=(
  python3-gi python3-gst-1.0 gstreamer1.0-tools
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  gstreamer1.0-plugins-bad gstreamer1.0-x v4l-utils
)
MISSING_PACKAGES=()
for package in "${REQUIRED_PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
    MISSING_PACKAGES+=("$package")
  fi
done
if (( ${#MISSING_PACKAGES[@]} > 0 )); then
  echo "Installing required Raspberry Pi OS packages: ${MISSING_PACKAGES[*]}"
  apt-get update
  apt-get install -y --no-upgrade "${MISSING_PACKAGES[@]}"
fi

INSTALL_BASE=/opt/anastylos-microscope
RELEASES="$INSTALL_BASE/releases"
CURRENT="$INSTALL_BASE/current"
RELEASE="$RELEASES/$RELEASE_ID"
SERVICE_NAME=anastylos-mic-node
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME.service"
if [[ -e "$CURRENT" && ! -L "$CURRENT" ]]; then
  echo "refusing to replace a non-symlink install path: $CURRENT" >&2
  exit 1
fi
if [[ -L "$RELEASE" ]]; then
  echo "refusing a symlinked release path: $RELEASE" >&2
  exit 1
fi
if [[ -L "$CURRENT" ]]; then
  OLD_TARGET="$(readlink -f "$CURRENT")"
  case "$OLD_TARGET" in
    "$RELEASES"/*) ;;
    *) echo "current install points outside managed releases; refusing update" >&2; exit 1 ;;
  esac
else
  OLD_TARGET=""
fi
if [[ -e "$SERVICE_PATH" && -z "$OLD_TARGET" ]]; then
  echo "service unit exists without a managed release; refusing to overwrite it" >&2
  exit 1
fi

install -d -m 0755 "$RELEASES"
if [[ ! -e "$RELEASE" ]]; then
  RELEASE_TEMP="$RELEASES/.staging-$RELEASE_ID-$$"
  install -d -m 0755 "$RELEASE_TEMP/node" "$RELEASE_TEMP/src" "$RELEASE_TEMP/schemas"
  cp -a "$STAGE/node/src" "$RELEASE_TEMP/node/"
  cp -a "$STAGE/node/assets" "$RELEASE_TEMP/node/"
  cp -a "$STAGE/src/anastylos_microscopy" "$RELEASE_TEMP/src/"
  cp -a "$STAGE/schemas/." "$RELEASE_TEMP/schemas/"
  chown -R root:root "$RELEASE_TEMP"
  find "$RELEASE_TEMP" -type d -exec chmod 0755 {} +
  find "$RELEASE_TEMP" -type f -exec chmod 0644 {} +
  mv "$RELEASE_TEMP" "$RELEASE"
fi

PYTHONPATH="$RELEASE/node/src:$RELEASE/src" /usr/bin/python3 - <<'PY'
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
import anastylos_mic_node
from anastylos_microscopy import CAPTURE_MANIFEST_SCHEMA
from anastylos_microscopy.schema import validate_named
Gst.init(None)
assert CAPTURE_MANIFEST_SCHEMA == "anastylos-microscopy-capture-manifest/1"
assert callable(validate_named)
print(f"Python/GStreamer import PASS; node={anastylos_mic_node.__version__}")
PY
for element in v4l2src tee appsink jpegdec videoconvert gdkpixbufoverlay textoverlay kmssink; do
  gst-inspect-1.0 "$element" >/dev/null || { echo "missing GStreamer element: $element" >&2; exit 1; }
done

SERVICE_TEMP="$(mktemp)"
sed -e "s/^User=anastylos$/User=$RUN_USER/" \
    -e "s/^Group=anastylos$/Group=$RUN_USER/" \
    "$STAGE/deploy/anastylos-mic-node.service" >"$SERVICE_TEMP"
chmod 0644 "$SERVICE_TEMP"
OLD_SERVICE_BACKUP=""
if [[ -f "$SERVICE_PATH" ]] && ! cmp -s "$SERVICE_TEMP" "$SERVICE_PATH"; then
  install -d -m 0755 /var/backups
  OLD_SERVICE_BACKUP="/var/backups/$SERVICE_NAME.service.$(date -u +%Y%m%dT%H%M%SZ).$$"
  cp -a "$SERVICE_PATH" "$OLD_SERVICE_BACKUP"
fi

WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE_NAME"; then
  WAS_ACTIVE=1
  systemctl stop "$SERVICE_NAME"
fi
PREVIOUS_SERVICE_EXISTS=0
[[ -f "$SERVICE_PATH" ]] && PREVIOUS_SERVICE_EXISTS=1
PREVIOUS_TARGET="$OLD_TARGET"

rollback() {
  local reason="$1"
  echo "Activation failed: $reason; restoring prior service target" >&2
  systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  if [[ -n "$PREVIOUS_TARGET" ]]; then
    local rollback_link="$INSTALL_BASE/.current-rollback-$$"
    ln -s "$PREVIOUS_TARGET" "$rollback_link"
    mv -Tf "$rollback_link" "$CURRENT"
  elif [[ -L "$CURRENT" ]]; then
    rm -f "$CURRENT"
  fi
  if [[ "$PREVIOUS_SERVICE_EXISTS" == 1 && -n "$OLD_SERVICE_BACKUP" ]]; then
    cp -a "$OLD_SERVICE_BACKUP" "$SERVICE_PATH"
  elif [[ "$PREVIOUS_SERVICE_EXISTS" == 0 ]]; then
    rm -f "$SERVICE_PATH"
  fi
  systemctl daemon-reload
  if [[ -n "$PREVIOUS_TARGET" || "$WAS_ACTIVE" == 1 ]]; then
    systemctl start "$SERVICE_NAME" || true
  else
    systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
  fi
  exit 1
}

ROLLBACK_ARMED=1
on_error() {
  local exit_code=$?
  if [[ "$ROLLBACK_ARMED" == 1 ]]; then
    trap - ERR
    rollback "command exited with status $exit_code"
  fi
  exit "$exit_code"
}
trap on_error ERR

NEW_LINK="$INSTALL_BASE/.current-new-$$"
ln -s "$RELEASE" "$NEW_LINK"
mv -Tf "$NEW_LINK" "$CURRENT"
install -m 0644 "$SERVICE_TEMP" "$SERVICE_PATH"
rm -f "$SERVICE_TEMP"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
if ! systemctl start "$SERVICE_NAME"; then
  rollback "systemd start command failed"
fi
sleep 2
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  journalctl -u "$SERVICE_NAME" -n 40 --no-pager >&2 || true
  rollback "service is not active"
fi
for _ in $(seq 1 10); do
  [[ -s /run/anastylos-mic-node/status.json ]] && break
  sleep 1
done
if [[ ! -s /run/anastylos-mic-node/status.json ]]; then
  rollback "service did not publish diagnostic status"
fi

WRAPPER_TEMP="$(mktemp)"
cat >"$WRAPPER_TEMP" <<'EOF'
#!/bin/sh
PYTHONPATH=/opt/anastylos-microscope/current/node/src:/opt/anastylos-microscope/current/src exec /usr/bin/python3 -m anastylos_mic_node "$@"
EOF
install -m 0755 "$WRAPPER_TEMP" /usr/local/bin/anastylos-mic-node
rm -f "$WRAPPER_TEMP"
ROLLBACK_ARMED=0
trap - ERR

if [[ -e "$STAGE" ]]; then
  rm -rf -- "$STAGE"
fi
echo "ANASTYLOS microscope service installed and active."
echo "Release: $RELEASE_ID"
echo "Status: sudo -u $RUN_USER /usr/local/bin/anastylos-mic-node status"
