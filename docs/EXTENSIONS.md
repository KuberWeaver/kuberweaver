# Extension SDK (protocol v1)

KuberWeaver borrows the **small host + explicit provider registries + lifecycle
hooks + separately installed packages** approach from [Nous Hermes's plugin
architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/).
This is a KuberWeaver protocol, **not Hermes-native API compatibility**. It imports
no Hermes modules. Product integrations belong in standalone extensions, not
vendor-specific branches in the kernel.

## Choose the smallest extension surface

- **A new service using an existing executor:** add a declarative
  `plugins/<id>/manifest.yaml` blueprint and its supported playbook/assets. The
  catalogue schema drives configuration; follow `EXECUTION.md`. No Python
  extension or kernel edit should be necessary for a compatible blueprint.
- **A new backend or observer:** create the Python package described below. The
  host selects registered providers by `(kind, name)`, not vendor `if/else`.
  Builtin consumer kinds include `inventory`, `execution`, `secrets`, `notifications`,
  and `diagnostics`; new lowercase kinds need no SDK change. A kind's provider
  call signature is defined by its consumer, not by the generic registry.

## Package and manifest

```text
my-extension/
  plugin.yaml
  module.py
```

```yaml
id: my-extension
version: 1.0.0
api_version: 1
entrypoint: module.py
capabilities:
  - providers:diagnostics
  - hooks:job_finished
  - data:read-write
```

Required fields: `id`, a semantic-version string `version`, `entrypoint`, and a
list of unique capability strings. Optional fields: integer `api_version`
(default `1`) and string `description`. Unknown fields/unsupported API versions
are rejected. IDs, kinds, names and event names match `[a-z][a-z0-9_-]{0,63}`.
Protocol v1 deliberately fixes the entrypoint to the regular file `module.py`;
absolute paths, traversal, package symlinks and symlinked ancestor directories
are rejected. `module.py` executes as a standalone module without changing
`sys.path`; use `Path(__file__).parent` for shipped resources. No implicit sibling
imports or dependency installation is supplied.

Capabilities are declarations enforced by the registration API:

| Declaration | SDK surface |
| --- | --- |
| `providers:<kind>` | `ctx.register(kind, name, provider, description=None)` |
| `hooks:<event>` | `ctx.add_hook(event, handler)` |
| `data:read-write` | `ctx.plugin_data_dir() -> Path` |

`providers:` and `hooks:` suffixes use the identifier grammar above. Other
capability families are rejected, including wildcards. `ctx.plugin_id` and
`ctx.capabilities` are read-only; `ctx.protocol_version == 1`. Context has no
public `get`, `list`, `emit` or host registry. Registration closes when
`register(ctx)` returns; captured contexts cannot register providers later.
Providers/hooks can continue calling the declared durable-data API.

```python
class Diagnostics:
    def read(self):
        return {"mode": "rules", "read_only": True, "findings": [],
                "summary": {"total": 0, "warning": 0, "critical": 0}}


def register(ctx):
    ctx.register("diagnostics", "my-extension", Diagnostics(),
                 {"label": "My extension", "read_only": True})
    ctx.add_hook("job_finished", lambda payload: None)
```

Descriptions must be JSON-serializable objects without reserved `kind`, `name`
or `provider` fields. Discovery never serializes provider objects or their reprs.
Metadata is copied on both registration and listing.

## Local lifecycle

Run from the application root, using its Python 3.14 environment:

```sh
.venv/bin/python scripts/plugins.py path
.venv/bin/python scripts/plugins.py doctor examples/extensions/lab-notes
.venv/bin/python scripts/plugins.py install examples/extensions/lab-notes
.venv/bin/python scripts/plugins.py list
# Review all source and capability declarations BEFORE this explicit trust action:
.venv/bin/python scripts/plugins.py enable lab-notes --trust
# Restart the app to activate configured changes.
.venv/bin/python scripts/plugins.py doctor lab-notes --execute --trust
.venv/bin/python scripts/plugins.py disable lab-notes
# Restart the app to remove its providers/hooks from the running registry.
```

Global `--data-dir /absolute/path` goes **before** the subcommand; default is
`KUBERWEAVER_DATA_DIR` or `<project>/data`. This must match the app's state directory.
All commands emit JSON. `list`, `path`, installation and default `doctor` do not
import extension code. Default doctor validates the manifest and compiles syntax
without executing it. `--execute --trust` runs registration in a fresh Python
process with temporary copied source/data and a 15-second timeout. It does not
share or mutate the application's registry. **A subprocess is not a sandbox:**
trusted code can still read/write host files or contact the network.

Installation accepts **local directories only**, never URLs/git archives, never
runs pip, and never overwrites an installed ID. Packages are installed disabled.
Configuration writes are locked, atomic, private (`0600`), and read back before
success is reported. `list` status `enabled` describes persisted intent, not
whether a running app has restarted successfully. The CLI does not hot-load code.
The authenticated administrator HTTP enable/disable actions rebuild the registry
only when the execution queue is idle; this is not source upgrade/sandboxing.

```text
data/
  extensions.json             # {"schema_version":1,"enabled":["lab-notes"]}
  extensions/lab-notes/        # Installed, replaceable source
  plugin-data/lab-notes/       # Durable local state, separate from source
```

Disabling or replacing source does not delete plugin data. The data API creates
only its validated ID namespace and refuses pre-existing symlink redirects.
Malformed configuration raises instead of silently enabling anything. Manually
copying a directory into `data/extensions/` is also supported; it remains disabled
until its exact manifest ID is explicitly enabled.

## Host integration contract

The core owns builtin provider instances and approval/RBAC/safety policy. It
constructs one registry at startup, before serving concurrent requests:

```python
from kuberweaver.extensions import ExtensionRegistry, read_enabled

registry = ExtensionRegistry()
# Core registers its actual instances, without putting credentials in descriptions:
registry.register("diagnostics", "rules", rules_provider, {"read_only": True})
registry.register("execution", "ansible", ansible_provider, {"available": True})
reports = registry.load_directory(data_dir / "extensions",
                                  enabled=read_enabled(data_dir / "extensions.json"))
# Publish registry.list() and reports to authorized discovery UI as appropriate.
provider = registry.get("execution", "ansible")
```

`ExtensionRegistry()` has no required constructor arguments:

- `register(kind, name, provider, description=None)` rejects duplicates, including
  attempts to replace a builtin.
- `get(kind, name)` returns the original provider or raises `KeyError`.
- `list(kind=None)` returns JSON-safe `{kind, name, ...description}` records.
- `add_hook(event, handler)` registers synchronous observers.
- `emit(event, payload)` calls observers with a **separate deep copy per hook**;
  returns `{event, hook, status, error_type?}` reports. Exceptions/SystemExit are
  isolated; exception text, payloads and hook return values are not exposed.
- `load_directory(path, enabled=None)` returns per-package manifest/status reports
  including declared capabilities. `None` or an empty set enables **nothing**.
  Invalid siblings do not prevent valid ones from loading. Duplicate manifest IDs
  both fail. Failed registration commits **no providers or hooks**, including
  conflicts with existing providers. Already loaded IDs cannot register twice.

### Selected consumer bindings (actual HTTP/worker dispatch)

Registration advertises a capability; it does not override a builtin or select it.
Authenticated `GET /api/providers` returns:

- `schema_version: 1` and `bindings: {kind: selected_name}`;
- `available: [{kind, name, methods, available, ...safe registration metadata}]`;
- `active: {kind: {kind, name, selected: true, available, status, ...metadata}}`;
- `errors: [{kind?, name?, section?, error}]` for unavailable selected capabilities.

`available` on a descriptor means the synchronous consumer protocol is callable
(and execution source is unchanged), not a claim that remote healthchecks passed.
For example an empty restricted-file source remains `status: unconfigured`.

Administrator `PATCH /api/providers` with the session cookie and `X-CSRF-Token`:

```json
{"bindings":{"diagnostics":"lab-notes"}}
```

merges only the named bindings. Names must be registered, protocol-compatible
lowercase identifiers; import paths/unknown kinds are rejected (`422`). Operators
and viewers cannot change bindings (`403`). Busy execution queues reject changes
(`409`). The full selection is atomically persisted/read back in private
`data/config/providers.json` (or the single existing `.yaml`/`.yml` source):

```json
{"schema_version":1,"bindings":{"inventory":"gns3","diagnostics":"lab-notes","execution":"default","notifications":"local-outbox","secrets":"restricted-file"}}
```

Missing/disabled selected providers **never fall back**. Inventory/diagnostics
routes return `503` only for their unavailable capability; authentication and job
views remain usable. Invalid binding-file schema makes capability dispatch fail
closed without disabling authentication/admin repair. Integrations and Settings
report the selected providers, including `unavailable`, rather than builtin rows.

Consumer signatures (synchronous, validated before selection):

| Kind | Required methods | Consumer |
| --- | --- | --- |
| `inventory` | `read() -> dict` | `GET /api/infrastructure` |
| `diagnostics` | `read() -> dict` | `GET /api/diagnostics` |
| `execution` | `execute(job, emit) -> dict`, `rollback(job, emit) -> dict` | Approved serialized worker |
| `notifications` | `send(payload)` | Canonical lifecycle dispatcher |
| `secrets` | `resolve(reference)` | Internal reference-source contract; no secret-resolution HTTP endpoint |

Inventory returns `nodes`, `links`, `source`, and `errors` plus source provenance.
Diagnostics returns `mode`, `read_only`, `findings` and structured
`summary: {total, critical, warning}`. Execution returns a supported terminal
`status`; admission, approval and source-drift checks remain kernel-owned. The
approved execution name and source fingerprint are persisted in
`approved_execution_provider: {name, provenance}`. A later idle binding change
does not retarget rollback of an older approved job. Missing/changed approved
providers reject rollback and cannot be substituted with the current selection.
The secrets selector does **not** imply OpenBao-to-Ansible credential injection;
see `SECRETS.md` for the actual credential boundary.

### Manual rollback and retry eligibility

Job responses (list/detail, submit/approve/rollback and Overview recent jobs) include
`can_rollback: boolean`. The UI must combine that field with administrator role,
not infer eligibility from `failed`/`rollback_failed` status alone.
`POST /api/jobs/{id}/rollback` requires admin + CSRF and returns the queued job
(`200`); ineligible, already queued or manual-review jobs return `409`.

Successful jobs may request rollback with the unchanged approved provider.
`rollback_failed` retries additionally require persisted matching terminal result,
a `journal.json` for that exact job with `status: rollback_failed`, valid partial
`modified`/`restored` indices and outstanding private snapshot files referenced by
the result. Ordinary failed jobs, absent/inconsistent journals and interrupted
jobs marked `requires_manual_review` cannot be blindly retried. Retry uses the
same serialized worker and pinned provider; the executor still enforces ownership
and skips already-restored entries. Provider/extension changes remain blocked
until worker completion observers finish, not merely until terminal status is
written. Application tests cover automatic and explicit partial-restoration
failure and retry both within one process and across restart.

### Canonical observer lifecycle

The host emits `before_plan`, `after_approval`, `job_started`, and `job_finished`.
Payloads contain only `{event, job_id, status, time, operation, environment}`;
`job_id` is null for validation-only requests. `before_plan` follows request
authentication/schema parsing but precedes admission; hook changes cannot mutate
gates. `after_approval` follows durable approval/Git evidence. `job_started` follows
worker security checks. `job_finished` is emitted once per consumed operation for
returned success/failure, caught provider/security exceptions, and explicit
rollback. Repeated terminal queue items produce no duplicate completion.

Observers are not approval gates: separate copies, ignored return values,
nonfatal exceptions/SystemExit, sanitized audit/warnings. Notification dispatch
resolves the current selected provider for each lifecycle event; missing/failing
notification providers warn without changing deployment status. The builtin
outbox only delivers `job_finished` summaries. There is no implicit outbox fallback.
Calling `load_directory(..., set())` on an existing registry is deliberately
**not an unload API**; disabling creates a fresh registry.

The example `lab-notes` supplies `.read()` diagnostics and records actual worker
`job_finished` IDs/status/time in `data/plugin-data/lab-notes/last-job.json`.
Select it through `/api/providers`, submit/approve a real test job, and read
`/api/diagnostics`: the finding carries that durable event's provenance. With no
observation it returns no findings; it makes no infrastructure health claims.
Disabling the extension removes the capability, leaves the selected name in
config, and makes `/api/diagnostics` explicitly unavailable until selection changes.

## Trust boundary and limits

Capability checks prevent accidental SDK misuse, **not hostile Python**. Enabled
code runs with the application's OS permissions and can bypass Python encapsulation,
access credentials, mutate process globals, block, or call `os._exit`. Registration
rollback covers registry entries/hooks, **not arbitrary file writes or import-time
side effects**. Only install/enable code you have reviewed and trust. Restrict
write access to source/config/state to the service owner. Trust is not cryptographic:
changing enabled source changes what will run next restart; review changes again.
No remote fetching, automatic dependency installation, dependency solver, signature
verification or live upgrade is implemented. Hooks run synchronously: keep them
short and offload slow work to a durable local queue. Operator interrupts still
propagate. For genuinely untrusted code, use a separately secured process/container
with explicit IPC and credentials policy, not this in-process SDK.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -p test_extensions.py -v
.venv/bin/python -m pytest tests/test_provider_bindings.py tests/test_lifecycle_recovery.py -q
```

Tests exercise disabled non-import, duplicate identities, manifest/path/capability
checks, transaction rollback, sibling isolation, copied events, durable state
containment, explicit trust CLI lifecycle, opt-in doctor process isolation, and
example enabled/disabled discovery without changing the core. Consumer tests add
actual authenticated provider selection, persisted restart/disabled fail-closed
routes, real worker event provenance, nonfatal observers, source pinning/drift,
admin/CSRF restrictions and application-level partial rollback retry across
restart with an injected command boundary (no live guest mutations).
