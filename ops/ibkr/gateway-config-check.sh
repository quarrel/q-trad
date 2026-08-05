#!/usr/bin/env bash
set -euo pipefail

gateway_settings="${QTRAD_IBKR_GATEWAY_SETTINGS:?set QTRAD_IBKR_GATEWAY_SETTINGS to the Gateway settings directory}"
expected_restart="${QTRAD_IBKR_AUTO_RESTART_TIME:?set QTRAD_IBKR_AUTO_RESTART_TIME to the approved daily restart time}"

fail() {
    echo "IBKR Gateway configuration invalid: $*" >&2
    exit 69
}

[[ "$gateway_settings" == /* ]] || fail "QTRAD_IBKR_GATEWAY_SETTINGS must be an absolute path"
gateway_config="$gateway_settings/config.ini"
jts_ini="$gateway_settings/jts.ini"
[[ -f "$gateway_config" ]] || fail "Gateway/IBC config is missing: $gateway_config"
[[ -f "$jts_ini" ]] || fail "Gateway settings file is missing: $jts_ini"

read_key() {
    local file="$1"
    local wanted="$2"
    awk -F= -v wanted="$wanted" '
        /^[[:space:]]*[#;]/ { next }
        {
            name = $1
            sub(/^[[:space:]]+/, "", name)
            sub(/[[:space:]]+$/, "", name)
            if (name == wanted) {
                value = $0
                sub(/^[^=]*=/, "", value)
                sub(/^[[:space:]]+/, "", value)
                sub(/[[:space:]]+$/, "", value)
                print value
                exit
            }
        }
    ' "$file"
}

read_section_key() {
    local file="$1"
    local wanted_section="$2"
    local wanted_key="$3"
    awk -F= -v wanted_section="$wanted_section" -v wanted_key="$wanted_key" '
        BEGIN { in_section = 0 }
        /^[[:space:]]*[#;]/ { next }
        /^[[:space:]]*\[/ {
            section = $0
            sub(/^[[:space:]]*\[/, "", section)
            sub(/\][[:space:]]*$/, "", section)
            in_section = (section == wanted_section)
            next
        }
        {
            name = $1
            sub(/^[[:space:]]+/, "", name)
            sub(/[[:space:]]+$/, "", name)
            if (in_section && name == wanted_key) {
                value = $0
                sub(/^[^=]*=/, "", value)
                sub(/^[[:space:]]+/, "", value)
                sub(/[[:space:]]+$/, "", value)
                print value
                exit
            }
        }
    ' "$file"
}

is_enabled() {
    case "${1,,}" in
        true|yes) return 0 ;;
        *) return 1 ;;
    esac
}

normalise_time() {
    local raw="${1//[[:space:]]/}"
    local hour
    local minute

    if [[ "$raw" =~ ^([0-9]{1,2}):([0-9]{2})(:[0-9]{2})?$ ]]; then
        hour=$((10#${BASH_REMATCH[1]}))
        minute=$((10#${BASH_REMATCH[2]}))
    elif [[ "$raw" =~ ^(0?[1-9]|1[0-2]):([0-9]{2})(AM|PM|am|pm)$ ]]; then
        hour=$((10#${BASH_REMATCH[1]}))
        minute=$((10#${BASH_REMATCH[2]}))
        if [[ "${BASH_REMATCH[3],,}" == "pm" && "$hour" -lt 12 ]]; then
            ((hour += 12))
        elif [[ "${BASH_REMATCH[3],,}" == "am" && "$hour" -eq 12 ]]; then
            hour=0
        fi
    else
        return 1
    fi

    ((hour <= 23 && minute <= 59)) || return 1
    printf '%02d:%02d\n' "$hour" "$minute"
}

trading_mode="$(read_key "$gateway_config" TradingMode)"
[[ "${trading_mode,,}" == "paper" ]] || fail "TradingMode must be paper"

read_only_api="$(read_key "$gateway_config" ReadOnlyApi)"
is_enabled "$read_only_api" || fail "ReadOnlyApi must be yes/true"

configured_restart="$(read_key "$gateway_config" AutoRestartTime)"
[[ -n "$configured_restart" ]] || fail "AutoRestartTime is not explicitly configured"
expected_restart_normalised="$(normalise_time "$expected_restart")" || \
    fail "QTRAD_IBKR_AUTO_RESTART_TIME is not a valid time"
configured_restart_normalised="$(normalise_time "$configured_restart")" || \
    fail "Gateway AutoRestartTime is not a valid time"
[[ "$configured_restart_normalised" == "$expected_restart_normalised" ]] || \
    fail "AutoRestartTime is $configured_restart_normalised, expected $expected_restart_normalised"

api_only="$(read_section_key "$jts_ini" IBGateway ApiOnly)"
is_enabled "$api_only" || fail "[IBGateway]/ApiOnly must be true/yes"

trusted_ips="$(read_section_key "$jts_ini" IBGateway TrustedIPs)"
[[ "$trusted_ips" == "127.0.0.1" ]] || fail "[IBGateway]/TrustedIPs must be 127.0.0.1"

echo "IBKR Gateway configuration verified: paper, read-only, localhost-only, restart $expected_restart_normalised"
