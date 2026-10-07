#!/usr/bin/env bash
# Makes Open WebUI's tool-server list match /etc/ai-lab/vuln-mcp.env: one connection with the id
# "vuln-findings" when the vulnerability MCP is on, none when it is off, every other setting left
# alone. Safe to repeat; writes only when something differs. Secrets are read from Secrets Manager
# at run time and never printed or put on a command line. Details: lambda/vuln_mcp/README.md.
#   sudo ai-lab-register-vuln-mcp [--check]
# Exit: 0 done, 2 failed after retries, 3 cannot automate (local password login is off).
set -uo pipefail

ENV_FILE="${AI_LAB_VULN_MCP_ENV:-/etc/ai-lab/vuln-mcp.env}"
RETRIES="${AI_LAB_RETRIES:-6}"
RETRY_DELAY="${AI_LAB_RETRY_DELAY:-10}"
READY_ATTEMPTS="${AI_LAB_READY_ATTEMPTS:-120}"
READY_DELAY="${AI_LAB_READY_DELAY:-5}"
CHECK=false
log() { echo "[register-vuln-mcp] $*"; }

case "${1:-}" in
    "") ;;
    --check) CHECK=true ;;
    *) log "Usage: ai-lab-register-vuln-mcp [--check]"; exit 2 ;;
esac
[[ -r "$ENV_FILE" ]] || { log "Cannot read $ENV_FILE"; exit 2; }
# shellcheck disable=SC1090
. "$ENV_FILE" # ENABLED URL TOKEN_ARN ADMIN_ARN ADMIN_EMAIL LOCAL_LOGIN PORT REGION
if [[ "$ENABLED" == true && ( -z "$URL" || -z "$TOKEN_ARN" ) ]]; then
    log "Enabled, but the URL or token secret ARN is missing from $ENV_FILE."
    exit 2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
token=""
status=""

# api METHOD PATH [BODY_FILE]: body in $tmp/out, HTTP status in $status (000 = no answer).
# The session token reaches curl on stdin, so it is not in the process list.
api() {
    local args=(-sS --max-time 60 -o "$tmp/out" -w '%{http_code}' -X "$1" -H 'Content-Type: application/json')
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
    elif jq -e 'type == "string"' <<<"$raw" >/dev/null 2>&1; then
        jq -er . <<<"$raw"
    else
        [[ -n "$raw" && "$raw" != None ]] && printf '%s' "$raw"
    fi
}

# One try. Returns 0 done, 1 retry, 2 fatal, 3 cannot automate.
attempt() {
    local n pw tok changed want
    token=""
    for n in $(seq 1 "$READY_ATTEMPTS"); do
        api GET /health
        [[ "$status" == 200 ]] && break
        log "Waiting for Open WebUI... attempt $n/$READY_ATTEMPTS"
        [[ "$n" -lt "$READY_ATTEMPTS" ]] && sleep "$READY_DELAY"
    done
    [[ "$status" == 200 ]] || { log "Open WebUI did not become ready"; return 1; }

    if [[ "$LOCAL_LOGIN" != true ]]; then
        log "Local login is off, so there is no admin API session; nothing was changed. Use Admin Settings > External Tools."
        return 3
    fi
    pw="$(secret "$ADMIN_ARN")" || { log "Could not read the admin password secret"; return 1; }
    PW="$pw" jq -nc --arg e "$ADMIN_EMAIL" '{email: $e, password: env.PW}' >"$tmp/body"
    api POST /api/v1/auths/signin "$tmp/body"
    case "$status" in
        200) ;;
        403) log "Open WebUI refused password sign-in (local login is disabled). Use Admin Settings > External Tools."; return 3 ;;
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
    if [[ "$ENABLED" == true ]]; then
        tok="$(secret "$TOKEN_ARN")" || { log "Could not read the MCP token secret"; return 1; }
        TOKEN="$tok" jq -nc --arg url "$URL" '{
            url: $url, path: "", type: "mcp", auth_type: "bearer", key: env.TOKEN, headers: null, forward_cookies: false,
            config: {enable: true, function_name_filter_list: "",
                     access_grants: [{principal_type: "user", principal_id: "*", permission: "read"}]},
            info: {id: "vuln-findings", name: "Vulnerability Findings",
                   description: "Read-only vulnerability findings (hosts, CVEs, severities)."}}' >"$tmp/new"
        # Open WebUI connects and lists the tools, as the Verify button does.
        api POST /api/v1/configs/tool_servers/verify "$tmp/new"
        if [[ "$status" != 200 ]] || ! jq -e '.status == true' "$tmp/out" >/dev/null 2>&1; then
            log "Open WebUI could not connect to the MCP server and list its tools (HTTP $status)"
            return 1
        fi
        log "Open WebUI connected to the MCP server and listed $(jq '.specs | length' "$tmp/out") tools: $(jq -r '[.specs[].name] | sort | join(", ")' "$tmp/out")"
    fi

    # Ours: id "vuln-findings", or any MCP connection to the same URL (replaced, never duplicated).
    # The new entry takes the old one's place; all other entries pass through untouched.
    local mine='def mine: (.info.id? == "vuln-findings") or (.type? == "mcp" and $url != "" and .url? == $url);'
    jq -c --slurpfile d "$tmp/new" --arg url "$URL" "$mine"'
        . as $all | [.[] | select(mine | not)] as $kept
        | (($all | map(mine) | index(true)) // ($kept | length)) as $at
        | if $d[0] == null then $kept else $kept[:$at] + [$d[0]] + $kept[$at:] end' "$tmp/old" >"$tmp/upd" || return 2
    changed=true
    [[ "$(jq -cS . "$tmp/old")" == "$(jq -cS . "$tmp/upd")" ]] && changed=false
    if $CHECK; then
        $changed && log "Check only. Connection would change (feature $ENABLED)." || log "Check only. Connection is already correct (feature $ENABLED)."
        return 0
    fi
    $changed || { log "Connection already correct; nothing to change."; return 0; }

    jq -c '{TOOL_SERVER_CONNECTIONS: .}' "$tmp/upd" >"$tmp/body"
    api POST /api/v1/configs/tool_servers "$tmp/body"
    [[ "$status" == 200 ]] || { log "Could not save the tool servers (HTTP $status)"; return 1; }
    want=0
    [[ "$ENABLED" == true ]] && want=1
    [[ "$(jq --arg url "$URL" "$mine"'[.TOOL_SERVER_CONNECTIONS[] | select(mine)] | length' "$tmp/out")" == "$want" ]] ||
        { log "Saved, but the connection list is not as intended afterwards"; return 1; }
    log "Connection $([[ "$ENABLED" == true ]] && echo registered || echo removed)."
}

for n in $(seq 1 "$RETRIES"); do
    attempt
    rc=$?
    [[ "$rc" -ne 1 ]] && exit "$rc"
    log "Attempt $n/$RETRIES failed"
    [[ "$n" -lt "$RETRIES" ]] && sleep "$RETRY_DELAY"
done
log "Gave up. Run 'sudo ai-lab-register-vuln-mcp' again once the problem is fixed."
exit 2
