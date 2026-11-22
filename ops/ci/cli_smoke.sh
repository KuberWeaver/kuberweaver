#!/usr/bin/env bash
# Small end-to-end check of the installed `kw` binary against a real server.
#
# Unlike the pytest suites, this drives the console script over HTTP with a clean
# configuration directory, exactly like an operator. It uses throwaway state, a
# temporary port and the repository plugin catalogue, and it must finish well
# under a minute. Run it from anywhere:
#
#   ops/ci/cli_smoke.sh
#
# Exit code 0 means every command returned its documented code and the whole walk
# stayed inside the time budget.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
KW="${KW:-$ROOT/.venv/bin/kw}"
if [ ! -x "$KW" ]; then
  echo "kw not found at $KW; install with: uv pip install --python .venv/bin/python -e . --no-deps" >&2
  exit 1
fi

STATE="$(mktemp -d "${TMPDIR:-/tmp}/kw-smoke.XXXXXX")"
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$STATE"
}
trap cleanup EXIT

PORT="$("$PYTHON" - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
)"

GRAPH="$STATE/graph.json"
cat > "$GRAPH" <<'JSON'
{
  "name": "CLI smoke",
  "environment": "dev",
  "nodes": [
    {"id": "nginx", "plugin_id": "nginx", "label": "Web", "position": {"x": 1, "y": 1},
     "config": {}, "price_monthly": 12.5, "currency": "EUR"}
  ],
  "edges": [],
  "strategy": {"canary": 1, "batch_size": 1}
}
JSON

echo "initialising a throwaway local vault (state: $STATE)"
if ! vault_output="$("$PYTHON" scripts/setup_local_vault.py --data-dir "$STATE/data" --install 2>&1)"; then
  echo "local vault setup refused: $vault_output" >&2
  exit 1
fi

echo "starting a throwaway server on 127.0.0.1:$PORT"
PYTHONPATH="$ROOT/backend" KUBERWEAVER_STATE_DIR="$STATE/data" \
  KUBERWEAVER_PLUGINS_DIR="$ROOT/plugins" \
  "$PYTHON" -m uvicorn kuberweaver.app:app --host 127.0.0.1 --port "$PORT" \
  --log-level warning >/dev/null 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 100); do
  if "$PYTHON" - "$PORT" <<'PY'
import sys, urllib.request
try:
    urllib.request.urlopen(f'http://127.0.0.1:{sys.argv[1]}/healthz', timeout=1).read()
except Exception:
    sys.exit(1)
PY
  then break; fi
  sleep 0.1
done

export KW_CONFIG_DIR="$STATE/kw"
export KW_URL="http://127.0.0.1:$PORT"
export KW_PASSWORD="${KW_SMOKE_ADMIN_PASSWORD:-admin}"
unset KW_AGENT_TOKEN || true

step() {
  local label="$1"; shift
  printf '%s ... ' "$label"
  if "$KW" "$@" >/dev/null; then
    echo ok
  else
    echo "FAILED ($label: kw $*)" >&2
    exit 1
  fi
}

started="$(date +%s)"
step "login" auth login --username admin
step "whoami" --json auth whoami
# The local vault is a trusted extension; binding it makes key generation possible.
step "bind the local vault" providers bind --kind secrets --name local-vault
step "generate a key" --json keys generate --name smoke-key
step "list keys" --json keys list
step "connector drivers" --json connectors drivers
step "provider bindings" --json providers list
step "validate a priced graph" --json deploy validate --graph-file "$GRAPH"
step "finops" --json finops
step "inventory" --json inventory list
step "logout" auth logout
elapsed="$(($(date +%s) - started))"

"$PYTHON" - "$elapsed" <<'PY'
import sys
seconds = int(sys.argv[1])
if seconds >= 60:
    sys.exit(f'the CLI walk took {seconds}s, over the 60s budget')
print(f'walk finished in {seconds}s')
PY
