#!/usr/bin/env bash
# Watchdog: relaunch the runner if the lock is free, work remains, and before deadline.
set -u
REPO="/home/ubu/Desktop/infra-platform-lab/Prototype/kuberweaver"
DIR="$REPO/ops/autopilot-enterprise"
DEADLINE="2026-11-20T08:39:59+01:00"
[ -f "$DIR/STOP" ] && exit 0
NOW=$(date +%s); DL=$(date -d "$DEADLINE" +%s)
[ "$NOW" -ge "$DL" ] && exit 0
python3 "$DIR/next.py" | grep -q '"action": "stop"' && exit 0
# if a runner already holds the lock, do nothing
if flock -n "$DIR/.lock" -c true 2>/dev/null; then
  nohup bash "$DIR/run.sh" >> "$DIR/watchdog.log" 2>&1 &
  echo "watchdog relaunched runner $(date '+%F %T')" >> "$DIR/watchdog.log"
fi
exit 0
