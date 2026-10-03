#!/usr/bin/env bash
# Stops this instance when nobody is using the lab.
#
# Installed by cloud-init and run every minute by ai-lab-idle-check.timer. It:
#   1. counts Open WebUI users active in the last 3 minutes (Open WebUI's own
#      definition, read from its database) and open connections to Ollama, which
#      means a reply is being generated;
#   2. keeps an idle clock that resets whenever either is non-zero, or whenever
#      activity cannot be determined (unknown is treated as active);
#   3. publishes ActiveUsers, IdleMinutes, UptimeMinutes and Heartbeat to
#      CloudWatch (namespace AILab) for the independent watchdog;
#   4. powers the instance off (EC2 stops it) after IDLE_MINUTES of idleness.
#
# Set AI_LAB_DRY_RUN=1 to print what it sees without publishing or powering off.
set -uo pipefail

ENV_FILE=/etc/ai-lab/auto-stop.env
READY_FILE=/var/lib/ai-lab/ready
STATE_DIR=/run/ai-lab # tmpfs: cleared on every boot, so a restart gets a fresh idle window
ACTIVE_WINDOW_SECONDS=180

# shellcheck disable=SC1090
source "$ENV_FILE" || exit 1 # OPEN_WEBUI_VOLUME, OLLAMA_PORT, AUTO_STOP_PARAMETER

# Wait for first-boot setup to finish; the idle clock starts when it has.
[[ -f $READY_FILE ]] || exit 0

mkdir -p "$STATE_DIR"
now=$(date +%s)
[[ -f $STATE_DIR/last-activity ]] || echo "$now" >"$STATE_DIR/last-activity"

# Settings live in an SSM parameter that Terraform manages, so changing them in
# terraform.tfvars takes effect on the next run without replacing the instance.
# If the parameter cannot be read, fall back to the safe defaults below.
enabled=true
IDLE_MINUTES=60
imds_token=$(curl -s -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)
imds() { curl -s -m 3 -H "X-aws-ec2-metadata-token: $imds_token" "http://169.254.169.254/latest/meta-data/$1"; }
instance_id=$(imds instance-id || true)
region=$(imds placement/region || true)
if [[ -n $region ]] && config=$(aws ssm get-parameter --region "$region" --name "$AUTO_STOP_PARAMETER" --query Parameter.Value --output text 2>/dev/null); then
  enabled=$(jq -r '.enabled' <<<"$config" 2>/dev/null || echo true)
  configured_idle=$(jq -r '.idle_minutes' <<<"$config" 2>/dev/null || true)
  [[ $configured_idle =~ ^[0-9]+$ ]] && IDLE_MINUTES=$configured_idle
fi
if [[ $enabled == "false" ]]; then
  echo "$now" >"$STATE_DIR/last-activity" # a later re-enable gets a full idle window
  exit 0
fi

# Active Open WebUI users: last_active_at (epoch seconds) within the window.
active_users=-1
volume_path=$(docker volume inspect -f '{{.Mountpoint}}' "$OPEN_WEBUI_VOLUME" 2>/dev/null || true)
db="$volume_path/webui.db"
if [[ -n $volume_path && -r $db ]]; then
  active_users=$(sqlite3 -readonly -cmd '.timeout 2000' "$db" \
    "SELECT COUNT(*) FROM user WHERE last_active_at >= CAST(strftime('%s','now') AS INTEGER) - $ACTIVE_WINDOW_SECONDS;" 2>/dev/null || true)
fi
[[ $active_users =~ ^[0-9]+$ ]] || active_users=-1

# Replies in flight: Open WebUI holds a connection to Ollama while one streams.
busy_connections=$(ss -Htn state established "( dport = :$OLLAMA_PORT )" 2>/dev/null | wc -l)

if ((active_users != 0 || busy_connections > 0)); then
  echo "$now" >"$STATE_DIR/last-activity"
fi

last_activity=$(<"$STATE_DIR/last-activity")
idle_minutes=$(((now - last_activity) / 60))
uptime_minutes=$(awk '{print int($1 / 60)}' /proc/uptime)

echo "active_users=$active_users busy_connections=$busy_connections idle_minutes=$idle_minutes/$IDLE_MINUTES uptime_minutes=$uptime_minutes"

if [[ -n ${AI_LAB_DRY_RUN:-} ]]; then
  exit 0
fi

# Publish metrics for the watchdog. Failures here must never block the shutdown.
if [[ -n $instance_id && -n $region ]]; then
  metrics=$(jq -n --arg id "$instance_id" \
    --argjson users "$active_users" --argjson idle "$idle_minutes" --argjson up "$uptime_minutes" \
    '[{MetricName:"Heartbeat",Value:1,Unit:"Count"},
      {MetricName:"ActiveUsers",Value:$users,Unit:"Count"},
      {MetricName:"IdleMinutes",Value:$idle,Unit:"None"},
      {MetricName:"UptimeMinutes",Value:$up,Unit:"None"}]
     | map(. + {Dimensions:[{Name:"InstanceId",Value:$id}]})')
  aws cloudwatch put-metric-data --region "$region" --namespace AILab --metric-data "$metrics" ||
    logger -t ai-lab-idle "could not publish metrics"
fi

if ((idle_minutes >= IDLE_MINUTES)); then
  logger -t ai-lab-idle "idle for $idle_minutes minutes (limit $IDLE_MINUTES); powering off"
  systemctl poweroff
fi
