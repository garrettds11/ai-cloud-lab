# Shared helpers for the lab's scripts that call the Open WebUI admin API from the instance.
# Sourced, never run. Installed by cloud-init at /usr/local/lib/ai-lab/webui-lib.sh.
#
# Why this is more than a curl wrapper: Open WebUI marks a user "active" on every authenticated
# request, and the idle monitor (ai-lab-idle-check) treats an active user as a reason to keep the
# lab running. A script that polls the API as the admin would therefore keep the lab awake
# forever. webui_begin remembers the admin's last_active_at before the first request and
# webui_end puts it back afterwards (only if nothing but this script has moved it).
#
# Needs in the environment (the callers set these from /etc/ai-lab/vuln-mcp.env):
#   PORT REGION ADMIN_ARN ADMIN_EMAIL LOCAL_LOGIN, and optionally OPEN_WEBUI_VOLUME.
# Test overrides: AI_LAB_WEBUI_DB (path to webui.db), AI_LAB_STATE_DIR.

WEBUI_TOKEN_FILE="${AI_LAB_STATE_DIR:-/run/ai-lab}/webui-admin-token"
_webui_baseline=""
_webui_began=0

webui_db_path() {
    if [[ -n "${AI_LAB_WEBUI_DB:-}" ]]; then
        printf '%s' "$AI_LAB_WEBUI_DB"
        return
    fi
    local mount
    mount="$(docker volume inspect -f '{{.Mountpoint}}' "${OPEN_WEBUI_VOLUME:-}" 2>/dev/null)" || return 1
    [[ -n "$mount" ]] && printf '%s/webui.db' "$mount"
}

# SQL-quote an email for the one query below. Emails come from the settings file, not from users,
# but quote anyway.
_webui_sql_quote() { printf "'%s'" "${1//\'/\'\'}"; }

webui_begin() {
    _webui_began=1
    _webui_baseline=""
    local db q
    db="$(webui_db_path)" || return 0
    [[ -r "$db" ]] || return 0
    q="$(_webui_sql_quote "${ADMIN_EMAIL:-}")"
    _webui_baseline="$(sqlite3 -readonly -cmd '.timeout 2000' "$db" "SELECT last_active_at FROM user WHERE email = $q;" 2>/dev/null)" || _webui_baseline=""
    [[ "$_webui_baseline" =~ ^[0-9]+$ ]] || _webui_baseline=""
}

# Put the admin's last_active_at back, unless it is now older than the baseline or the account was
# active long after this script started (a person using it): then leave it.
webui_end() {
    [[ "$_webui_began" == 1 ]] || return 0
    _webui_began=0
    [[ -n "$_webui_baseline" ]] || return 0
    local db q now
    db="$(webui_db_path)" || return 0
    [[ -w "$db" ]] || return 0
    q="$(_webui_sql_quote "${ADMIN_EMAIL:-}")"
    now="$(date +%s)"
    # Only rewind a value this script produced: newer than the baseline, and not in the future.
    sqlite3 -cmd '.timeout 2000' "$db" \
        "UPDATE user SET last_active_at = $_webui_baseline WHERE email = $q AND last_active_at > $_webui_baseline AND last_active_at <= $now + 2;" \
        >/dev/null 2>&1 || true
}

webui_secret() {
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

# webui_login: ensures a usable admin session token in $WEBUI_TOKEN_FILE (root-only, tmpfs).
# Returns 0 ready, 3 password login is off (cannot automate), 1 not possible now.
webui_login() {
    if [[ "${LOCAL_LOGIN:-}" != true ]]; then
        return 3
    fi
    umask 077
    mkdir -p "$(dirname "$WEBUI_TOKEN_FILE")"
    local code
    if [[ -s "$WEBUI_TOKEN_FILE" ]]; then
        code="$(printf 'header = "Authorization: Bearer %s"\n' "$(<"$WEBUI_TOKEN_FILE")" |
            curl -sS -m 20 -o /dev/null -w '%{http_code}' -K - "http://127.0.0.1:$PORT/api/v1/auths/" 2>/dev/null)" || code=000
        [[ "$code" == 200 ]] && return 0
        rm -f "$WEBUI_TOKEN_FILE"
    fi
    local pw body token
    pw="$(webui_secret "$ADMIN_ARN")" || return 1
    body="$(PW="$pw" jq -nc --arg e "$ADMIN_EMAIL" '{email: $e, password: env.PW}')" || return 1
    pw=""
    code="$(printf '%s' "$body" | curl -sS -m 30 -o "$WEBUI_TOKEN_FILE.tmp" -w '%{http_code}' -X POST \
        -H 'Content-Type: application/json' --data-binary @- "http://127.0.0.1:$PORT/api/v1/auths/signin" 2>/dev/null)" || code=000
    body=""
    if [[ "$code" == 403 ]]; then
        rm -f "$WEBUI_TOKEN_FILE.tmp"
        return 3
    fi
    if [[ "$code" != 200 ]]; then
        rm -f "$WEBUI_TOKEN_FILE.tmp"
        return 1
    fi
    token="$(jq -r '.token // empty' "$WEBUI_TOKEN_FILE.tmp" 2>/dev/null)"
    rm -f "$WEBUI_TOKEN_FILE.tmp"
    [[ -n "$token" ]] || return 1
    printf '%s' "$token" >"$WEBUI_TOKEN_FILE"
}

# webui_get PATH_AND_QUERY: GET with the admin token. Prints the body; returns 0 only for HTTP 200.
# A 401 drops the cached token so the next login starts fresh.
webui_get() {
    local out code
    out="$(mktemp)"
    code="$(printf 'header = "Authorization: Bearer %s"\n' "$(<"$WEBUI_TOKEN_FILE")" |
        curl -sS -m 60 -o "$out" -w '%{http_code}' -K - "http://127.0.0.1:$PORT$1" 2>/dev/null)" || code=000
    if [[ "$code" == 200 ]]; then
        cat "$out"
        rm -f "$out"
        return 0
    fi
    [[ "$code" == 401 ]] && rm -f "$WEBUI_TOKEN_FILE"
    rm -f "$out"
    return 1
}

# webui_post PATH: POST the JSON body on stdin with the admin token. Prints the response; returns 0
# only for HTTP 200. WEBUI_POST_TIMEOUT (seconds, default 60) bounds the request. The body goes through a root-only temp file, because curl's stdin carries the
# token header.
webui_post() {
    local out code bodyfile
    umask 077
    out="$(mktemp)"
    bodyfile="$(mktemp)"
    cat >"$bodyfile"
    code="$(printf 'header = "Authorization: Bearer %s"\n' "$(<"$WEBUI_TOKEN_FILE")" |
        curl -sS -m "${WEBUI_POST_TIMEOUT:-60}" -o "$out" -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data-binary "@$bodyfile" \
            -K - "http://127.0.0.1:$PORT$1" 2>/dev/null)" || code=000
    rm -f "$bodyfile"
    if [[ "$code" == 200 ]]; then
        cat "$out"
        rm -f "$out"
        return 0
    fi
    [[ "$code" == 401 ]] && rm -f "$WEBUI_TOKEN_FILE"
    rm -f "$out"
    return 1
}

# Read KEY='value' from the settings file without executing it.
webui_setting() {
    sed -n "s/^$1='\\(.*\\)'\$/\\1/p" "${AI_LAB_SETTINGS_FILE:-/etc/ai-lab/vuln-mcp.env}" | head -n1
}

webui_load_settings() {
    local k
    for k in PORT REGION ADMIN_ARN ADMIN_EMAIL LOCAL_LOGIN; do
        printf -v "$k" '%s' "$(webui_setting "$k")"
    done
    [[ "$PORT" =~ ^[0-9]{1,5}$ ]] || return 1
    # The data volume's name is in the auto-stop settings (written as KEY=value, no quotes).
    OPEN_WEBUI_VOLUME="$(sed -n 's/^OPEN_WEBUI_VOLUME=\(.*\)$/\1/p' "${AI_LAB_AUTO_STOP_ENV:-/etc/ai-lab/auto-stop.env}" 2>/dev/null | head -n1)" || OPEN_WEBUI_VOLUME=""
}
