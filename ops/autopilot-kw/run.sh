#!/usr/bin/env bash
# kw CLI unattended runner: one backlog slice per iteration until deadline/stop.
set -u
REPO="/home/ubu/Desktop/infra-platform-lab/Prototype/kuberweaver"
cd "$REPO" || exit 1
DIR="ops/autopilot-kw"
PY="$REPO/.venv/bin/python"
exec 9>"$DIR/.lock"
flock -n 9 || { echo "$(date '+%F %T') runner already active"; exit 0; }
echo "$(date '+%F %T') runner started pid $$" >> "$DIR/runner.log"
while true; do
  if [ -f "$DIR/STOP" ]; then echo "$(date '+%F %T') stop marker" >> "$DIR/runner.log"; break; fi
  DEADLINE=$("$PY" -c "import json;print(json.load(open('$DIR/STATE.json'))['deadline'])")
  NOW=$(date +%s); DL=$(date -d "$DEADLINE" +%s)
  if [ "$NOW" -ge "$DL" ]; then echo "$(date '+%F %T') deadline reached" >> "$DIR/runner.log"; break; fi
  NEXT=$("$PY" "$DIR/next.py")
  if echo "$NEXT" | grep -q '"action": "stop"'; then
    echo "$(date '+%F %T') no work remaining" >> "$DIR/runner.log"; break
  fi
  echo "$(date '+%F %T') slice start" >> "$DIR/runner.log"
  hermes chat --oneshot --query-file "$DIR/slice-prompt.md" --in "$REPO" \
    --run-budget 900 --max-turns 80 >> "$DIR/runner.log" 2>&1
  echo "$(date '+%F %T') slice end rc=$?" >> "$DIR/runner.log"
  # Bounded recovery of a dirty tree left by a capped slice: commit only if green.
  if [ -n "$(git status --short)" ]; then
    echo "$(date '+%F %T') dirty tree after slice; running gate" >> "$DIR/runner.log"
    if "$PY" -m pytest tests/test_cli.py -q -p no:cacheprovider >> "$DIR/runner.log" 2>&1; then
      git add kw/ tests/ ops/autopilot-kw/STATE.json \
        && git commit -q -m "feat(kw): checkpoint (auto-recovered slice)" >> "$DIR/runner.log" 2>&1
      echo "$(date '+%F %T') dirty tree committed green" >> "$DIR/runner.log"
    else
      echo "$(date '+%F %T') dirty tree NOT green; stopping for manual review" >> "$DIR/runner.log"
      break
    fi
  fi
  sleep 3
done
echo "$(date '+%F %T') runner exit" >> "$DIR/runner.log"
