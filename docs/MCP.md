# KuberWeaver MCP: external agents, platform controls

This optional **stdio MCP server** lets an external agent pilot KuberWeaver using
35 typed domain tools. It is not an embedded diagnostic model. The agent's own
model and MCP host are outside KuberWeaver; the adapter makes authenticated HTTP
calls to the existing API. No Hermes configuration is installed or changed.

## Install and launch

Install into the application's virtual environment, **not Hermes's environment**:

```sh
.venv/bin/python -m pip install -r requirements.txt -r requirements-mcp.txt
.venv/bin/python scripts/mcp_server.py \
  --base-url http://127.0.0.1:8000 \
  --token-file /absolute/private/kuberweaver-agent.token
```

The Python SDK is pinned to `mcp==1.30.0`. Its public `FastMCP`,
`ClientSession`, `StdioServerParameters` and `stdio_client` interfaces implement
MCP framing, discovery, validation and structured responses. SDK reference:
<https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0>.

The script resolves the backend relative to itself, so an MCP host may launch it
from any working directory. Use absolute paths for the interpreter, script and
token file in a host configuration. There is no daemon to start separately and
no HTTP MCP listener. Stdout is exclusively MCP protocol traffic; diagnostic
logging goes to stderr. Do not merge stderr into stdout.

### Exact process configuration

| Argument | Environment alternative | Default / policy |
|---|---|---|
| `--base-url ORIGIN` | `KUBERWEAVER_MCP_BASE_URL` | `http://127.0.0.1:8000` |
| `--token-file PATH` | `KUBERWEAVER_MCP_TOKEN_FILE` | Required; **path**, never token value |
| `--allow-remote-https` | None | Off; explicit administrator opt-in |

Only an origin is accepted: no credentials, non-root path, query or fragment.
Plain HTTP requires a numeric loopback address (`127.0.0.1`, `[::1]`, or another
numeric loopback). DNS names including `localhost` are not accepted for HTTP.
HTTPS to a non-loopback origin requires `--allow-remote-https`; normal certificate
and hostname verification stay enabled. A remote API must separately allow the
configured deployment hostname; this flag does not bypass the API's Host policy.
The URL and token path are process configuration, never tool arguments.

## Human-issued access policy

A human administrator first uses the normal authenticated, CSRF-protected
`POST /api/agent-tokens` endpoint to issue a short-lived token with an explicit
role and minimum scopes. The human securely saves the one-time token response to
the private file. Do not put token values in argv, environment variables, MCP
host JSON, shell history, documentation, prompts or source control. This adapter
has **no token-issuance, revocation, session-login or secret-retrieval tool** and
cannot self-enrol or upgrade its privileges.

The token file must be a regular file owned by the adapter's OS user, with no
group/other permission bits (typically mode `0600`). Symlink token files are
rejected. At most 4096 ASCII non-whitespace token characters are accepted, with
surrounding file whitespace stripped. The file is checked at startup and reopened
for every request, so an administrator can rotate it atomically. The API checks
expiration and revocation on every call. Avoid granting the adapter write access
to administrator configuration or the directory holding its token.

Discovery always lists the complete tool catalogue. Listing a tool **does not
grant permission**. Both the token's role and its scope must permit the actual
HTTP endpoint; the real API returns 403 otherwise. Roles cannot be escalated by
passing a tool parameter. Human-only token management is intentionally absent.

### Tool catalogue and minimum access

Every read tool has `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`. Every action has `readOnlyHint=false` and
`idempotentHint=false` (no automatic mutation retry). Conservatively, all actions
except plan validation and read-only connection discovery have `destructiveHint=true`, including blueprint storage,
configuration, potentially billable diagnostics and external delivery. Tools also
publish `_meta` keys `kuberweaver/access` (`read`/`write`) and
`kuberweaver/dangerous` (boolean). These are host hints, not authorization gates.

| Tools | Required scope | Minimum platform role |
|---|---|---|
| `list_plugins` | `read:catalog` or broad `read` | viewer |
| `get_overview` | `read:overview` or broad `read`, includes recent jobs and costs | viewer |
| `get_topology` | `read:infrastructure` or broad `read` | viewer |
| `list_connection_drivers`, `list_connections`, `get_connection` | `read:infrastructure` or broad `read`; cached metadata only | viewer |
| `discover_connection` | `read:infrastructure` or broad `read`; explicit remote observation | operator |
| `create_connection`, `update_connection` | `configure`; references and restricted endpoint configuration only | admin |
| `get_settings` | `read:settings` or broad `read` | viewer |
| `get_finops` | `read:finops` or broad `read` | viewer |
| `get_diagnostics_cache` | `read:diagnostics` or broad `read` | viewer |
| `list_audit` | `read:audit` or broad `read` | viewer |
| `list_integrations`, `list_providers`, `list_extensions` | `read:integrations` or broad `read` | viewer |
| `list_jobs`, `get_job` | `read:jobs` or broad `read` | viewer |
| `list_blueprints`, `get_blueprint` | `read:blueprints` or broad `read` | viewer |
| `get_operations`, `get_notification_deliveries` | `read:operations` or broad `read`; `notify` alone does not grant inspection | admin |
| `validate_plan` | `plan` | viewer |
| `save_blueprint`, `submit_job` | `submit` | operator |
| `approve_job`, `reject_job` | `approve` | admin |
| `rollback_job` | `rollback` | admin |
| `update_settings`, `update_provider_bindings` | `configure` | admin |
| `enable_extension`, `disable_extension` | `extensions` | admin |
| `run_diagnostics` | `diagnose` | operator |
| `retry_notifications` | `notify` | admin |
| `issue_chatops_action` | `chatops` | admin |

Viewer tokens may have any supported `read:*`, broad `read`, or `plan`; operator tokens may additionally have
`submit`/`diagnose`; administrator tokens may be issued any explicit supported
scope. Prefer separate planner and human-controlled approver identities. An
administrator-role token restricted to `read`, `plan`, `submit` still **cannot
approve** a pending job. Administrator approval scopes are sensitive authority,
not a substitute for a human decision in an agent host.

## Typed client contract

Call `tools/list` for authoritative input/output JSON Schemas. Top-level arguments
and fixed domain envelopes are closed (`additionalProperties=false`); required
parameters cannot be omitted, and strings are not coerced into numbers/booleans.
Plugin node `config` remains a JSON object because each discovered manifest owns
its schema; platform validation, unsafe-field checks and admission still apply.

* No-argument read tools, `run_diagnostics`, `retry_notifications`: `{}`.
  `get_notification_deliveries` reads the API's safe delivery records; it never
  requests webhook payloads, private destinations or credentials. An older API
  without that optional inspection route returns a sanitized 404.
* `get_job`, `get_blueprint`, `get_connection`, `discover_connection`, `approve_job`, `reject_job`, `rollback_job`:
  `{"identity": "<32 lowercase hexadecimal characters>"}`.
* `validate_plan`, `submit_job`: `{"graph": <Graph>}`.
* `save_blueprint`: `{"name": "Blueprint name", "graph": <Graph>}`.
* `update_settings`: `{"settings": {"policies": {...}, "pricing": {...}}}`.
  Supply one or both sections. Policy/pricing fields use the platform's typed
  models and bounds; only supplied fields are sent, not model defaults.
* `update_provider_bindings`: `{"bindings": {"inventory": "registered-name"}}`.
  Allowed keys: `inventory`, `diagnostics`, `execution`, `notifications`, `secrets`.
  This selects a registered provider; it never reads a secret value.
* `enable_extension`: `{"identity": "installed-extension", "trusted": true}`.
  Explicit literal `true` is required: enabling trusted Python code is dangerous.
* `disable_extension`: `{"identity": "installed-extension"}`.
* `create_connection`: `{"connection": <ConnectionSpec>}`. The closed definition
  includes `label`, registered `driver`, restricted HTTPS `endpoint`, logical
  `secret_ref`, registered `secrets_provider`, optional `tls_sha256`, and `enabled`.
  No password or arbitrary connection configuration is accepted.
* `update_connection`: `{"identity": "<32 lowercase hexadecimal characters>",
  "patch": {"revision": 1, "enabled": false}}`. Only supplied fields are patched;
  the exact current revision is required. Credential ingestion remains a human-only
  API workflow and is intentionally absent from MCP.
* `issue_chatops_action`: `{"job_id": "<32 lowercase hexadecimal characters>",
  "decision": "approve"}` (or `"reject"`). This issues the existing bounded
  action descriptor; it does not forge a callback signature or bypass approval.
  Uninstalled/unconfigured ChatOps fails through the real API (404/503).

Successful tool results have MCP `structuredContent`:

```json
{"data": {"platform": "response shape varies by domain"}, "request_id": "request correlation identifier"}
```

`data` is the original JSON object/array/scalar, not a string containing JSON.
FastMCP also supplies compatible text content. MCP errors have `isError=true` and
a sanitized fixed message. Upstream response bodies, headers, exception strings
and validation input values are never included in error messages. For example,
403 becomes `Permission denied (HTTP 403; request ...)`; optional capability
absence, conflicts, validation rejection and rate limits remain distinguishable
by status without exposing provider secret details.

A Python host using the pinned SDK can connect as follows (no token value):

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command="/absolute/kuberweaver/.venv/bin/python",
        args=["/absolute/kuberweaver/scripts/mcp_server.py",
              "--base-url", "http://127.0.0.1:8000"],
        env={"KUBERWEAVER_MCP_TOKEN_FILE": "/absolute/private/kuberweaver-agent.token"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("list_plugins", {})
            if result.isError:
                raise RuntimeError("KuberWeaver denied or could not complete the call")
            catalogue = result.structuredContent["data"]

asyncio.run(main())
```

## Controls and boundaries retained

* No arbitrary URL/path/HTTP request, local file, shell, SSH, provider-secret,
  model invocation or raw signed-ChatOps callback tool exists.
* Graph admission, target allowlists, plugin schemas, quotas, cost estimates,
  approval state, canary policy, runtime queue safety, provenance/drift checks and
  rollback snapshots/journals remain inside the platform. Tools call normal API
  routes, not execution internals. Approval is never implicit.
* `get_diagnostics_cache` only uses the read route. Only `run_diagnostics` requests
  a run, retaining API concurrency, cooldown and provider budgets. Notifications
  retry only on the explicit retry tool.
* API requests carry a freshly generated `X-Request-ID` and fixed
  `X-KuberWeaver-Client: mcp-stdio`; these are correlation metadata, not credentials
  or authorization claims. Returned `request_id` is adapter-generated; whether
  middleware persists it in HTTP logs is a deployment concern. Domain audit
  attribution comes from the API's authenticated `agent:<token-id>` principal.
* HTTPX ignores proxy and certificate environment configuration (`trust_env=false`),
  never follows redirects, has a 5-second connect / 20-second I/O timeout and a
  25-second total deadline. Decoded JSON response bodies are capped at 1 MiB.
  Responses exceeding the cap or invalid JSON fail closed. No automatic retry.
* A timeout, transport failure or oversized response **does not establish that a
  mutation failed**. Inspect jobs/audit/blueprints before retrying. MCP cancellation
  does not cancel already submitted jobs; poll `get_job` for state and stored
  events. An unbounded SSE forwarding tool is intentionally absent.
* Browser auth/token-management routes, raw metrics and health probes are not
  domain tools. Existing job events are available through `get_job`. No resource
  or prompt exposes the adapter's configuration or token.
* Successful API data is trusted only to the same extent as the API's existing
  redaction contract. Job logs, blueprint names and plugin descriptions are
  untrusted content to a model; do not treat their text as instructions.

## Verification

```sh
.venv/bin/python -m pytest tests/test_mcp_server.py -q
```

Tests include a real SDK `ClientSession` talking over stdio to a separate adapter
process and an isolated real KuberWeaver **Uvicorn subprocess**, with temporary
state/plugin directories. A human session issues a scoped bearer through the real
API. The protocol discovers 35 tools, reads the catalogue, validates a graph,
persists and reads back a blueprint, submits a production job, observes pending
approval, receives 403 on approval without `approve` scope, verifies no state
transition, and observes 401 after human revocation in the same MCP session.
Other tests cover invalid credentials, every tool's exact route/body/annotations,
closed input schemas, private-file handling, URL policy, deadline, response cap,
redirect refusal and sanitized upstream errors. No live state, secrets or actual
infrastructure are used by these tests.
