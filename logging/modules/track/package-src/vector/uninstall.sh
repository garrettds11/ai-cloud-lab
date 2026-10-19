#!/bin/bash
# SSM Distributor runs this as "sh <script>", and sh is dash on Ubuntu; switch to bash first.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
systemctl disable --now vector || true
apt-get purge -y vector || true
rm -f /etc/systemd/system/vector.service.d/10-logging-bakeoff.conf
systemctl daemon-reload
rm -rf /etc/vector /var/lib/vector
