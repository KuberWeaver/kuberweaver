# Connections and hypervisors

Connections select a named hypervisor driver and secret source through provider registries. This guide describes the trust model and distinguishes read-only discovery, the blocked blank-disk vSphere path and the separate VMware Ubuntu OVF workflow. None of these labels alone proves successful remote VM creation.

## Kernel and module boundaries

The kernel retains accounts, roles, quotas, requests, approvals, audit and one execution queue. It does not select VMware, Proxmox or Hyper-V through vendor-specific branches. Catalogue forms remain declarative: a compatible service uses an existing executor, while a new transport or lifecycle supplies a provider. Requests needing multiple executors are rejected rather than introducing a second orchestrator.

The documented installation uses PostgreSQL, migrated separately from SQLite without changing module contracts. Secrets remain an explicitly selected, replaceable provider; connection passwords are not stored as plaintext in the application database. No message broker or AI model is mandatory.

- `backend/kuberweaver/connections.py`: connection configuration, references, timestamped observations and API, without a VMware dependency.
- `examples/extensions/esxi-runtime/`: `hypervisor/esxi`, pyVmomi-based read-only discovery. A VM whose annotation starts with `kuberweaver-job:` is reported with `ownership_sha256`, the digest of that annotation, never the annotation text itself; environment-state adoption compares that digest and refuses a VM without it. The extension implements no `destroy_resource`, so a destruction execution is refused with 503 instead of guessing; a write-capable driver must implement `destroy_resource(connection, credentials, uuid, marker)` and return `True` only after it has confirmed the VM is gone.
- `examples/extensions/local-vault/`: optional Fernet secret source for connections explicitly selecting it.
- `examples/extensions/openbao-local/`: separate local Docker vault alternative; its historical acceptance is blocked and its storage retained.
- `examples/extensions/vsphere-provisioning/` and `plugins/vsphere-vm/`: planned, blocked blank-disk saved-plan provisioning.
- `examples/extensions/esxi-ovf/` and `plugins/vmware-vm/`: separate create-only Ubuntu OVF composition; see its guide for prerequisites and acceptance limits.
- `frontend/src/Connections.tsx`: connection configuration, protected credential deposit and explicit discovery actions.
- `frontend/src/DiscoverySchemaFields.tsx`: observation-backed selectors using schema `x-options-source`, without a VMware branch in Canvas.

Disabling an extension must not silently select another provider. Its capability becomes unavailable while unrelated portal functions remain usable.

## Connection workflow

1. Install and explicitly authorize a trusted driver and secret source.
2. In Connections, save a label, driver, server HTTPS origin, secret source and logical secret reference. The initial connection contract accepts explicit IP addresses, not arbitrary URLs or DNS names.
3. If the source explicitly supports writes, a human administrator deposits credentials through the protected form. The password is not returned to the browser or included in the connection, events or MCP.
4. Choose **Test** or **Discover**. Ordinary reads do not initiate network probes.
5. Inspect observation date, revision, server, datastores, networks, pools and VMs. Errors and missing observations remain explicit. An inventoried VM is not proof of a functional guest OS.

Administrators configure connections and deposit credentials. Operators may test/discover. Viewers inspect observations. Agents use the same routes with cumulative role/scope limits; secret-value deposit remains limited to a human administrator session.

## TLS trust and secrets

Without a declared fingerprint, the Python driver uses normal certificate-chain and hostname validation. An administrator-confirmed SHA-256 fingerprint may establish trust for a private or self-signed certificate. It is checked on **each actual TLS socket before HTTP data is sent**, not through a preliminary probe followed by an unverified connection. Confirm certificate changes explicitly; do not auto-update trust.

The Python driver's fingerprint support does not add TLS features to the official OpenTofu provider. That path still requires a valid chain and matching server name. No `allow_unverified_ssl` or bypass proxy is introduced. The separate OVF workflow applies its own explicit fingerprint and transport-isolation controls; see its guide rather than assuming trust settings are interchangeable.

OpenBao is read-only by default. Optional writes are restricted to an administrator-controlled namespace and require exact readback. Values are bounded so serialized JSON remains readable. A vault outage never falls back to plaintext files.

Preserve the historical remote vault, its global binding and its data even when unavailable. A local alternative has its own container, storage and provider and must be selected explicitly. Encrypted lab storage with recovery material under the same local account is not production separation of duties. Fernet encryption does not protect against compromise of that OS account or root. Back up key, authenticated marker and ciphertext together, privately.

## Blank-disk vSphere provisioning: blocked

The versioned official `vmware/vsphere` provider targets new VM creation on standalone ESXi with the required API-write entitlement. Cloning and OVF import are different operations. Do not claim free ESXi licensing permits API writes.

Local checks of **2.18.0** found `power_state` read-only and no `power_on` argument. Its [create workflow](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/vsphere/resource_vsphere_virtual_machine.go) calls `PowerOn`, so it does not meet the powered-off-create contract. The executor refuses preparation and application; no setting bypasses this gate. Creating and then powering off outside the plan is not an acceptable workaround. Real local `version`, `init -lockfile=readonly`, `validate` and `providers schema` checks do not remove that blocker.

The intended saved-plan workflow uses the same queue: non-mutating preparation, public summary/digest, approval, exact-file application and independent readback. Inputs are bounded inventory references and resources, not arbitrary HCL, paths or commands. Credentials are resolved for the subprocess, not written into variables. Plan, state and technical traces remain private. Connection or dependency changes invalidate approval. Interrupted attempts are not automatically retried or destroyed.

`vsphere-vm` remains planned and its provisioner disabled pending real acceptance. Deletion is not universal rollback; it would need a separate plan and authorization. No automatic destruction after ambiguous failure is allowed. This blocker does not relabel the distinct `vmware-vm` OVF path as unsupported or turn it into full CRUD.

## Historical local observations

Earlier worker qualification covered durable manual review of ambiguous interruptions, canonical result capture under lock, notifications outside the lock, and concurrent approval/rollback preservation after a post-publication logging error.

The PostgreSQL migration preserved the initial three accounts, fifteen requests, twenty-nine sessions and one hundred audit events. Verification logins added four audit events in PostgreSQL without changing the earlier accounts/sessions or SQLite. Authenticated HTTP readbacks were recorded after startup. See `POSTGRESQL.md` and private `data/evidence/final-runtime.json`, `postgresql-live-authenticated.json` and `final-live-capabilities.json`. These are historical records, not a live service check.

The Fernet `local-vault` source was installed and enabled locally without changing other activations or the global secrets binding. Its key/marker and 0600 files under a 0700 directory were checked. No real ESXi credential was deposited by that qualification. Explicitly select it for a new connection.

The local OpenBao attempt corrected Docker `NetworkMode="bridge"`/`"default"` handling without weakening image, ownership, mounts or permissions. An uninitialized Raft attempt was privately archived. A later attempt initialized and sealed the vault but did not save recoverable bootstrap material or an application token. A lost initialization response is suspected, not proven. The exact `kw-openbao-v2` container was stopped and initialized Raft retained. Do not repeat initialization or delete Raft to bypass recovery. See `data/evidence/v2-local-bao-recipe.json` and the earlier `v2-local-bao-attempt.json`.

Earlier public ESXi observation at `192.168.1.100` reported ESXi `8.0.3`, build `24280767`, API `HostAgent`. The user confirmed the fingerprint; the driver accepted that public read and rejected a deliberately wrong pin. This did not establish authenticated inventory, licensing, write privileges or VM creation. Credentials were not recovered from conversation history. Browser/HTTP/MCP acceptance used an explicitly named test driver on an isolated real local server: it proves interface controls, not authenticated ESXi access. Private evidence: `data/evidence/v2-esxi-public.json` and `v2-baseline.json`.

## Reproducible checks

Historical post-migration records reported 1,244 passing backend tests and one skip (`redis-server` absent), 235 frontend tests in twelve files, TypeScript/Vite build and `git diff --check`. A 113-code-file source manifest checked stability during that run. Private records: `data/evidence/final-verification.json`, `final-source-manifest.json`, `postgresql-and-vault-tests.xml`. Earlier pre-PostgreSQL evidence reported 1,146 backend passes, one skip and the same frontend count in `v2-backend-tests.xml`. These are **historical counts, not current results**, and do not prove authenticated inventory, VM creation or production maturity.

From the repository root:

```sh
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-execution.txt -r requirements-mcp.txt -r requirements-infrastructure.txt -r requirements-postgresql.txt -r requirements-local-vault.txt
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL KUBERWEAVER_STATE_DIR="$state" KW_TOFU_LOCAL_CHECK=1 .venv/bin/python -m pytest tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Browser acceptance has optional dependencies. Keep disposable default state: importing the ASGI module constructs an application, and sharing one PostgreSQL state between pytest and child servers deliberately triggers the single-controller lock. PostgreSQL fixtures create their own UUID schemas using `data/postgresql/test-url`; that file must select the test database, never the live database.

```sh
uv pip install --python .venv/bin/python -r requirements-acceptance.txt
# If no system Chrome/Chromium is available:
.venv/bin/python -m playwright install chromium
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-browser-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL KUBERWEAVER_STATE_DIR="$state" .venv/bin/python -m pytest tests/test_v2_real_protocol.py -q
```

This test uses disposable state and a test driver, not ESXi credentials or lab resources. Historical deployment acceptance scripts can mutate real targets and must not be treated as unit tests.

## Separate integration work

Hyper-V, Proxmox, cloud, organization Ansible Galaxy catalogues, monitoring/security stacks, Cloudflare and external notifications are separate integrations, not prerequisites for existing connection management. The original acceptance plan is referenced by `plans/2026-11-15-v2-hypervisors.md`; existing contracts remain in `EXTENSIONS.md`, `EXECUTION.md`, `SECRETS.md` and `AGENT_ACCESS.md`.
