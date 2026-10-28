#!/usr/bin/env bash
# Run a manage.py command on the production server as the project_1 account,
# with the same settings as the running service, e.g.
#
#   /opt/project_1/app/deploy/manage.sh createsuperuser
set -euo pipefail

SERVICE=project_1

# The settings from the installed service file, plus the secret key.
read -ra settings <<< "$(systemctl show "$SERVICE" --property=Environment --value)"
settings+=("$(sudo cat "/etc/$SERVICE/env")")

cd "/opt/$SERVICE/app"
exec sudo -u "$SERVICE" env "${settings[@]}" "/opt/$SERVICE/venv/bin/python" manage.py "$@"
