#!/usr/bin/env bash
# Adds (or with --remove, deletes) the "Lab Logs" tool-server connection in Open WebUI, pointing at
# the local MCP server from /etc/ai-lab/logs-mcp.json. Every other connection is left alone; safe
# to repeat, and it writes only when something differs. It signs in with the lab's admin account
# (settings in /etc/ai-lab/vuln-mcp.env, which the lab always writes); the password is read from
# Secrets Manager at run time and never printed or put on a command line.
#   sudo ai-lab-register-logs-mcp [--remove]
# Exit: 0 done, 2 failed after retries, 3 cannot automate (local password login is off).
set -uo pipefail

LAB_ENV="${AI_LAB_VULN_MCP_ENV:-/etc/ai-lab/vuln-mcp.env}"
CONFIG="${AI_LAB_LOGS_MCP_CONFIG:-/etc/ai-lab/logs-mcp.json}"
RETRIES="${AI_LAB_RETRIES:-6}"
RETRY_DELAY="${AI_LAB_RETRY_DELAY:-10}"
READY_ATTEMPTS="${AI_LAB_READY_ATTEMPTS:-60}"
READY_DELAY="${AI_LAB_READY_DELAY:-5}"
SERVER_ID="lab-logs"
REMOVE=false
log() { echo "[register-logs-mcp] $*"; }

case "${1:-}" in
    "") ;;
    --remove) REMOVE=true ;;
    *) log "Usage: ai-lab-register-logs-mcp [--remove]"; exit 2 ;;
esac
[[ -r "$LAB_ENV" ]] || { log "Cannot read $LAB_ENV"; exit 2; }
# shellcheck disable=SC1090
. "$LAB_ENV" # uses ADMIN_ARN ADMIN_EMAIL LOCAL_LOGIN PORT REGION
MCP_PORT="$(jq -r '.port // 8766' "$CONFIG" 2>/dev/null)" || MCP_PORT=8766
URL="http://127.0.0.1:${MCP_PORT}/mcp"

umask 077
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
token=""
status=""
stdin_body=""

# api METHOD PATH [BODY_FILE | -]: body in $tmp/out, HTTP status in $status (000 = no answer).
# The session token reaches curl on stdin, so it is not in the process list; "-" sends $stdin_body.
api() {
    local args=(-sS --max-time 60 -o "$tmp/out" -w '%{http_code}' -X "$1" -H 'Content-Type: application/json')
    if [[ "${3:-}" == - ]]; then
        args+=(--data-binary @-)
        status="$(printf '%s' "$stdin_body" | curl "${args[@]}" "http://127.0.0.1:$PORT$2" 2>/dev/null)" || status=000
        return
    fi
    [[ -n "${3:-}" ]] && args+=(--data-binary "@$3")
    if [[ -n "$token" ]]; then
        status="$(printf 'header = "Authorization: Bearer %s"\n' "$token" | curl "${args[@]}" -K - "http://127.0.0.1:$PORT$2" 2>/dev/null)" || status=000
    else
        status="$(curl "${args[@]}" "http://127.0.0.1:$PORT$2" 2>/dev/null)" || status=000
    fi
}

# secret ARN: the value of a plain-text secret, or the one value of a key/value secret.
secret() {
    local raw
    raw="$(aws secretsmanager get-secret-value --secret-id "$1" --query SecretString --output text --region "$REGION")" || return 1
    if jq -e 'type == "object"' <<<"$raw" >/dev/null 2>&1; then
        jq -er 'if length == 1 and (.[] | type) == "string" then .[] else error("x") end' <<<"$raw"
    else
        [[ -n "$raw" && "$raw" != None ]] && printf '%s' "$raw"
    fi
}

# One try. Returns 0 done, 1 retry, 2 fatal, 3 cannot automate.
attempt() {
    local n pw changed
    token=""
    for n in $(seq 1 "$READY_ATTEMPTS"); do
        api GET /health
        [[ "$status" == 200 ]] && break
        [[ "$n" -lt "$READY_ATTEMPTS" ]] && sleep "$READY_DELAY"
    done
    [[ "$status" == 200 ]] || { log "Open WebUI did not become ready"; return 1; }
    if [[ "$LOCAL_LOGIN" != true ]]; then
        log "Local login is off, so there is no admin API session; nothing was changed. Add $URL in Admin Settings > External Tools."
        return 3
    fi
    pw="$(secret "$ADMIN_ARN")" || { log "Could not read the admin password secret"; return 1; }
    stdin_body="$(PW="$pw" jq -nc --arg e "$ADMIN_EMAIL" '{email: $e, password: env.PW}')" || return 2
    api POST /api/v1/auths/signin -
    stdin_body=""
    case "$status" in
        200) ;;
        403) log "Open WebUI refused password sign-in (local login is disabled)."; return 3 ;;
        000 | 5??) log "Sign-in not possible yet (HTTP $status)"; return 1 ;;
        *) log "Open WebUI rejected the admin sign-in (HTTP $status)."; return 2 ;;
    esac
    token="$(jq -r '.token // empty' "$tmp/out")"
    [[ -n "$token" ]] || { log "Sign-in returned no token."; return 2; }

    api GET /api/v1/configs/tool_servers
    if [[ "$status" != 200 ]] || ! jq -ce '.TOOL_SERVER_CONNECTIONS | arrays' "$tmp/out" >"$tmp/old"; then
        log "Could not read the tool servers (HTTP $status)"
        return 1
    fi

    echo null >"$tmp/new"
    if ! $REMOVE; then
        jq -nc --arg url "$URL" --arg id "$SERVER_ID" '{
            url: $url, path: "", type: "mcp", auth_type: "none", key: "", headers: null, forward_cookies: false,
            config: {enable: true, function_name_filter_list: "",
                     access_grants: [{principal_type: "user", principal_id: "*", permission: "read"}]},
            info: {id: $id, name: "Lab Logs",
                   description: "Read-only search of the lab logs in VictoriaLogs (host journal and Open WebUI)."}}' >"$tmp/new"
        # Open WebUI connects and lists the tools, as the Verify button does.
        api POST /api/v1/configs/tool_servers/verify "$tmp/new"
        if [[ "$status" != 200 ]] || ! jq -e '.status == true' "$tmp/out" >/dev/null 2>&1; then
            log "Open WebUI could not connect to $URL and list its tools (HTTP $status)"
            return 1
        fi
        log "Open WebUI listed $(jq '.specs | length' "$tmp/out") tools: $(jq -r '[.specs[].name] | sort | join(", ")' "$tmp/out")"
    fi

    # Ours: id "lab-logs", or any MCP connection to the same URL (replaced in place, never duplicated).
    local mine='def mine: (.info.id? == $id) or (.type? == "mcp" and .url? == $url);'
    jq -c --slurpfile d "$tmp/new" --arg url "$URL" --arg id "$SERVER_ID" "$mine"'
        . as $all | [.[] | select(mine | not)] as $kept
        | (($all | map(mine) | index(true)) // ($kept | length)) as $at
        | if $d[0] == null then $kept else $kept[:$at] + [$d[0]] + $kept[$at:] end' "$tmp/old" >"$tmp/upd" || return 2
    changed=true
    [[ "$(jq -cS . "$tmp/old")" == "$(jq -cS . "$tmp/upd")" ]] && changed=false
    $changed || { log "Connection already correct; nothing to change."; return 0; }
    jq -c '{TOOL_SERVER_CONNECTIONS: .}' "$tmp/upd" >"$tmp/body"
    api POST /api/v1/configs/tool_servers "$tmp/body"
    [[ "$status" == 200 ]] || { log "Could not save the tool servers (HTTP $status)"; return 1; }
    log "Connection $($REMOVE && echo removed || echo "registered: $URL")."
}

for n in $(seq 1 "$RETRIES"); do
    attempt
    rc=$?
    [[ "$rc" -ne 1 ]] && exit "$rc"
    log "Attempt $n/$RETRIES failed"
    [[ "$n" -lt "$RETRIES" ]] && sleep "$RETRY_DELAY"
done
log "Gave up. Run 'sudo ai-lab-register-logs-mcp' again once the problem is fixed."
exit 2
