#!/bin/bash
# Removes the "Lab Logs" MCP server and its Open WebUI connection from the lab host.
set -euo pipefail
if [[ -x /usr/local/sbin/ai-lab-register-logs-mcp ]]; then
    /usr/local/sbin/ai-lab-register-logs-mcp --remove ||
        echo "Could not remove the Lab Logs connection from Open WebUI; delete it in Admin Settings > External Tools."
fi
systemctl disable --now ai-lab-logs-mcp.service || true
rm -f /etc/systemd/system/ai-lab-logs-mcp.service
systemctl daemon-reload
rm -rf /opt/ai-lab-logs-mcp
rm -f /etc/ai-lab/logs-mcp.json /usr/local/sbin/ai-lab-register-logs-mcp
