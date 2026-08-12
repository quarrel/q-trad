#!/usr/bin/env bash
set -euo pipefail

canonical_unit="qtrad-ibgateway.service"
proc_root="${QTRAD_IBKR_PROC_ROOT:-/proc}"
[[ "$proc_root" == /* ]] || {
    echo "Gateway process root must be absolute" >&2
    exit 64
}

mapfile -t active_units < <(
    systemctl list-units --type=service --state=active --no-legend --plain         'qtrad-ibgateway*.service' | awk '{print $1}'
)
if (("${#active_units[@]}" != 1)) || [[ "${active_units[0]:-}" != "$canonical_unit" ]]; then
    echo "exactly $canonical_unit must be the sole active q-trad Gateway service" >&2
    exit 69
fi

mapfile -t listener_pids < <(
    ss -H -ltnp '( sport = :4002 )' |
        sed -nE 's/.*pid=([0-9]+).*/\1/p' |
        sort -u
)
if (("${#listener_pids[@]}" != 1)); then
    echo "Gateway API port 4002 must have exactly one attributable listener" >&2
    exit 69
fi

listener_pid="${listener_pids[0]}"
cgroup_path="$proc_root/$listener_pid/cgroup"
[[ -r "$cgroup_path" ]] || {
    echo "Gateway API listener cgroup is unavailable" >&2
    exit 69
}
owner_unit="$(sed -nE 's#^.*/([^/]+\.service)$#\1#p' "$cgroup_path" | sort -u)"
[[ "$owner_unit" == "$canonical_unit" ]] || {
    echo "Gateway API port 4002 is not owned by $canonical_unit" >&2
    exit 69
}
