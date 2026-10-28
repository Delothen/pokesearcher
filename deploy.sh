#!/usr/bin/env bash
# Runs on the VM after every push, from the freshly synced copy of the repo.
# It runs as your own user and uses sudo for the steps that need root.
#
#   /opt/project_1/app      code (owned by root)
#   /opt/project_1/venv     virtualenv (owned by root)
#   /var/lib/project_1      database and static files (owned by the service user)
#   /etc/project_1/env      secret key (readable by root only)
set -euo pipefail

SERVICE=project_1
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="/opt/$SERVICE"

if grep -q '^Environment=.*CHANGE-ME' "$SRC_DIR/deploy/$SERVICE.service"; then
    echo "ERROR: the site URL has not been set." >&2
    echo "Replace CHANGE-ME in deploy/$SERVICE.service with your site's hostname," >&2
    echo "then commit and push the change." >&2
    exit 1
fi

# The unprivileged account the app runs as.
if ! id -u "$SERVICE" >/dev/null 2>&1; then
    sudo useradd --system --user-group --no-create-home \
        --home-dir "/var/lib/$SERVICE" --shell /usr/sbin/nologin "$SERVICE"
fi

sudo mkdir -p "$PREFIX/app"
sudo rsync -a --delete --chown=root:root --chmod=a+rX,go-w "$SRC_DIR/" "$PREFIX/app/"

if ! sudo test -x "$PREFIX/venv/bin/python"; then
    python3 -c 'import ensurepip' 2>/dev/null || sudo apt-get install -y python3-venv
    sudo python3 -m venv "$PREFIX/venv"
fi
sudo "$PREFIX/venv/bin/pip" install --quiet --disable-pip-version-check -r "$PREFIX/app/requirements.txt"

# Generated once on the VM; never leaves it.
if ! sudo test -s "/etc/$SERVICE/env"; then
    sudo mkdir -p "/etc/$SERVICE"
    sudo sh -c "umask 077 && python3 -c 'import secrets; print(\"SECRET_KEY=\" + secrets.token_urlsafe(50))' > /etc/$SERVICE/env"
fi

# Migrations and collectstatic run as part of the service start (see the unit).
sudo install -m 644 "$PREFIX/app/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
sudo systemctl daemon-reload
sudo systemctl enable --quiet "$SERVICE"
if ! sudo systemctl restart "$SERVICE" || ! { sleep 2; systemctl is-active --quiet "$SERVICE"; }; then
    echo "ERROR: $SERVICE failed to start. Recent log:" >&2
    sudo journalctl -u "$SERVICE" -n 40 --no-pager >&2
    exit 1
fi

echo "Deployed: $SERVICE is running."
