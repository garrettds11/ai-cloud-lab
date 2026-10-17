#!/bin/bash
set -euo pipefail
systemctl disable --now wazuh-agent || true
apt-get purge -y wazuh-agent || true
rm -f /etc/apt/sources.list.d/wazuh.list /usr/share/keyrings/wazuh.gpg
