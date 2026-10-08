# Open WebUI admin actions for the control panel. Runs on the lab instance as root, through the
# SSM document in terraform/webui_admin.tf; nothing is installed on the instance for it.
#
# SSM puts the two values below in place only after checking them against the document's
# allowed values, so ACTION is always one of the listed action names and EXPECTED_VERSION is
# empty or a dotted version number. Do not add a parameter without an allowedValues or a tight
# allowedPattern in the document.
#
# Prints exactly one JSON line on stdout; the panel reads the last line. Never prints secrets.
ACTION='{{ action }}'
EXPECTED_VERSION='{{ expectedVersion }}'
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
        emit --argjson h "$healthy" --arg v "$version" --arg e "$EXPECTED_VERSION" --argjson m "$matches" \
            '{ok: true, action: "status", healthy: $h,
              version: (if $v == "" then null else $v end),
              expectedVersion: (if $e == "" then null else $e end),
              versionMatches: $m}'
        ;;
    *)
        fail "Unknown action."
        ;;
esac
