#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg

token="$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
region="$(curl -fsS -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/placement/region)"
track="$(cat track.txt)"
manager="$(aws ssm get-parameter --name "/logging-bakeoff/$track/wazuh-manager-ip" --query Parameter.Value --output text --region "$region")"
agent_name="ai-cloud-lab-$(hostname -s)"

install -d -m 0755 /usr/share/keyrings
curl -fsSL https://packages.wazuh.com/key/GPG-KEY-WAZUH | gpg --dearmor --yes -o /usr/share/keyrings/wazuh.gpg
chmod 0644 /usr/share/keyrings/wazuh.gpg
echo 'deb [signed-by=/usr/share/keyrings/wazuh.gpg] https://packages.wazuh.com/4.x/apt/ stable main' >/etc/apt/sources.list.d/wazuh.list
apt-get update
WAZUH_MANAGER="$manager" WAZUH_AGENT_NAME="$agent_name" apt-get install -y wazuh-agent

python3 - <<'PY'
from pathlib import Path

path = Path("/var/ossec/etc/ossec.conf")
text = path.read_text()
if "<location>/var/log/syslog</location>" not in text:
    marker = "</ossec_config>"
    position = text.find(marker)
    if position < 0:
        raise SystemExit("Wazuh config has no ossec_config closing tag")
    localfiles = """  <localfile><log_format>syslog</log_format><location>/var/log/syslog</location></localfile>
  <localfile><log_format>syslog</log_format><location>/var/log/auth.log</location></localfile>
  <localfile><log_format>json</log_format><location>/var/lib/docker/containers/*/*-json.log</location></localfile>
"""
    text = text[:position] + localfiles + text[position:]
    path.write_text(text)
PY
# The backend can still be pulling and starting the Wazuh stack when this runs, so retry
# enrollment for up to 15 minutes instead of failing on the first refused connection.
enrolled=no
for attempt in $(seq 1 90); do
  if /var/ossec/bin/agent-auth -m "$manager" -A "$agent_name"; then
    enrolled=yes
    break
  fi
  echo "Wazuh manager not accepting enrollment yet (attempt $attempt of 90); retrying in 10 seconds"
  sleep 10
done
if [ "$enrolled" != yes ]; then
  echo "Wazuh manager at $manager did not accept enrollment within 15 minutes" >&2
  exit 1
fi
systemctl daemon-reload
systemctl enable --now wazuh-agent
