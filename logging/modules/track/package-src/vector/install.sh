#!/bin/bash
# SSM Distributor runs this as "sh <script>", and sh is dash on Ubuntu; switch to bash first.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl
curl --proto '=https' --tlsv1.2 -sSfL https://setup.vector.dev | bash
apt-get update
apt-get install -y vector

token="$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
region="$(curl -fsS -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/placement/region)"
track="$(cat track.txt)"
aws ssm get-parameter --name "/logging-bakeoff/$track/vector-config" --query Parameter.Value --output text --region "$region" >/etc/vector/vector.toml
chown root:vector /etc/vector/vector.toml
chmod 0640 /etc/vector/vector.toml
usermod -aG docker,adm,systemd-journal vector

install -d -m 0755 /etc/systemd/system/vector.service.d
# The packaged unit validates /etc/vector/vector.yaml before each start, so point the check at our
# file too. Skip sink health checks there so a stopped backend does not keep Vector from starting.
cat >/etc/systemd/system/vector.service.d/10-logging-bakeoff.conf <<'EOF'
[Service]
ExecStartPre=
ExecStartPre=/usr/bin/vector validate --skip-healthchecks /etc/vector/vector.toml
ExecStart=
ExecStart=/usr/bin/vector --config /etc/vector/vector.toml
EOF
systemctl daemon-reload
systemctl enable --now vector
