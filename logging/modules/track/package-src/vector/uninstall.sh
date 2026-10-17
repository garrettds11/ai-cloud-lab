#!/bin/bash
set -euo pipefail
systemctl disable --now vector || true
apt-get purge -y vector || true
rm -f /etc/systemd/system/vector.service.d/10-logging-bakeoff.conf
systemctl daemon-reload
rm -rf /etc/vector /var/lib/vector
