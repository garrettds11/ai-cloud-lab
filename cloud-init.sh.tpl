#!/usr/bin/env bash

set -euxo pipefail

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

echo "=================================================="
echo "Starting PyGPT + Ollama AI lab installation"
echo "=================================================="

apt-get update
apt-get upgrade -y

apt-get install -y \
    curl \
    wget \
    git \
    jq \
    ca-certificates \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    xfce4 \
    xfce4-goodies \
    xrdp \
    xorgxrdp \
    dbus-x11 \
    libgl1 \
    libegl1 \
    libxcb-cursor0 \
    libxcb-xinerama0 \
    libxkbcommon-x11-0 \
    libpulse0 \
    libnss3 \
    libasound2-data \
    libasound2-plugins \
    portaudio19-dev \
    snapd

# Ubuntu 24.04 uses the time64 package name. Fall back for related Ubuntu images.
apt-get install -y libasound2t64 || apt-get install -y libasound2

# Ubuntu AWS images usually include the SSM agent. This makes sure it is active.
ensure_ssm_agent

# Configure Ubuntu desktop account.
echo "ubuntu:${desktop_password}" | chpasswd
passwd -u ubuntu || true
echo "startxfce4" > /home/ubuntu/.xsession
chown ubuntu:ubuntu /home/ubuntu/.xsession
chmod 600 /home/ubuntu/.xsession

# Configure XRDP.
usermod -aG ssl-cert xrdp
systemctl enable xrdp
systemctl restart xrdp

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

# Install PyGPT in a dedicated Python virtual environment.
sudo -H -u ubuntu python3 -m venv /home/ubuntu/pygpt-venv

sudo -H -u ubuntu /home/ubuntu/pygpt-venv/bin/python \
    -m pip install \
    --upgrade \
    pip \
    setuptools \
    wheel

sudo -H -u ubuntu /home/ubuntu/pygpt-venv/bin/pip \
    install \
    pygpt-net

# Create desktop launcher.
mkdir -p /home/ubuntu/Desktop

cat > /home/ubuntu/Desktop/PyGPT.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=PyGPT
Comment=Local AI Assistant
Exec=/home/ubuntu/pygpt-venv/bin/pygpt
Icon=utilities-terminal
Terminal=false
Categories=Development;
EOF

chmod +x /home/ubuntu/Desktop/PyGPT.desktop
chown -R ubuntu:ubuntu /home/ubuntu/Desktop
chown -R ubuntu:ubuntu /home/ubuntu/pygpt-venv

# Convenience command.
cat > /usr/local/bin/pygpt <<'EOF'
#!/bin/bash
exec /home/ubuntu/pygpt-venv/bin/pygpt "$@"
EOF

chmod +x /usr/local/bin/pygpt

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
echo "--- PyGPT ---"
if [ -x /home/ubuntu/pygpt-venv/bin/pygpt ]; then
    echo "PyGPT installed."
    /home/ubuntu/pygpt-venv/bin/pip show pygpt-net | sed -n '1,12p' || true
else
    echo "PyGPT NOT FOUND."
fi

echo
echo "--- XRDP ---"
systemctl is-active xrdp || true

echo
echo "--- SSM Agent ---"
systemctl is-active amazon-ssm-agent.service 2>/dev/null || \
systemctl is-active snap.amazon-ssm-agent.amazon-ssm-agent.service 2>/dev/null || true

echo
EOF

chmod +x /usr/local/bin/ai-lab-status

cat > /home/ubuntu/AI-LAB-README.txt <<EOF
PyGPT + Ollama EC2 Lab
======================

Ollama URL:
    http://127.0.0.1:11434

Installed model:
    ${ollama_model}

PyGPT executable:
    /home/ubuntu/pygpt-venv/bin/pygpt

To inspect the environment:
    ai-lab-status

To inspect Ollama models:
    ollama list

To manually test the model:
    ollama run ${ollama_model}

Ollama is intentionally listening only on localhost.

Configure PyGPT to use the Ollama provider and select:
    ${ollama_model}
EOF

chown ubuntu:ubuntu /home/ubuntu/AI-LAB-README.txt

echo "=================================================="
echo "AI lab installation complete"
echo "=================================================="
