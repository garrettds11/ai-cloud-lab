#!/bin/bash
# Installs the "Lab Logs" MCP server on the lab host and registers it in Open WebUI.
# Run by SSM Distributor (AWS-ConfigureAWSPackage) from the unpacked package folder.
set -euo pipefail

token="$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
region="$(curl -fsS -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/placement/region)"
track="$(cat track.txt)"

install -d -m 0755 /opt/ai-lab-logs-mcp /etc/ai-lab
install -m 0644 mcp_protocol.py vuln_tools.py log_tools.py victorialogs_tools.py server.py /opt/ai-lab-logs-mcp/
install -m 0755 register.sh /usr/local/sbin/ai-lab-register-logs-mcp

# {"backend_url": "https://<backend private IP>", "port": 8766}; no secret.
umask 022
aws ssm get-parameter --name "/logging-bakeoff/$track/mcp-config" --query Parameter.Value --output text \
    --region "$region" >/etc/ai-lab/logs-mcp.json.new
python3 -c 'import json, sys; c = json.load(open(sys.argv[1])); assert c["backend_url"].startswith("https://")' \
    /etc/ai-lab/logs-mcp.json.new
mv /etc/ai-lab/logs-mcp.json.new /etc/ai-lab/logs-mcp.json
chmod 0644 /etc/ai-lab/logs-mcp.json

cat >/etc/systemd/system/ai-lab-logs-mcp.service <<'EOF'
[Unit]
Description=Lab Logs MCP server (read-only VictoriaLogs search for Open WebUI)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
DynamicUser=yes
# -E -s: ignore Python environment variables and user site packages; the script's own folder
# (/opt/ai-lab-logs-mcp) is still searched first, which is where its modules are.
ExecStart=/usr/bin/python3 -E -s -B -u /opt/ai-lab-logs-mcp/server.py --config /etc/ai-lab/logs-mcp.json
WorkingDirectory=/opt/ai-lab-logs-mcp
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
MemoryMax=256M
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ai-lab-logs-mcp.service
systemctl restart ai-lab-logs-mcp.service

# Registration needs Open WebUI and its admin sign-in. If it cannot be automated (local login is
# off) the server still runs; add it by hand as logging/README.md describes.
rc=0
AI_LAB_RETRIES=3 /usr/local/sbin/ai-lab-register-logs-mcp || rc=$?
case "$rc" in
    0) ;;
    3) echo "Lab Logs MCP server is running, but it could not be registered automatically; see logging/README.md." ;;
    *) echo "Lab Logs MCP registration failed (exit $rc); run 'sudo ai-lab-register-logs-mcp' on the lab host to retry." ;;
esac
