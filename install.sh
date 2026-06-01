#!/usr/bin/env bash
# Generate systemd user units that run artwall from this checkout, and enable
# the timer. Nothing is copied or installed elsewhere — the units point back
# here, so keep this directory in place.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
# Resolve the concrete interpreter (sys.executable), not a PATH/mise shim that
# may not exist in the systemd user environment.
python_bin="$(python3 -c 'import sys; print(sys.executable)')"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "==> Writing systemd user units into $units"
echo "    repo:   $here"
echo "    python: $python_bin"
mkdir -p "$units"
sed -e "s|__REPO__|$here|g" -e "s|__PYTHON__|$python_bin|g" \
    "$here/systemd/artwall.service" > "$units/artwall.service"
cp "$here/systemd/artwall.timer" "$units/"

echo "==> Enabling the timer"
systemctl --user daemon-reload
systemctl --user enable --now artwall.timer

cat <<'EOF'

Done.

artwall runs as a systemd *user* service, so it needs WAYLAND_DISPLAY and
SWAYSOCK in the user manager's environment. Add this to your Sway config
(~/.config/sway/config) if it isn't there already:

    exec systemctl --user import-environment WAYLAND_DISPLAY SWAYSOCK

Trigger a wallpaper change now with:

    systemctl --user start artwall.service

Check the schedule with:

    systemctl --user list-timers artwall.timer
EOF
