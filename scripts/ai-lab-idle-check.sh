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
#   4. powers the instance off (EC2 stops it) after IDLE_MINUTES of idleness, when
#      idle shutdown is on (IDLE_MINUTES above 0);
#   5. powers it off MAX_UPTIME_MINUTES after boot even if people are active, when a
#      hard time limit is set (MAX_UPTIME_MINUTES above 0). The warning email comes
#      from the watchdog. The limit counts from the later of boot and the last timer
#      reset, which the control panel's reset button writes (epoch seconds) to the SSM
#      parameter "$AUTO_STOP_PARAMETER/reset-at". A reset from an earlier run is older
#      than this boot, so it is ignored. If that parameter cannot be read, the hard
#      limit is skipped for that minute; the watchdog enforces it as the backstop.
#
# Set AI_LAB_DRY_RUN=1 to print what it sees without publishing or powering off.
# The AI_LAB_* path overrides below exist for the offline tests.
set -uo pipefail

ENV_FILE=${AI_LAB_AUTO_STOP_ENV:-/etc/ai-lab/auto-stop.env}
READY_FILE=${AI_LAB_READY_FILE:-/var/lib/ai-lab/ready}
STATE_DIR=${AI_LAB_STATE_DIR:-/run/ai-lab} # tmpfs: cleared on every boot, so a restart gets a fresh idle window
UPTIME_FILE=${AI_LAB_UPTIME_FILE:-/proc/uptime}
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
MAX_UPTIME_MINUTES=0 # unreadable settings never apply a hard limit
imds_token=$(curl -s -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)
imds() { curl -s -m 3 -H "X-aws-ec2-metadata-token: $imds_token" "http://169.254.169.254/latest/meta-data/$1"; }
instance_id=$(imds instance-id || true)
region=$(imds placement/region || true)
if [[ -n $region ]] && config=$(aws ssm get-parameter --region "$region" --name "$AUTO_STOP_PARAMETER" --query Parameter.Value --output text 2>/dev/null); then
  enabled=$(jq -r '.enabled' <<<"$config" 2>/dev/null || echo true)
  configured_idle=$(jq -r '.idle_minutes' <<<"$config" 2>/dev/null || true)
  [[ $configured_idle =~ ^[0-9]+$ ]] && IDLE_MINUTES=$configured_idle
  configured_max=$(jq -r '.max_uptime_minutes' <<<"$config" 2>/dev/null || true)
  [[ $configured_max =~ ^[0-9]+$ ]] && MAX_UPTIME_MINUTES=$configured_max
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
uptime_seconds=$(awk '{print int($1)}' "$UPTIME_FILE")
uptime_minutes=$((uptime_seconds / 60))

# Hard limit clock: the later of boot and the last timer reset. Only a plain decimal of at
# most 10 digits counts, and a reset more than RESET_SKEW_SECONDS in the future is ignored
# (the clock then runs from boot), so a bad or hand-written value can never switch the hard
# limit off. A slightly future value (clock skew) is treated as now.
RESET_SKEW_SECONDS=300
hard_minutes=$uptime_minutes
hard_limit_known=true
if ((MAX_UPTIME_MINUTES > 0)); then
  reset_at=0
  if [[ -n $region ]]; then
    if reset_raw=$(aws ssm get-parameter --region "$region" --name "$AUTO_STOP_PARAMETER/reset-at" --query Parameter.Value --output text 2>"$STATE_DIR/reset-error"); then
      if [[ $reset_raw =~ ^[0-9]{1,10}$ ]] && ((10#$reset_raw <= now + RESET_SKEW_SECONDS)); then
        reset_at=$((10#$reset_raw))
      elif [[ $reset_raw != 0 ]]; then
        logger -t ai-lab-idle "ignoring an invalid or future timer reset value; counting the hard limit from boot"
      fi
    elif ! grep -q ParameterNotFound "$STATE_DIR/reset-error" 2>/dev/null; then
      hard_limit_known=false
    fi
  else
    hard_limit_known=false
  fi
  ((reset_at > now)) && reset_at=$now
  boot_epoch=$((now - uptime_seconds))
  hard_start=$boot_epoch
  ((reset_at > hard_start)) && hard_start=$reset_at
  hard_minutes=$(((now - hard_start) / 60))
fi

echo "active_users=$active_users busy_connections=$busy_connections idle_minutes=$idle_minutes/$IDLE_MINUTES uptime_minutes=$uptime_minutes hard_limit_minutes=$hard_minutes/$MAX_UPTIME_MINUTES"

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

# Hard time limit: stop at the maximum even if people are active.
if ((MAX_UPTIME_MINUTES > 0)) && [[ $hard_limit_known == false ]]; then
  logger -t ai-lab-idle "could not read the timer reset; skipping the hard limit this minute (the watchdog backs it up)"
elif ((MAX_UPTIME_MINUTES > 0 && hard_minutes >= MAX_UPTIME_MINUTES)); then
  logger -t ai-lab-idle "hard limit reached: $hard_minutes minutes since boot or the last timer reset (maximum $MAX_UPTIME_MINUTES); powering off"
  systemctl poweroff
  exit 0
fi

if ((IDLE_MINUTES > 0 && idle_minutes >= IDLE_MINUTES)); then
  logger -t ai-lab-idle "idle for $idle_minutes minutes (limit $IDLE_MINUTES); powering off"
  systemctl poweroff
fi
