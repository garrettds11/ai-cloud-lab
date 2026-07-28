#!/usr/bin/env bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

LOG_FILE="/var/log/ai-lab-bootstrap.log"
exec > >(tee -a "$LOG_FILE") 2>&1

wait_for_ollama() {
    for attempt in $(seq 1 60); do
        if curl --silent --fail http://127.0.0.1:11434/api/tags >/dev/null; then
            echo "Ollama is ready."
            return 0
        fi

        echo "Waiting for Ollama... attempt $attempt"
        sleep 2
    done

    echo "Ollama did not become ready in time."
    systemctl --no-pager --full status ollama || true
    journalctl -u ollama --no-pager -n 100 || true
    return 1
}

ensure_ssm_agent() {
    if systemctl list-unit-files --type=service | grep -q '^amazon-ssm-agent.service'; then
        systemctl enable --now amazon-ssm-agent.service || true
        return 0
    fi

    if systemctl list-unit-files --type=service | grep -q '^snap.amazon-ssm-agent.amazon-ssm-agent.service'; then
        systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || true
        return 0
    fi

    if ! command -v snap >/dev/null 2>&1; then
        apt-get install -y snapd
    fi

    snap install amazon-ssm-agent --classic || true
    systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || true
}

configure_local_firewall() {
    cat > /usr/local/sbin/ai-lab-firewall <<'EOF'
#!/bin/bash
set -euo pipefail

# Defense in depth: the AWS security group has no inbound ${open_webui_host_port} or 11434 rules,
# and these host rules keep both services reachable only from instance-local
# loopback clients such as SSM and SSH tunnels.
for port in ${open_webui_host_port} 11434; do
    iptables -C INPUT -p tcp --dport "$port" ! -i lo -j DROP 2>/dev/null || \
        iptables -I INPUT -p tcp --dport "$port" ! -i lo -j DROP
done
EOF

    chmod +x /usr/local/sbin/ai-lab-firewall

    cat > /etc/systemd/system/ai-lab-firewall.service <<'EOF'
[Unit]
Description=Restrict AI lab web and model ports to loopback
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ai-lab-firewall
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now ai-lab-firewall.service
}

wait_for_open_webui() {
    for attempt in $(seq 1 120); do
        if curl --silent --fail "http://127.0.0.1:${open_webui_host_port}" >/dev/null; then
            echo "Open WebUI is ready."
            return 0
        fi

        echo "Waiting for Open WebUI... attempt $attempt"
        sleep 5
    done

    echo "Open WebUI did not become ready in time."
    docker ps -a || true
    docker logs --tail 200 "${open_webui_container_name}" || true
    return 1
}

echo "=================================================="
echo "Starting Open WebUI + Ollama AI lab installation"
echo "=================================================="

apt-get update
apt-get upgrade -y

apt-get install -y \
    curl \
    wget \
    git \
    jq \
    ca-certificates \
    docker.io \
    iptables \
    snapd

# Ubuntu AWS images usually include the SSM agent. This makes sure it is active.
ensure_ssm_agent

systemctl enable --now docker
usermod -aG docker ubuntu || true
configure_local_firewall

# Install Ollama using the official Linux installer.
curl -fsSL https://ollama.com/install.sh | sh

# Keep Ollama local to the EC2 instance before starting/restarting the service.
mkdir -p /etc/systemd/system/ollama.service.d

cat > /etc/systemd/system/ollama.service.d/environment.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
EOF

systemctl daemon-reload
systemctl enable ollama
systemctl restart ollama
wait_for_ollama

# Pull requested model.
sudo -H -u ubuntu env OLLAMA_HOST=http://127.0.0.1:11434 ollama pull "${ollama_model}"

docker volume create "${open_webui_docker_volume}"
docker pull "${open_webui_container_image}"
docker rm -f "${open_webui_container_name}" 2>/dev/null || true

docker run -d \
    --name "${open_webui_container_name}" \
    --restart unless-stopped \
    --network host \
    -v "${open_webui_docker_volume}:/app/backend/data" \
    -e OLLAMA_BASE_URL="${open_webui_ollama_base_url}" \
    -e WEBUI_URL="${open_webui_url}" \
    -e WEBUI_AUTH=true \
    -e ENABLE_LOGIN_FORM=true \
    -e ENABLE_PASSWORD_AUTH=true \
    -e ENABLE_SIGNUP=false \
    -e ENABLE_OAUTH_SIGNUP=false \
    -e ENABLE_OPENAI_API=false \
    -e WEBUI_ADMIN_EMAIL="${open_webui_admin_email}" \
    -e WEBUI_ADMIN_NAME="${open_webui_admin_name}" \
    -e WEBUI_ADMIN_PASSWORD="${open_webui_admin_password}" \
    "${open_webui_container_image}"

wait_for_open_webui

# Convenience diagnostic script.
cat > /usr/local/bin/ai-lab-status <<'EOF'
#!/bin/bash

echo
echo "=== EC2 AI Lab ==="
echo

echo "--- Ollama service ---"
systemctl --no-pager --full status ollama | head -20 || true

echo
echo "--- Ollama API ---"
curl -s http://127.0.0.1:11434/api/tags | jq . || true

echo
echo "--- Installed models ---"
ollama list || true

echo
echo "--- Open WebUI container ---"
docker ps --filter name=${open_webui_container_name} || true

echo
echo "--- Open WebUI HTTP ---"
curl -I http://127.0.0.1:${open_webui_host_port} || true

echo
echo "--- SSM Agent ---"
systemctl is-active amazon-ssm-agent.service 2>/dev/null || \
systemctl is-active snap.amazon-ssm-agent.amazon-ssm-agent.service 2>/dev/null || true

echo
EOF

chmod +x /usr/local/bin/ai-lab-status

cat > /home/ubuntu/AI-LAB-README.txt <<EOF
Open WebUI + Ollama EC2 Lab
===========================

Ollama URL:
    http://127.0.0.1:11434

Open WebUI URL on this instance:
    http://127.0.0.1:${open_webui_host_port}

Access Open WebUI from your workstation through SSM port forwarding or an
optional SSH tunnel, then open:
    http://localhost:${open_webui_host_port}

Installed model:
    ${ollama_model}

To inspect the environment:
    ai-lab-status

To inspect Ollama models:
    ollama list

To manually test the model:
    ollama run ${ollama_model}

Ollama and Open WebUI are intentionally reachable only through private paths.
EOF

chown ubuntu:ubuntu /home/ubuntu/AI-LAB-README.txt

echo "=================================================="
echo "AI lab installation complete"
echo "=================================================="
