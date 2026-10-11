# Open WebUI admin actions for the control panel. Runs on the lab instance as root, through the
# SSM document in terraform/webui_admin.tf; nothing is installed on the instance for it.
#
# SSM puts the two values below in place only after checking them against the document's
# allowed values, so ACTION is always one of the listed action names and EXPECTED_VERSION is
# empty or a dotted version number, and SINCE_HOUR is empty or up to ten digits. Do not add a parameter without an allowedValues or a tight
# allowedPattern in the document. USER_ID is empty or an Open WebUI user ID (letters, digits, hyphens) and
# ROLE is empty, pending or user. MODEL is empty or a model name (letters, digits and . _ : / -).
# PAYLOAD is empty or base64 text (the write actions' JSON, checked by the panel's API and again here)
# and TOOL_TOKEN_PREFIX is empty or a Secrets Manager name prefix (letters, digits and / _ . -).
#
# Prints exactly one JSON line on stdout; the panel reads the last line. Never prints secrets.
ACTION='{{ action }}'
EXPECTED_VERSION='{{ expectedVersion }}'
SINCE_HOUR='{{ sinceHour }}'
USER_ID='{{ userId }}'
ROLE='{{ role }}'
MODEL='{{ model }}'
PAYLOAD='{{ payload }}'
TOOL_TOKEN_PREFIX='{{ toolTokenPrefix }}'
set -uo pipefail

SETTINGS_FILE="${AI_LAB_SETTINGS_FILE:-/etc/ai-lab/vuln-mcp.env}"

emit() { jq -cn "$@"; }
fail() {
    emit --arg a "$ACTION" --arg m "$1" '{ok: false, action: $a, error: $m}'
    exit 1
}

if ! command -v jq >/dev/null 2>&1; then
    echo '{"ok": false, "error": "jq is not installed on the instance."}'
    exit 1
fi
[[ -r "$SETTINGS_FILE" ]] || fail "The lab settings file is missing, so the Open WebUI port is unknown."
# The settings file is written by cloud-init; read only the one value needed, never source it.
port="$(sed -n "s/^PORT='\([0-9]\{1,5\}\)'\$/\1/p" "$SETTINGS_FILE")"
[[ -n "$port" ]] || fail "The lab settings file has no Open WebUI port."
base="http://127.0.0.1:$port"

# ---- Read-only actions that sign in to Open WebUI as its administrator ----
# tool-servers, settings and export-config. The admin password is read from Secrets Manager on the
# instance by the helper below and never passes through SSM. The admin's "last active" time is put
# back afterwards, so these reads do not keep an idle lab awake. Nothing here changes a setting.
# Do not write a double opening brace anywhere in this file: SSM treats it as a parameter.
LIB="${AI_LAB_WEBUI_LIB:-/usr/local/lib/ai-lab/webui-lib.sh}"
OUTPUT_LIMIT=20000 # SSM returns at most 24000 characters of output

sign_in() {
    [[ -r "$LIB" ]] || fail "The Open WebUI helper is not installed on this instance."
    # shellcheck disable=SC1090
    . "$LIB" || fail "Cannot read the Open WebUI helper."
    [[ -f "${AI_LAB_READY_FILE:-/var/lib/ai-lab/ready}" ]] || fail "Open WebUI is not set up yet."
    webui_load_settings || fail "No usable lab settings."
    webui_begin
    trap webui_end EXIT
    local rc=0
    webui_login || rc=$?
    case "$rc" in
        0) ;;
        3) fail "Password login is off, so the panel cannot sign in to Open WebUI. Use Open WebUI's own admin settings." ;;
        *) fail "The panel could not sign in to Open WebUI." ;;
    esac
}

# Only these Open WebUI settings are ever read out. A new name is added here on purpose; there is
# no "dump everything" path, so a secret that a future version adds cannot leak through this list.
ADMIN_SETTING_KEYS='["ENABLE_SIGNUP","ENABLE_API_KEY","ENABLE_API_KEYS","ENABLE_API_KEY_ENDPOINT_RESTRICTIONS","API_KEY_ALLOWED_ENDPOINTS","DEFAULT_USER_ROLE","JWT_EXPIRES_IN","ENABLE_COMMUNITY_SHARING","ENABLE_MESSAGE_RATING","ENABLE_CHANNELS","ENABLE_NOTES","ENABLE_USER_WEBHOOKS","SHOW_ADMIN_DETAILS"]'
SCALAR_JQ='select(.value | type | IN("boolean", "number", "string")) | select(((.value | type) != "string") or (.value | length) <= 100)'

# Sets SETTINGS_JSON (a list of key, value, scope) and UNAVAILABLE_JSON (what could not be read).
collect_settings() {
    local body
    SETTINGS_JSON='[]'
    UNAVAILABLE_JSON='[]'
    if body="$(webui_get /api/v1/auths/admin/config)" && jq -e 'type == "object"' <<<"$body" >/dev/null 2>&1; then
        SETTINGS_JSON="$(jq -c --argjson keys "$ADMIN_SETTING_KEYS" --argjson cur "$SETTINGS_JSON" \
            '$cur + [to_entries[] | select(.key as $k | $keys | index($k)) | '"$SCALAR_JQ"' | {key: .key, value: .value, scope: "Global"}]' <<<"$body")"
    else
        UNAVAILABLE_JSON="$(jq -c '. + ["Sign-up, sign-in and API key settings"]' <<<"$UNAVAILABLE_JSON")"
    fi
    if body="$(webui_get /api/v1/configs/models)" && jq -e 'type == "object"' <<<"$body" >/dev/null 2>&1; then
        SETTINGS_JSON="$(jq -c --argjson cur "$SETTINGS_JSON" \
            '$cur + [to_entries[] | select(.key == "DEFAULT_MODELS") | '"$SCALAR_JQ"' | {key: .key, value: .value, scope: "Global"}]' <<<"$body")"
    else
        UNAVAILABLE_JSON="$(jq -c '. + ["Default model"]' <<<"$UNAVAILABLE_JSON")"
    fi
    if body="$(webui_get /api/v1/images/config)" && jq -e 'type == "object"' <<<"$body" >/dev/null 2>&1; then
        SETTINGS_JSON="$(jq -c --argjson cur "$SETTINGS_JSON" \
            '$cur + [(if has("ENABLED") then .ENABLED else .enabled? end) | select(type == "boolean") | {key: "IMAGE_GENERATION_ENABLED", value: ., scope: "Global"}]' <<<"$body")"
    else
        UNAVAILABLE_JSON="$(jq -c '. + ["Image generation"]' <<<"$UNAVAILABLE_JSON")"
    fi
    if body="$(webui_get /api/v1/users/default/permissions)" && jq -e 'type == "object"' <<<"$body" >/dev/null 2>&1; then
        SETTINGS_JSON="$(jq -c --argjson cur "$SETTINGS_JSON" \
            '$cur + [to_entries[] | select(.value | type == "object") | .key as $g | (.value | to_entries[]) | select(.value | type == "boolean")
                     | {key: ($g + "." + .key), value: .value, scope: "User default"}]' <<<"$body")"
    else
        UNAVAILABLE_JSON="$(jq -c '. + ["Default permissions for new users"]' <<<"$UNAVAILABLE_JSON")"
    fi
}

# ---- Write actions (phase 2 of #55) ----
# set-default-model, set-model-params, set-feature, upsert-tool-server, remove-tool-server,
# import-skill and apply-desired. The panel's API has already checked the request; each function checks it again,
# changes one thing, reads it back from Open WebUI and reports success only if it matches. A
# secret chosen for a tool server is read here from Secrets Manager (the instance role can read only
# the secrets under the tool token prefix) and is never printed. Boot-managed objects are refused.
RESERVED_MODELS='["security-analyst"]'
RESERVED_SERVERS='["vuln-findings"]'
GRANTS='[{"principal_type": "user", "principal_id": "*", "permission": "read"}]'

# err MESSAGE: the failure result of the current KIND; callers write "err ...; return 1".
err() { jq -cn --arg k "${KIND:-}" --arg m "$1" '{ok: false, kind: $k, error: $m}'; }
done_ok() { jq -cn --arg k "$KIND" --argjson d "${1:-null}" '$d + {ok: true, kind: $k}' | jq -c '.' ; }
jp() { jq -r "$1" <<<"$P"; }

# The model IDs Open WebUI offers (Ollama's and its own).
model_ids() { webui_get /api/v1/models | jq -r '(.data // .)[]? | .id | strings'; }

do_default_model() {
    local model cfg body back
    KIND=default-model
    model="$(jp '.model // ""')"
    [[ "$model" =~ ^[A-Za-z0-9._:/-]{1,100}$ ]] || { err "Choose one of the installed models."; return 1; }
    model_ids | grep -Fxq -- "$model" || { err "That model is not installed in Open WebUI."; return 1; }
    cfg="$(webui_get /api/v1/configs/models)" && jq -e 'type == "object"' <<<"$cfg" >/dev/null 2>&1 || { err "Could not read the model settings from Open WebUI."; return 1; }
    body="$(jq -c --arg m "$model" '.DEFAULT_MODELS = $m' <<<"$cfg")"
    printf '%s' "$body" | webui_post /api/v1/configs/models >/dev/null || { err "Open WebUI refused the change."; return 1; }
    back="$(webui_get /api/v1/configs/models | jq -r '.DEFAULT_MODELS // ""')"
    [[ "$back" == "$model" ]] || { err "Open WebUI did not keep the change."; return 1; }
    done_ok "$(jq -cn --arg m "$model" '{model: $m, scope: "Global"}')"
}

do_model_params() {
    local model old new action path back
    KIND=model-params
    model="$(jp '.model // ""')"
    [[ "$model" =~ ^[A-Za-z0-9._:/-]{1,100}$ ]] || { err "Choose a model."; return 1; }
    jq -e --argjson r "$RESERVED_MODELS" '.model as $m | ($r | index($m)) == null' <<<"$P" >/dev/null || { err "That model is set up by the lab at every start, so a change here would be overwritten. Change it in Terraform."; return 1; }
    # Ranges: the API enforces them first; this is the second check.
    jq -e '((has("temperature") | not) or ((.temperature | type) == "number" and .temperature >= 0 and .temperature <= 2))
           and ((has("numCtx") | not) or ((.numCtx | type) == "number" and .numCtx == (.numCtx | floor) and .numCtx >= 512 and .numCtx <= 131072))
           and (has("temperature") or has("numCtx"))' <<<"$P" >/dev/null || { err "Temperature must be 0 to 2 and context length a whole number from 512 to 131072."; return 1; }
    model_ids | grep -Fxq -- "$model" || { err "That model is not installed in Open WebUI."; return 1; }
    if old="$(webui_get "/api/v1/models/model?id=$model")" && jq -e 'type == "object"' <<<"$old" >/dev/null 2>&1; then
        action=update
        path=/api/v1/models/model/update
    else
        # An installed model with no Open WebUI entry of its own: create one that only carries the settings.
        action=create
        path=/api/v1/models/create
        old="$(jq -cn --arg id "$model" --argjson g "$GRANTS" '{id: $id, base_model_id: null, name: $id, meta: {}, params: {}, is_active: true, access_grants: $g}')"
    fi
    new="$(jq -c --argjson p "$P" '.params = ((.params // {}) + ({temperature: $p.temperature, num_ctx: $p.numCtx} | with_entries(select(.value != null))))
        | del(.created_at, .updated_at, .user_id, .user)' <<<"$old")" || { err "The model record was not in a form this panel understands."; return 1; }
    printf '%s' "$new" | webui_post "$path" >/dev/null || { err "Open WebUI refused the change."; return 1; }
    back="$(webui_get "/api/v1/models/model?id=$model")" || { err "Saved, but Open WebUI would not show the model afterwards."; return 1; }
    jq -e --argjson p "$P" '(($p.temperature == null) or (.params.temperature == $p.temperature)) and (($p.numCtx == null) or (.params.num_ctx == $p.numCtx))' <<<"$back" >/dev/null ||
        { err "Open WebUI did not keep the change."; return 1; }
    done_ok "$(jq -cn --arg m "$model" --arg a "$action" --argjson b "$back" '{model: $m, change: $a, scope: "Model", params: ($b.params | {temperature, num_ctx})}')"
}

do_feature() {
    local feature value endpoint post set_jq view_jq cur new back
    KIND=feature
    feature="$(jp '.feature // ""')"
    value="$(jq -c '.value' <<<"$P")"
    case "$feature" in
        api_keys)
            endpoint=/api/v1/auths/admin/config post=/api/v1/auths/admin/config
            jq -e 'type == "boolean"' <<<"$value" >/dev/null || { err "This switch is on or off."; return 1; }
            set_jq='if has("ENABLE_API_KEYS") then .ENABLE_API_KEYS = $v elif has("ENABLE_API_KEY") then .ENABLE_API_KEY = $v else error("unsupported") end'
            view_jq='if has("ENABLE_API_KEYS") then .ENABLE_API_KEYS else .ENABLE_API_KEY end' ;;
        api_key_routes)
            endpoint=/api/v1/auths/admin/config post=/api/v1/auths/admin/config
            jq -e 'type == "array" and length <= 20 and all(.[]; type == "string" and test("^/[A-Za-z0-9/_.-]{1,100}$"))' <<<"$value" >/dev/null || { err "Routes must be paths such as /api/chat/completions, at most 20."; return 1; }
            set_jq='if has("ENABLE_API_KEY_ENDPOINT_RESTRICTIONS") and has("API_KEY_ALLOWED_ENDPOINTS") then .ENABLE_API_KEY_ENDPOINT_RESTRICTIONS = ($v | length > 0) | .API_KEY_ALLOWED_ENDPOINTS = ($v | join(",")) else error("unsupported") end'
            view_jq='[.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS, .API_KEY_ALLOWED_ENDPOINTS]' ;;
        message_rating)
            endpoint=/api/v1/auths/admin/config post=/api/v1/auths/admin/config
            jq -e 'type == "boolean"' <<<"$value" >/dev/null || { err "This switch is on or off."; return 1; }
            set_jq='if has("ENABLE_MESSAGE_RATING") then .ENABLE_MESSAGE_RATING = $v else error("unsupported") end'
            view_jq='.ENABLE_MESSAGE_RATING' ;;
        image_generation)
            endpoint=/api/v1/images/config post=/api/v1/images/config/update
            jq -e 'type == "boolean"' <<<"$value" >/dev/null || { err "This switch is on or off."; return 1; }
            set_jq='if has("ENABLED") then .ENABLED = $v else error("unsupported") end'
            view_jq='.ENABLED' ;;
        memory)
            endpoint=/api/v1/users/default/permissions post=/api/v1/users/default/permissions
            jq -e 'type == "boolean"' <<<"$value" >/dev/null || { err "This switch is on or off."; return 1; }
            set_jq='if (.features | type) == "object" and (.features | has("memories")) then .features.memories = $v else error("unsupported") end'
            view_jq='.features.memories' ;;
        *) err "Unknown feature."; return 1 ;;
    esac
    cur="$(webui_get "$endpoint")" && jq -e 'type == "object"' <<<"$cur" >/dev/null 2>&1 || { err "Could not read the current setting from Open WebUI."; return 1; }
    new="$(jq -c --argjson v "$value" "$set_jq" <<<"$cur" 2>/dev/null)" || { err "This version of Open WebUI does not have that setting."; return 1; }
    printf '%s' "$new" | webui_post "$post" >/dev/null || { err "Open WebUI refused the change."; return 1; }
    back="$(webui_get "$endpoint")" || { err "Saved, but Open WebUI would not show the setting afterwards."; return 1; }
    [[ "$(jq -c "$view_jq" <<<"$new")" == "$(jq -c "$view_jq" <<<"$back")" ]] || { err "Open WebUI did not keep the change."; return 1; }
    done_ok "$(jq -cn --arg f "$feature" --argjson v "$value" --arg s "$([[ "$feature" == memory ]] && echo "User default" || echo Global)" '{feature: $f, value: $v, scope: $s}')"
}

# The tool server connection the panel is asked to keep. The token, if any, is read from the named
# secret and goes only to Open WebUI.
do_tool_server() {
    local id name type url path secret token="" enabled old list entry vuln_url answer back
    KIND=tool-server
    id="$(jp '.id // ""')"
    name="$(jp '.name // ""')"
    type="$(jp '.type // ""')"
    url="$(jp '.url // ""')"
    path="$(jp '.path // ""')"
    secret="$(jp '.tokenSecret // ""')"
    enabled="$(jq -c 'if .enabled == false then false else true end' <<<"$P")"
    [[ "$id" =~ ^[A-Za-z0-9_-]{1,40}$ ]] || { err "The server ID may use letters, digits, - and _ (up to 40)."; return 1; }
    jq -e --argjson r "$RESERVED_SERVERS" '.id as $i | ($r | index($i)) == null' <<<"$P" >/dev/null || { err "That server is set up by the lab at every start and is not changed here."; return 1; }
    [[ "$type" == mcp || "$type" == openapi ]] || { err "The type must be MCP or OpenAPI."; return 1; }
    [[ "$url" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%/+-]*)?$ ]] || { err "The address must start with https:// and have no login, query or fragment."; return 1; }
    [[ "$path" =~ ^[A-Za-z0-9._/-]{0,100}$ && "$path" != *..* && "$path" != /* ]] || { err "The path may use letters, digits and . _ / - only, with no .. and no leading /."; return 1; }
    [[ ${#name} -ge 1 && ${#name} -le 60 ]] || { err "Give the server a name of up to 60 characters."; return 1; }
    if [[ -n "$secret" ]]; then
        [[ -n "$TOOL_TOKEN_PREFIX" && "$secret" == "$TOOL_TOKEN_PREFIX"* && "$secret" != *..* && "$secret" =~ ^[A-Za-z0-9/_.+=@-]{1,200}$ ]] || { err "Choose a token from the list of lab tool tokens."; return 1; }
        token="$(webui_secret "$secret")" || { err "The chosen token could not be read from Secrets Manager."; return 1; }
    fi
    old="$(webui_get /api/v1/configs/tool_servers)" && list="$(jq -ce '.TOOL_SERVER_CONNECTIONS | arrays' <<<"$old" 2>/dev/null)" || { err "Could not read the tool servers from Open WebUI."; return 1; }
    vuln_url="$(jq -r '[.[] | select(.info.id? == "vuln-findings") | .url][0] // ""' <<<"$list")"
    [[ -z "$vuln_url" || "$url" != "$vuln_url" ]] || { err "That address is the lab's own findings server, which is managed at every start."; return 1; }
    entry="$(TOKEN="$token" jq -cn --arg id "$id" --arg n "$name" --arg t "$type" --arg u "$url" --arg p "$path" --argjson en "$enabled" --argjson g "$GRANTS" --argjson old "$list" '
        ([$old[] | select(.info.id? == $id)][0] // {}) as $o
        | {url: $u, path: $p, type: $t, auth_type: (if env.TOKEN == "" then "none" else "bearer" end), key: env.TOKEN,
           headers: null, forward_cookies: false,
           config: {enable: $en, function_name_filter_list: ($o.config.function_name_filter_list? // ""), access_grants: ($o.config.access_grants? // $g)},
           info: {id: $id, name: $n, description: ($o.info.description? // "")}}')"
    token=""
    # Must connect and list its tools before anything is saved.
    answer="$(printf '%s' "$entry" | WEBUI_POST_TIMEOUT=30 webui_post /api/v1/configs/tool_servers/verify)" && jq -e '.status == true' <<<"$answer" >/dev/null 2>&1 ||
        { err "Open WebUI could not connect to that server and list its tools, so nothing was saved. Check the address, the token and that the server is running."; return 1; }
    jq -c --argjson e "$entry" --arg id "$id" '{TOOL_SERVER_CONNECTIONS: ([.[] | select(.info.id? != $id)] + [$e])}' <<<"$list" | webui_post /api/v1/configs/tool_servers >/dev/null ||
        { err "Open WebUI refused to save the server."; return 1; }
    back="$(webui_get /api/v1/configs/tool_servers)" || { err "Saved, but Open WebUI would not list the servers afterwards."; return 1; }
    jq -e --arg id "$id" --arg u "$url" --arg t "$type" --argjson en "$enabled" '[.TOOL_SERVER_CONNECTIONS[] | select(.info.id? == $id and .url == $u and .type == $t and ((.config.enable != false) == $en))] | length == 1' <<<"$back" >/dev/null ||
        { err "Open WebUI did not keep the change."; return 1; }
    done_ok "$(jq -cn --arg id "$id" --argjson n "$(jq '.specs | length' <<<"$answer")" '{id: $id, toolCount: $n}')"
}

do_tool_server_remove() {
    local id list back
    KIND=tool-server-remove
    id="$(jp '.id // ""')"
    [[ "$id" =~ ^[A-Za-z0-9_-]{1,40}$ ]] || { err "That is not a server ID."; return 1; }
    jq -e --argjson r "$RESERVED_SERVERS" '.id as $i | ($r | index($i)) == null' <<<"$P" >/dev/null || { err "That server is set up by the lab at every start and is not changed here."; return 1; }
    list="$(webui_get /api/v1/configs/tool_servers | jq -ce '.TOOL_SERVER_CONNECTIONS | arrays')" || { err "Could not read the tool servers from Open WebUI."; return 1; }
    jq -e --arg id "$id" 'any(.[]; .info.id? == $id)' <<<"$list" >/dev/null || { done_ok "$(jq -cn --arg id "$id" '{id: $id, alreadyGone: true}')"; return 0; }
    jq -c --arg id "$id" '{TOOL_SERVER_CONNECTIONS: [.[] | select(.info.id? != $id)]}' <<<"$list" | webui_post /api/v1/configs/tool_servers >/dev/null || { err "Open WebUI refused the change."; return 1; }
    back="$(webui_get /api/v1/configs/tool_servers)" || { err "Removed, but Open WebUI would not list the servers afterwards."; return 1; }
    jq -e --arg id "$id" 'all(.TOOL_SERVER_CONNECTIONS[]; .info.id? != $id)' <<<"$back" >/dev/null || { err "Open WebUI did not keep the change."; return 1; }
    done_ok "$(jq -cn --arg id "$id" '{id: $id, removed: true}')"
}

# Adds a model's entry (if it has none) and puts a skill ID in its meta.skillIds. Prints nothing; returns 1 on failure.
attach_skill() { # $1 = model, $2 = skill ID
    local model="$1" sid="$2" old path new back
    if old="$(webui_get "/api/v1/models/model?id=$model")" && jq -e 'type == "object"' <<<"$old" >/dev/null 2>&1; then
        path=/api/v1/models/model/update
    else
        path=/api/v1/models/create
        old="$(jq -cn --arg id "$model" --argjson g "$GRANTS" '{id: $id, base_model_id: null, name: $id, meta: {}, params: {}, is_active: true, access_grants: $g}')"
    fi
    new="$(jq -c --arg s "$sid" '.meta = ((.meta // {}) | .skillIds = (((.skillIds // []) + [$s]) | unique)) | del(.created_at, .updated_at, .user_id, .user)' <<<"$old")" || return 1
    printf '%s' "$new" | webui_post "$path" >/dev/null || return 1
    back="$(webui_get "/api/v1/models/model?id=$model")" || return 1
    jq -e --arg s "$sid" '(.meta.skillIds // []) | index($s) != null' <<<"$back" >/dev/null
}

do_skill() {
    local id old action path body grants back model attached='[]' failed='[]'
    KIND=skill
    id="$(jp '.id // ""')"
    [[ "$id" =~ ^[a-z0-9_-]{1,40}$ ]] || { err "The skill ID may use lower-case letters, digits, - and _ (up to 40)."; return 1; }
    jq -e '(.name | type) == "string" and (.name | length) >= 1 and (.name | length) <= 80
           and ((.description // "") | type) == "string" and ((.description // "") | length) <= 300
           and (.content | type) == "string" and (.content | length) >= 1 and (.content | length) <= 1800
           and ((.enabled // true) | type) == "boolean"
           and ((.models // []) | type) == "array" and ((.models // []) | length) <= 10
           and (([.name, (.description // ""), .content] | join("") | explode | any(.[]; . < 9 or . == 11 or . == 12 or (. >= 14 and . <= 31) or . == 127)) | not)' <<<"$P" >/dev/null || { err "The skill is not in a form this panel accepts."; return 1; }
    jq -e --argjson r "$RESERVED_MODELS" '(.models // []) | all(. as $m | (type == "string") and test("^[A-Za-z0-9._:/-]{1,100}$") and (($r | index($m)) == null))' <<<"$P" >/dev/null || { err "A chosen model is not allowed. The lab's own model is set up at every start and is not changed here."; return 1; }
    # The models must exist before anything is created.
    while IFS= read -r model; do
        [[ -n "$model" ]] || continue
        model_ids | grep -Fxq -- "$model" || { err "The model $model is not installed in Open WebUI."; return 1; }
    done < <(jq -r '(.models // [])[]' <<<"$P")

    if old="$(webui_get "/api/v1/skills/id/$id")" && jq -e 'type == "object" and .id == $i' --arg i "$id" <<<"$old" >/dev/null 2>&1; then
        action=updated
        path="/api/v1/skills/id/$id/update"
        # Keep who may read it as it is, unless the record does not show that in a form this panel can send back.
        grants="$(jq -c '(.access_grants // []) | map({principal_type, principal_id, permission}) | if all(.[]; .principal_type != null and .principal_id != null and .permission != null) then . else null end' <<<"$old")"
        [[ "$grants" != "null" && -n "$grants" ]] || grants="$GRANTS"
    else
        action=created
        path=/api/v1/skills/create
        grants="$GRANTS"
    fi
    body="$(jq -c --argjson g "$grants" '{id: .id, name: .name, description: (.description // ""), content: .content, meta: {}, is_active: (.enabled // true), access_grants: $g}' <<<"$P")"
    printf '%s' "$body" | webui_post "$path" >/dev/null || { err "Open WebUI refused the skill."; return 1; }
    back="$(webui_get "/api/v1/skills/id/$id")" || { err "Saved, but Open WebUI would not show the skill afterwards."; return 1; }
    # The content is compared only when this version shows it back.
    jq -e --argjson p "$P" '.id == $p.id and .name == $p.name and (.is_active == ($p.enabled // true))
                            and ((has("content") | not) or .content == $p.content)' <<<"$back" >/dev/null || { err "Open WebUI did not keep the skill."; return 1; }
    while IFS= read -r model; do
        [[ -n "$model" ]] || continue
        if attach_skill "$model" "$id"; then
            attached="$(jq -c --arg m "$model" '. + [$m]' <<<"$attached")"
        else
            failed="$(jq -c --arg m "$model" '. + [$m]' <<<"$failed")"
        fi
    done < <(jq -r '(.models // [])[]' <<<"$P")
    if [[ "$failed" != "[]" ]]; then
        err "The skill was saved, but it could not be attached to: $(jq -r 'join(", ")' <<<"$failed")."
        return 1
    fi
    done_ok "$(jq -cn --arg i "$id" --arg a "$action" --argjson m "$attached" --argjson e "$(jq -c '.enabled // true' <<<"$P")" '{id: $i, change: $a, enabled: $e, attachedTo: $m}')"
}

# Runs one write request held in $P. Prints one result line.
run_write() {
    case "$1" in
        default-model) do_default_model ;;
        model-params) do_model_params ;;
        feature) do_feature ;;
        tool-server) do_tool_server ;;
        tool-server-remove) do_tool_server_remove ;;
        skill) do_skill ;;
        *) KIND="$1"; err "Unknown setting." ; return 1 ;;
    esac
}

decode_payload() {
    P="$(printf '%s' "$PAYLOAD" | base64 -d 2>/dev/null)" && [[ -n "$P" ]] && jq -e 'type == "object"' <<<"$P" >/dev/null 2>&1 || fail "The request was not understood."
}

# What may be shown of a tool-server connection: never its key, headers or OAuth settings, and the
# address without a login, query or fragment.
SAFE_SERVER_JQ='def clean: sub("^(?<s>https?://)[^/@]*@"; .s) | sub("[?#].*$"; "");
    def safe: {id: (.info.id // null), name: (.info.name // null), type: ((.type // "openapi") | tostring | .[0:20]),
               enabled: (if .config.enable == false then false else true end),
               url: ((.url // "") | tostring | clean | .[0:300]), path: ((.path // "") | tostring | clean | .[0:200]),
               authType: ((.auth_type // "none") | tostring | .[0:30]), forwardCookies: (.forward_cookies == true)};'

case "$ACTION" in
    status)
        healthy=false
        curl -fsS -m 10 -o /dev/null "$base/health" 2>/dev/null && healthy=true
        version="$(curl -fsS -m 10 "$base/api/version" 2>/dev/null | jq -r '.version // empty' 2>/dev/null)" || version=""
        version="${version#v}"
        matches=null
        if [[ -n "$EXPECTED_VERSION" && -n "$version" ]]; then
            matches=false
            [[ "$version" == "$EXPECTED_VERSION" ]] && matches=true
        fi
        # Ollama answers on the instance's loopback without a login: the models it has installed
        # and the ones loaded in memory. Names and sizes only. Null when Ollama does not answer.
        ollama_url="${AI_LAB_OLLAMA_URL:-http://127.0.0.1:11434}"
        ollama=null
        tags="$(curl -fsS -m 10 "$ollama_url/api/tags" 2>/dev/null)" && loaded="$(curl -fsS -m 10 "$ollama_url/api/ps" 2>/dev/null)" || loaded=""
        if [[ -n "${tags:-}" && -n "$loaded" ]]; then
            ollama="$(jq -nc --argjson t "$tags" --argjson p "$loaded" \
                '{installed: [($t.models // [])[] | {name: .name, sizeBytes: (.size // 0)}],
                  loaded: [($p.models // [])[] | {name: .name, sizeBytes: (.size // 0), vramBytes: (.size_vram // 0)}]}' 2>/dev/null)" || ollama=null
            [[ -n "$ollama" ]] || ollama=null
        fi
        emit --argjson h "$healthy" --arg v "$version" --arg e "$EXPECTED_VERSION" --argjson m "$matches" --argjson o "$ollama" \
            '{ok: true, action: "status", healthy: $h,
              version: (if $v == "" then null else $v end),
              expectedVersion: (if $e == "" then null else $e end),
              versionMatches: $m, ollama: $o}'
        ;;
    usage)
        # Hourly token usage per user and model, and the session log, from the helper scripts that
        # cloud-init installs. Collecting first brings the record up to date; the hour in progress
        # is read live and marked provisional. Counts and names only. Output stays under SSM's
        # 24000-character limit (ai-lab-usage trims it and says so in "truncated").
        [[ -x /usr/local/sbin/ai-lab-usage ]] || fail "Usage collection is not installed on this instance."
        since_args=()
        [[ -n "$SINCE_HOUR" ]] && since_args=(--since "$SINCE_HOUR")
        /usr/local/sbin/ai-lab-usage collect >/dev/null 2>&1 || true
        result="$(/usr/local/sbin/ai-lab-usage show "${since_args[@]}" --provisional 2>/dev/null)" || fail "Could not read the usage record."
        jq -c '. + {action: "usage"}' <<<"$result" 2>/dev/null || fail "The usage record was unreadable."
        ;;
    set-role)
        # Used by the spend caps job only (the browser cannot ask for it): moves one Open WebUI user
        # between "user" and "pending". The helper refuses administrators and any other role.
        helper="${AI_LAB_SET_ROLE:-/usr/local/sbin/ai-lab-set-role}"
        [[ -x "$helper" ]] || fail "Role changes are not installed on this instance."
        [[ -n "$USER_ID" && -n "$ROLE" ]] || fail "A user ID and a role are required."
        result="$("$helper" "$USER_ID" "$ROLE" 2>/dev/null)"
        code=$?
        jq -c '. + {action: "set-role"}' <<<"$result" 2>/dev/null || fail "The role change gave no answer."
        exit "$code"
        ;;
    chat-test)
        # One fixed prompt to one model through Open WebUI's chat API; the helper owns the prompt.
        helper="${AI_LAB_CHAT_TEST:-/usr/local/sbin/ai-lab-chat-test}"
        [[ -x "$helper" ]] || fail "The chat test is not installed on this instance."
        [[ -n "$MODEL" ]] || fail "A model name is required."
        result="$("$helper" "$MODEL" 2>/dev/null)"
        code=$?
        jq -c '. + {action: "chat-test"}' <<<"$result" 2>/dev/null || fail "The chat test gave no answer."
        exit "$code"
        ;;
    tool-servers)
        # Every tool server Open WebUI knows, each asked to connect and list its tools (the same
        # check as the Verify button in Open WebUI). Five at most, so the command stays inside its
        # time limit; the rest are reported as not checked.
        sign_in
        conns="$(webui_get /api/v1/configs/tool_servers)" || fail "Could not read the tool servers from Open WebUI."
        jq -e '.TOOL_SERVER_CONNECTIONS | arrays' <<<"$conns" >/dev/null 2>&1 || fail "Open WebUI returned its tool servers in a form this panel does not understand."
        servers='[]'
        total="$(jq '.TOOL_SERVER_CONNECTIONS | length' <<<"$conns")"
        checked=0
        for ((i = 0; i < total && i < 50; i++)); do
            conn="$(jq -c ".TOOL_SERVER_CONNECTIONS[$i]" <<<"$conns")"
            entry="$(jq -c "$SAFE_SERVER_JQ"' safe | . + {reachable: null, toolCount: null, toolNames: [], error: null}' <<<"$conn")"
            enabled="$(jq -r '.enabled' <<<"$entry")"
            if [[ "$enabled" == true && "$checked" -lt 5 ]]; then
                checked=$((checked + 1))
                if answer="$(printf '%s' "$conn" | WEBUI_POST_TIMEOUT=15 webui_post /api/v1/configs/tool_servers/verify)" && jq -e '.status == true' <<<"$answer" >/dev/null 2>&1; then
                    entry="$(jq -c --argjson a "$answer" '. + {reachable: true, toolCount: (($a.specs // []) | length),
                        toolNames: ([($a.specs // [])[] | .name? | strings | .[0:80]] | sort | .[0:50])}' <<<"$entry")"
                else
                    entry="$(jq -c '. + {reachable: false, error: "Open WebUI could not connect to this server and list its tools."}' <<<"$entry")"
                fi
            elif [[ "$enabled" == true ]]; then
                entry="$(jq -c '. + {error: "Not checked this time (only the first five enabled servers are)."}' <<<"$entry")"
            fi
            servers="$(jq -c --argjson e "$entry" '. + [$e]' <<<"$servers")"
        done
        emit --argjson s "$servers" --argjson n "$total" '{ok: true, action: "tool-servers", signedIn: true, total: $n, servers: $s}'
        ;;
    settings)
        # The settings that matter for a shared lab, each marked Global (applies to everyone) or User default
        # (the starting point for new users). Only the fixed list above is read.
        sign_in
        collect_settings
        emit --argjson s "$SETTINGS_JSON" --argjson u "$UNAVAILABLE_JSON" '{ok: true, action: "settings", settings: $s, unavailable: $u}'
        ;;
    export-config)
        # A configuration export with the secrets left out. Included: the settings above, the tool
        # servers (name, type, address without any login or query, auth type) and the models
        # (id, name, base model, numeric parameters). Never included: passwords, API keys, tokens, tool server
        # keys, headers and OAuth settings, system prompts, users, chats, files and memories.
        sign_in
        collect_settings
        conns="$(webui_get /api/v1/configs/tool_servers)" || conns='{}'
        servers="$(jq -c "$SAFE_SERVER_JQ"' [(.TOOL_SERVER_CONNECTIONS? // [])[] | safe]' <<<"$conns" 2>/dev/null)" || servers='[]'
        models_raw="$(webui_get /api/v1/models)" || models_raw=""
        models='[]'
        if [[ -n "$models_raw" ]] && jq -e '(.data // .) | type == "array"' <<<"$models_raw" >/dev/null 2>&1; then
            models="$(jq -c '[(.data // .)[] | {id: (.id | tostring | .[0:100]), name: (.name | if type == "string" then .[0:100] else null end),
                baseModel: (.info.base_model_id? | if type == "string" then .[0:100] else null end),
                params: ((.info.params // {}) | to_entries | map(select(.value | type | IN("number", "boolean"))) | from_entries)}]' <<<"$models_raw")" || models='[]'
        else
            UNAVAILABLE_JSON="$(jq -c '. + ["Models"]' <<<"$UNAVAILABLE_JSON")"
        fi
        version="$(curl -fsS -m 10 "$base/api/version" 2>/dev/null | jq -r '.version // empty' 2>/dev/null)" || version=""
        build() {
            jq -cn --arg v "${version#v}" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson st "$SETTINGS_JSON" --argjson sv "$servers" \
                --argjson m "$1" --argjson un "$UNAVAILABLE_JSON" --argjson tr "$2" \
                '{ok: true, action: "export-config", exportedAt: $at, openWebuiVersion: (if $v == "" then null else $v end), truncated: $tr,
                  omitted: ["passwords", "API keys and tokens", "tool server keys, headers and OAuth settings", "system prompts", "users", "chats", "files", "memories"],
                  settings: $st, toolServers: $sv, models: $m, unavailable: $un}'
        }
        out="$(build "$models" false)"
        if ((${#out} > OUTPUT_LIMIT)); then
            out="$(build "$(jq -c '[.[] | {id: .id}]' <<<"$models")" true)"
        fi
        ((${#out} <= OUTPUT_LIMIT)) || fail "The configuration is too large to export through the panel."
        echo "$out"
        ;;
    set-default-model | set-model-params | set-feature | upsert-tool-server | remove-tool-server | import-skill)
        sign_in
        decode_payload
        case "$ACTION" in
            set-default-model) kind=default-model ;;
            set-model-params) kind=model-params ;;
            set-feature) kind=feature ;;
            upsert-tool-server) kind=tool-server ;;
            remove-tool-server) kind=tool-server-remove ;;
            import-skill) kind=skill ;;
        esac
        result="$(run_write "$kind")"
        rc=$?
        jq -c '. + {action: $a}' --arg a "$ACTION" <<<"$result" 2>/dev/null || fail "The change gave no answer."
        exit "$rc"
        ;;
    apply-desired)
        # After an instance start: puts the panel's saved settings back, one at a time. A setting
        # that fails does not stop the rest. The saved settings come from the panel's API.
        sign_in
        decode_payload
        jq -e '.items | arrays' <<<"$P" >/dev/null 2>&1 || fail "The request was not understood."
        results='[]'
        all_items="$P"
        n="$(jq '.items | length' <<<"$all_items")"
        for ((i = 0; i < n && i < 40; i++)); do
            item="$(jq -c ".items[$i]" <<<"$all_items")"
            P="$(jq -c 'del(.kind)' <<<"$item")"
            kind="$(jq -r '.kind // ""' <<<"$item")"
            one="$(run_write "$kind")" || true
            results="$(jq -c --argjson r "$(jq -c . <<<"$one" 2>/dev/null || echo '{"ok":false,"error":"No answer."}')" '. + [$r]' <<<"$results")"
        done
        emit --argjson r "$results" '{ok: ($r | all(.ok)), action: "apply-desired", applied: ($r | map(select(.ok)) | length), failed: ($r | map(select(.ok | not)) | length), results: $r}'
        ;;
    *)
        fail "Unknown action."
        ;;
esac
