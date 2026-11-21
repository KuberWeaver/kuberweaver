#!/usr/bin/env bash
# Deploy the checkout to the local user unit, idempotently.
#
# The autopilot runs with cron approvals, so it cannot restart a systemd unit
# itself. This script runs from a user timer instead: it compares the checked out
# commit with the one recorded as deployed, rebuilds the frontend when its
# sources moved, restarts the unit, and records the commit only after readiness
# answers 200.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
unit=kuberweaver.service
url=http://127.0.0.1:8088
marker="${XDG_STATE_HOME:-$HOME/.local/state}/kuberweaver/deployed-sha"

mkdir -p "$(dirname "$marker")"
sha=$(git -C "$repo" rev-parse HEAD)
current=$(cat "$marker" 2>/dev/null || printf 'none')

if [ "$sha" = "$current" ]; then
    exit 0
fi

frontend_changed=1
if [ "$current" != "none" ] && git -C "$repo" cat-file -e "$current^{commit}" 2>/dev/null; then
    if git -C "$repo" diff --quiet "$current" "$sha" -- frontend/src frontend/index.html frontend/package.json frontend/vite.config.ts; then
        frontend_changed=0
    fi
fi

if [ "$frontend_changed" = 1 ] && [ -d "$repo/frontend/node_modules" ]; then
    (cd "$repo/frontend" && npm run build >/dev/null)
fi

systemctl --user restart "$unit"
for _ in $(seq 1 15); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$url/readyz" || true)
    [ "$code" = "200" ] && break
    sleep 1
done

if [ "$code" != "200" ]; then
    printf 'deploy of %s failed readiness (%s)\n' "$sha" "$code" >&2
    exit 1
fi

printf '%s\n' "$sha" > "$marker"
printf 'deployed %s\n' "$sha"
