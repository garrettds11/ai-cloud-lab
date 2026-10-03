#!/usr/bin/env bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

LOG_FILE="/var/log/ai-lab-bootstrap.log"
exec > >(tee -a "$LOG_FILE") 2>&1
STATE_DIR="/var/lib/ai-lab"
READY_FILE="$STATE_DIR/ready"
FAILED_FILE="$STATE_DIR/failed"
mkdir -p "$STATE_DIR"
rm -f "$READY_FILE" "$FAILED_FILE"

mark_failed() {
    touch "$FAILED_FILE"
    echo "AI lab bootstrap failed. See $LOG_FILE."
}

read_secret_value() {
    local secret_string="$1"

    # Secrets created with the Secrets Manager key/value editor are returned as
    # a one-property JSON object. Plaintext SecretString values remain valid.
    if printf '%s' "$secret_string" | jq -e 'type == "object"' >/dev/null 2>&1; then
        printf '%s' "$secret_string" |
            jq -er 'if length == 1 and (.[] | type) == "string" then .[] else error("secret object must contain exactly one string value") end'
        return
    fi

    if printf '%s' "$secret_string" | jq -e 'type == "string"' >/dev/null 2>&1; then
        printf '%s' "$secret_string" | jq -er '.'
        return
    fi

    printf '%s' "$secret_string"
}

trap mark_failed ERR

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
        systemctl enable --now amazon-ssm-agent.service
        return 0
    fi

    if systemctl list-unit-files --type=service | grep -q '^snap.amazon-ssm-agent.amazon-ssm-agent.service'; then
        systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service
        return 0
    fi

    if ! command -v snap >/dev/null 2>&1; then
        apt-get install -y snapd
    fi

    snap install amazon-ssm-agent --classic
    systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service
}

configure_local_firewall() {
    cat > /usr/local/sbin/ai-lab-firewall <<'EOF'
#!/bin/bash
set -euo pipefail

# Defense in depth: the AWS security group keeps the app port reachable only
# from the ALB when domain access is enabled, or from no network source when
# using SSM-only access. Ollama remains loopback-only in both modes.
if [[ "${open_webui_domain_access_enabled}" == "true" ]]; then
    protected_ports="11434"
else
    protected_ports="${open_webui_host_port} 11434"
fi

for port in $protected_ports; do
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

provision_demo_users() {
    if [[ -z "${open_webui_demo_password_secret_arn}" ]]; then
        echo "No Open WebUI demo-user password supplied; skipping demo-user provisioning."
        return 0
    fi

    local demo_users_json
    local demo_password
    local admin_token

    demo_users_json="$(printf '%s' '${open_webui_demo_users_b64}' | base64 --decode)"
    demo_password="$(read_secret_value "$(aws secretsmanager get-secret-value \
        --secret-id '${open_webui_demo_password_secret_arn}' \
        --query SecretString \
        --output text \
        --region '${aws_region}')")"

    if [[ -z "$demo_password" || "$demo_password" == "None" ]]; then
        echo "Open WebUI demo-user password secret was empty."
        return 1
    fi

    admin_token="$(curl --silent --show-error --fail \
        -X POST "http://127.0.0.1:${open_webui_host_port}/api/v1/auths/signin" \
        -H 'Content-Type: application/json' \
        --data "$(jq -n \
            --arg email '${open_webui_admin_email}' \
            --arg password "$open_webui_admin_password" \
            '{email: $email, password: $password}')" | jq -r '.token')"

    if [[ -z "$admin_token" || "$admin_token" == "null" ]]; then
        echo "Open WebUI admin sign-in did not return a token."
        return 1
    fi

    while IFS= read -r user_json; do
        local email
        local response_file
        local http_status
        email="$(jq -r '.email' <<<"$user_json")"
        echo "Provisioning Open WebUI demo account: $email"

        response_file="$(mktemp)"
        http_status="$(jq -n \
            --arg name "$(jq -r '.name' <<<"$user_json")" \
            --arg email "$email" \
            --arg password "$demo_password" \
            '{name: $name, email: $email, password: $password, role: "user"}' |
            curl --silent --show-error \
                -X POST "http://127.0.0.1:${open_webui_host_port}/api/v1/auths/add" \
                -H "Authorization: Bearer $admin_token" \
                -H 'Content-Type: application/json' \
                --data-binary @- \
                --output "$response_file" \
                --write-out '%%{http_code}')"

        if [[ "$http_status" != 2* ]]; then
            echo "Open WebUI demo-user creation failed for $email (HTTP $http_status)."
            cat "$response_file"
            rm -f "$response_file"
            return 1
        fi

        rm -f "$response_file"
    done < <(jq -c '.[]' <<<"$demo_users_json")
}

echo "=================================================="
echo "Starting Open WebUI + Ollama AI lab installation"
echo "=================================================="

apt-get update

apt-get install -y \
    curl \
    wget \
    git \
    jq \
    ca-certificates \
    docker.io \
    iptables \
    snapd \
    unzip

# Ubuntu 24.04 does not provide the AWS CLI package in every enabled APT
# source. Install AWS CLI v2 from AWS so bootstrap can retrieve Secrets Manager
# values through the instance role.
if ! command -v aws >/dev/null 2>&1; then
    aws_cli_tmp_dir="$(mktemp -d)"
    curl --fail --silent --show-error --location \
        "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" \
        --output "$aws_cli_tmp_dir/awscliv2.zip"
    unzip -q "$aws_cli_tmp_dir/awscliv2.zip" -d "$aws_cli_tmp_dir"
    "$aws_cli_tmp_dir/aws/install" --update
    rm -rf "$aws_cli_tmp_dir"
fi

aws --version

# Ubuntu AWS images usually include the SSM agent. This makes sure it is active.
ensure_ssm_agent

if systemctl is-active --quiet amazon-ssm-agent.service || \
   systemctl is-active --quiet snap.amazon-ssm-agent.amazon-ssm-agent.service; then
    echo "SSM agent is active."
else
    echo "SSM agent is not active; bootstrap cannot continue."
    systemctl --no-pager --full status amazon-ssm-agent.service snap.amazon-ssm-agent.amazon-ssm-agent.service || true
    exit 1
fi

systemctl enable --now docker
configure_local_firewall

# Retrieve the password at boot through the instance role. The secret ARN is
# safe to include in user-data; the password itself is not.
open_webui_admin_password="$(read_secret_value "$(aws secretsmanager get-secret-value \
    --secret-id '${open_webui_admin_password_secret_arn}' \
    --query SecretString \
    --output text \
    --region '${aws_region}')")"

if [[ -z "$open_webui_admin_password" || "$open_webui_admin_password" == "None" ]]; then
    echo "Open WebUI admin password secret was empty."
    exit 1
fi

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
HOME=/root OLLAMA_HOST=http://127.0.0.1:11434 ollama pull "${ollama_model}"

# Optional Cognito single sign-on for Open WebUI. The app client secret is read
# from Cognito through the instance role, so it is never in user-data.
oauth_args=()
if [[ "${open_webui_oidc_enabled}" == "true" ]]; then
    cognito_client_secret="$(aws cognito-idp describe-user-pool-client \
        --user-pool-id '${cognito_user_pool_id}' \
        --client-id '${cognito_client_id}' \
        --query 'UserPoolClient.ClientSecret' \
        --output text \
        --region '${aws_region}')"

    if [[ -z "$cognito_client_secret" || "$cognito_client_secret" == "None" ]]; then
        echo "Could not read the Cognito app client secret."
        exit 1
    fi

    oauth_args=(
        -e OAUTH_CLIENT_ID='${cognito_client_id}'
        -e OAUTH_CLIENT_SECRET="$cognito_client_secret"
        -e OPENID_PROVIDER_URL='https://cognito-idp.${aws_region}.amazonaws.com/${cognito_user_pool_id}/.well-known/openid-configuration'
        -e OPENID_REDIRECT_URI='${open_webui_url}/oauth/oidc/callback'
        -e OAUTH_PROVIDER_NAME='Cognito'
        -e OAUTH_SCOPES='openid email profile'
        -e OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true
    )
fi

# Security notice banner shown in Open WebUI. Passed through base64 so quotes and
# apostrophes in the text cannot break the shell.
webui_banners="$(echo '${open_webui_banners_b64}' | base64 -d)"

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
    -e ENABLE_LOGIN_FORM="${open_webui_local_login_enabled}" \
    -e ENABLE_PASSWORD_AUTH="${open_webui_local_login_enabled}" \
    -e ENABLE_SIGNUP=false \
    -e ENABLE_OAUTH_SIGNUP="${open_webui_oidc_enabled}" \
    -e DEFAULT_USER_ROLE="${open_webui_default_user_role}" \
    -e WEBUI_BANNERS="$webui_banners" \
    -e ENABLE_OPENAI_API=false \
    -e WEBUI_ADMIN_EMAIL="${open_webui_admin_email}" \
    -e WEBUI_ADMIN_NAME="${open_webui_admin_name}" \
    -e WEBUI_ADMIN_PASSWORD="$open_webui_admin_password" \
    "$${oauth_args[@]}" \
    "${open_webui_container_image}"

wait_for_open_webui

# The demo users are local Open WebUI accounts only when Cognito is off. With
# Cognito on they exist only in Cognito and get an Open WebUI account the first
# time they sign in. Local provisioning uses the password API, so it also needs
# local password sign-in enabled.
if [[ "${open_webui_local_demo_users_enabled}" == "true" ]]; then
    provision_demo_users
else
    echo "Skipping local demo-user provisioning (Cognito users, or local sign-in disabled)."
fi

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
sudo docker ps --filter name=${open_webui_container_name} || true

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

Local demo accounts:
    demo1@example.local
    demo2@example.local
    demo3@example.local
    demo4@example.local

All demo accounts receive the temporary Terraform value supplied through
open_webui_demo_user_password_secret_arn. Each user should change it from Profile after
first login. Do not use these demo credentials outside this lab.

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
touch "$READY_FILE"
rm -f "$FAILED_FILE"
