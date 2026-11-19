#!/usr/bin/env bash
# Enterprise-services autopilot runner: bounded slices until deadline/STOP.
set -u
REPO="/home/ubu/Desktop/infra-platform-lab/Prototype/kuberweaver"
DIR="$REPO/ops/autopilot-enterprise"
cd "$REPO" || exit 1
mkdir -p "$DIR/reports"
exec 9>"$DIR/.lock"
if ! flock -n 9; then echo "another runner holds the lock; exit"; exit 0; fi
DEADLINE="2026-11-20T08:39:59+01:00"
while true; do
  [ -f "$DIR/STOP" ] && { echo "STOP marker"; break; }
  NOW=$(date +%s); DL=$(date -d "$DEADLINE" +%s)
  [ "$NOW" -ge "$DL" ] && { echo "deadline passed"; break; }
  NEXT=$(python3 "$DIR/next.py")
  echo "$NEXT" | grep -q '"action": "stop"' && { echo "next.py says stop"; break; }
  echo "=== slice start $(date '+%F %T') ==="
  hermes chat --oneshot --query-file "$DIR/slice-prompt.md" --run-budget 1500 --max-turns 120 \
    >> "$DIR/runner.log" 2>&1
  echo "=== slice end $(date '+%F %T') rc=$? ==="
  # ensure clean tree between slices (commit or revert stray edits)
  if [ -n "$(git status --short)" ]; then
    echo "dirty tree after slice; attempting checkpoint" >> "$DIR/runner.log"
  fi
  sleep 5
done
echo "runner exited $(date '+%F %T')"
