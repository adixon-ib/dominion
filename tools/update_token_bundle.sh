#!/usr/bin/env bash
## update_token_bundle.sh <gm_ip> — Fetch lpc_uid from GM, rebuild token_bundle.tar, and optionally deploy
#
# update_token_bundle.sh
#
# Usage: ./update_token_bundle.sh <gm_ip>
#
# - Queries the Grid Master at <gm_ip> via WAPI (admin:infoblox) for the
#   cluster's lpc_uid (the value stored as one.cluster.license_uid /
#   licenseUid in tab.json).
# - Untars ./token_bundle.tar into a temp dir.
# - Updates licenseUid in tab.json with the lpc_uid pulled from the GM.
# - Re-tars the bundle as ./token_bundle.tar with a flat layout
#   (tab.json, docker-compose.yml, usage_calculator at the root, no ./ prefix).
# - Prompts whether to scp the new bundle to root@<gm_ip>:/storage/tmp/token_bundle.tar.
# - After a successful scp, prompts whether to touch
#   /infoblox/var/flags/airgapped_skip on the appliance.

set -euo pipefail

WAPI_USER="admin"
WAPI_PASS="infoblox"
WAPI_VERSION="v2.12"

IP_ADDRESS="${1:-${NIOS_IP:-}}"
if [[ -z "$IP_ADDRESS" ]]; then
    echo "Usage: $0 <gm_ip>  (or set NIOS_IP)"
    exit 1
fi

# Resolve the script's own directory so we find token_bundle.tar
# regardless of where the script is called from.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

BUNDLE="$SCRIPT_DIR/token_bundle.tar"
TAB_FILE="tab.json"
REMOTE_PATH="/storage/tmp/token_bundle.tar"
REQUIRED_MEMBERS=(tab.json docker-compose.yml usage_calculator)

# ---- sanity ----
if [[ ! -f "$BUNDLE" ]]; then
    echo "Error: token_bundle.tar not found in $SCRIPT_DIR"
    exit 1
fi
command -v curl >/dev/null 2>&1 || { echo "Error: curl is required"; exit 1; }

# ---- fetch lpc_uid from the GM via WAPI ----
WAPI_URL="https://${IP_ADDRESS}/wapi/${WAPI_VERSION}/grid:license_pool_container?_return_fields=lpc_uid"
echo "==> Querying ${WAPI_URL}"

WAPI_RESPONSE="$(curl -sk --fail -u "${WAPI_USER}:${WAPI_PASS}" "$WAPI_URL")" || {
    echo "Error: WAPI request failed."
    exit 1
}

# Extract lpc_uid. Prefer jq if present, fall back to grep/sed.
if command -v jq >/dev/null 2>&1; then
    NEW_LICENSE_UID="$(printf '%s' "$WAPI_RESPONSE" | jq -r '.[0].lpc_uid // empty')"
else
    NEW_LICENSE_UID="$(printf '%s' "$WAPI_RESPONSE" \
        | grep -oE '"lpc_uid"[[:space:]]*:[[:space:]]*"[^"]+"' \
        | head -n1 \
        | sed -E 's/.*"lpc_uid"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
fi

if [[ -z "${NEW_LICENSE_UID:-}" ]]; then
    echo "Error: could not parse lpc_uid from WAPI response:"
    echo "$WAPI_RESPONSE"
    exit 1
fi
echo "==> Got lpc_uid from GM: $NEW_LICENSE_UID"

# ---- extract bundle ----
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "==> Extracting $BUNDLE into temp dir: $WORK_DIR"
tar -xf "$BUNDLE" -C "$WORK_DIR"

TAB_PATH="$(find "$WORK_DIR" -type f -name "$TAB_FILE" | head -n1)"
if [[ -z "$TAB_PATH" ]]; then
    echo "Error: $TAB_FILE not found inside $BUNDLE"
    exit 1
fi

# ---- modify license_uid ----
echo "==> Updating license_uid in $TAB_PATH to: $NEW_LICENSE_UID"
if command -v jq >/dev/null 2>&1; then
    tmp=$(mktemp)
    jq --arg uid "$NEW_LICENSE_UID" '.license_uid = $uid | del(.licenseUid)' "$TAB_PATH" > "$tmp"
    mv "$tmp" "$TAB_PATH"
else
    sed -i.bak -E 's/"licenseUid"/"license_uid"/g' "$TAB_PATH"
    sed -i.bak -E "s/(\"license_uid\"[[:space:]]*:[[:space:]]*\")[^\"]*(\")/\1${NEW_LICENSE_UID}\2/" "$TAB_PATH"
    rm -f "${TAB_PATH}.bak"
fi

echo "==> New tab.json contents:"
cat "$TAB_PATH"
echo

# ---- repack ----
TAB_DIR="$(dirname "$TAB_PATH")"
echo "==> Verifying required members exist in $TAB_DIR"
for m in "${REQUIRED_MEMBERS[@]}"; do
    if [[ ! -e "$TAB_DIR/$m" ]]; then
        echo "Error: required member '$m' missing from bundle"
        exit 1
    fi
done

echo "==> Re-creating $BUNDLE from $TAB_DIR (flat layout, no ./ prefix)"
(
    cd "$TAB_DIR"
    rm -f ._* .DS_Store 2>/dev/null || true
    tar -cf "$BUNDLE" "${REQUIRED_MEMBERS[@]}"
)

echo "==> $BUNDLE rebuilt. Contents:"
tar -tf "$BUNDLE"
echo

# ---- prompt for scp ----
read -r -p "Do you want to scp the bundle over to ${IP_ADDRESS} with username root? [y/N]: " ANSWER
case "$ANSWER" in
    [yY]|[yY][eE][sS])
        echo "==> Copying $BUNDLE to root@${IP_ADDRESS}:${REMOTE_PATH}"
        scp "$BUNDLE" "root@${IP_ADDRESS}:${REMOTE_PATH}"
        echo "==> Done."

        # ---- prompt for airgapped_skip flag ----
        read -r -p "Do you want to touch /infoblox/var/flags/airgapped_skip on ${IP_ADDRESS}? [y/N]: " TOUCH_ANSWER
        case "$TOUCH_ANSWER" in
            [yY]|[yY][eE][sS])
                echo "==> Touching /infoblox/var/flags/airgapped_skip on root@${IP_ADDRESS}"
                ssh "root@${IP_ADDRESS}" "touch /infoblox/var/flags/airgapped_skip"
                echo "==> Flag set."
                ;;
            *)
                echo "==> Skipping touch of /infoblox/var/flags/airgapped_skip."
                ;;
        esac
        ;;
    *)
        echo "==> Skipping scp. Bundle is available at $BUNDLE"
        ;;
esac