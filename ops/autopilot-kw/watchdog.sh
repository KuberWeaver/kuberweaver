#!/usr/bin/env bash
# Finite watchdog: relaunch the kw runner if it died with work remaining, before the deadline.
REPO="/home/ubu/Desktop/infra-platform-lab/Prototype/kuberweaver"
DIR="$REPO/ops/autopilot-kw"
PY="$REPO/.venv/bin/python"
[ -f "$DIR/STOP" ] && exit 0
DEADLINE=$("$PY" -c "import json;print(json.load(open('$DIR/STATE.json'))['deadline'])" 2>/dev/null) || exit 0
NOW=$(date +%s); DL=$(date -d "$DEADLINE" +%s 2>/dev/null) || exit 0
[ "$NOW" -ge "$DL" ] && exit 0
# If we can take the lock, the runner is not holding it (i.e. not running).
if flock -n "$DIR/.lock" -c true 2>/dev/null; then
  NEXT=$("$PY" "$DIR/next.py" 2>/dev/null)
  if echo "$NEXT" | grep -q '"action": "work"'; then
    nohup bash "$DIR/run.sh" >/dev/null 2>&1 &
    echo "$(date '+%F %T') watchdog relaunched runner" >> "$DIR/runner.log"
    echo "relaunched kw runner"
  fi
fi
exit 0
