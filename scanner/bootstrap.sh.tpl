#!/usr/bin/env bash
set -euo pipefail
exec > >(tee -a /var/log/ai-scanner-bootstrap.log) 2>&1

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg unzip python3
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
source /etc/os-release
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

if ! command -v aws >/dev/null; then
  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install
  rm -rf /tmp/aws /tmp/awscliv2.zip
fi

install -d -m 0700 /opt/ai-scanner
aws s3 cp "s3://${bucket}/assets/run-scan.py" /opt/ai-scanner/run_scan.py --region "${region}"
echo '${script_sha256}  /opt/ai-scanner/run_scan.py' | sha256sum -c -
aws s3 cp "s3://${bucket}/assets/gmp-scan.py" /opt/ai-scanner/gmp_scan.py --region "${region}"
echo '${gmp_script_sha256}  /opt/ai-scanner/gmp_scan.py' | sha256sum -c -
aws s3 cp "s3://${bucket}/assets/assess-aws.py" /opt/ai-scanner/assess_aws.py --region "${region}"
echo '${assessment_sha256}  /opt/ai-scanner/assess_aws.py' | sha256sum -c -
aws s3 cp "s3://${bucket}/assets/assessment.json" /opt/ai-scanner/assessment.json --region "${region}"
echo '${assessment_config_sha256}  /opt/ai-scanner/assessment.json' | sha256sum -c -
chmod 0700 /opt/ai-scanner/run_scan.py
aws s3 cp "s3://${bucket}/assets/managed-images.py" /opt/ai-scanner/managed_images.py --region "${region}"
echo '${managed_images_sha256}  /opt/ai-scanner/managed_images.py' | sha256sum -c -
aws s3 cp "s3://${bucket}/assets/image-builds.zip" /opt/ai-scanner/image-builds.zip --region "${region}"
echo '${image_builds_sha256}  /opt/ai-scanner/image-builds.zip' | sha256sum -c -
install -d -m 0700 /opt/ai-scanner/image_builds
unzip -q /opt/ai-scanner/image-builds.zip -d /opt/ai-scanner/image_builds
rm /opt/ai-scanner/image-builds.zip

# The settings file contains resource names only. Secrets are fetched by the instance role
# during the scan and never embedded in user data or Terraform state.
cat >/etc/ai-scanner.env <<'EOF'
AWS_REGION='${region}'
REPORT_BUCKET='${bucket}'
PUBLIC_HOSTNAME='${hostname}'
CONTROL_PANEL_HOSTNAME='${control_panel_hostname}'
LAB_PRIVATE_IP='${lab_ip}'
IMAGE_DIGEST='${image_digest}'
SSH_SECRET_ARN='${ssh_secret_arn}'
GREENBONE_SECRET_ARN='${greenbone_secret_arn}'
SCAN_TIMEOUT_HOURS='${timeout_hours}'
EOF
chmod 0600 /etc/ai-scanner.env
touch /opt/ai-scanner/ready
