# Serving a static site through KuberWeaver

The showcase page in `site/` is deliberately static: no backend, no database, no
runtime dependency. It can be hosted by any web server. This page records how it
can also be deployed **by KuberWeaver itself**, using the existing workload brick
contract, so that a project deployment exercises its own tooling.

This is a documented path, not an observed one. No deployment of this site has
been run and recorded as evidence yet, and nothing below is a claim that it was.

## Why it fits the existing contract

A workload brick already owns, per job:

- a manifest with a JSON Schema (`schema`) and defaults;
- a pinned Ansible role (`implementation.role`, `version`, `license`, `source`);
- the lifecycle `snapshot`, `apply`, `verify`, `rollback`, `remove`;
- `verify.assertions` that must pass on a real target before the job succeeds;
- a document root and a listener port in its configuration.

Serving a directory of static files is exactly that shape: create the root, put
the files there, point nginx at it, probe `GET /`.

The reference brick is `plugins/web-nginx/`. Its `document_root` defaults to
`/var/www/kuberweaver` and its assertions already include `service-active`,
`config-valid`, `http-200` and `brick-header`. A static-site brick is the same
manifest with different defaults and content.

## The gap: publishing content

The execution provider passes a plan as variables to the playbooks
(`kw_config`, `kw_result_path`; see `backend/kuberweaver/workload_execution.py`).
There is **no general content channel**: no field in the manifest schema carries
an arbitrary file tree from the operator to the guest, and no vault binding
exists for file payloads. Confirmed against every brick that is live today:

| Brick | Serves | Content |
| --- | --- | --- |
| `nginx`, `apache` (`ansible-container-v1`) | a container vhost on the GNS3 lab guests | a generated JSON version page written by `_shared/guest.py` |
| `web-nginx` (`ansible-workload-v1`) | a vhost on an existing Ubuntu guest | a generated marker page, `force: false` |
| `local-oci` (`local-runtime-v1`) | busybox httpd on loopback only | a fixed marker |
| `cloudflare-tunnel`, `cloudflare-dns` | the connector on an existing guest | nothing, ingress stays configured in Cloudflare |

So `site/` cannot be published through KuberWeaver without either a new brick or
files placed outside it.

## Retained answer: a dedicated `static-site` brick

Do not modify `plugins/web-nginx/`. It has been reviewed, and its approval is bound
to fingerprints: adding a content field invalidates existing approvals and reopens
the review. Copy it instead:

```
plugins/static-site/
  manifest.yaml          kind workload, capability live, engine ansible,
                         executor ansible-workload-v1, requires compute.vm,
                         data_class config, pinned implementation.role/version,
                         closed schema, lifecycle, verify.assertions
  requirements.yml       the pinned nginx role
  iac/snapshot.yml       existing vhost state and served files
  iac/apply.yml          nginx role + copy of the payload into document_root
  iac/verify.yml         unit active, nginx -t, HTTP 200, and the served bytes
  iac/rollback.yml       restore the previous snapshot, remove what was created
  iac/remove.yml         remove the vhost and the served files
  payload/index.html     the site, administrator-trusted assets
  payload/assets/...
  roles.lock.json        exact role version plus SHA-256, required before apply
```

Install the role once and never commit the downloaded role:

```sh
ansible-galaxy role install -r plugins/static-site/requirements.yml \
  -p plugins/static-site/roles
```

This follows the existing boundary: one reviewed directory with a manifest and
playbooks, no kernel change, and `scripts/validate_preset.py` can chain it after a
provisioning step.

The alternative, extending the executor with a content field (size limits,
resumability, secrets), is rejected: it adds a transfer path to the core for one
use case. Putting base64 payloads into blueprint config is also rejected, because
it breaks the audit model and leaks into plan digests.

## What the brick declares

Beyond the layout above, the manifest needs the fields the workload provider
checks, and nothing else:

- `kind: workload`, `capability: live` (otherwise preparation refuses it);
- `executor` and `execution_provider: ansible-workload-v1`;
- `requires: compute.vm`. This is advisory outside one extension; the real check
  is recorded provisioning evidence;
- a closed schema (`additionalProperties: false`) with at least
  `target_uuid`, `ssh_key_id`, `server_name`, `document_root`, `listen_port`;
- `implementation.role`, `version`, `license`, `source`;
- `lifecycle` naming five playbooks relative to the plugin directory;
- `verify.assertions` that `verify.yml` proves on the guest.

The payload ships inside the plugin directory. It is administrator-trusted
content, like the plugin code itself, and it must be reviewed in the same pull
request.

## Preconditions that are not optional

- A reachable guest, adopted or provisioned by an earlier job, with its UUID
  recorded in the deployment evidence;
- a managed SSH key id issued through the API (`kw keys generate`), selected for
  the operation;
- the Ansible executor installed on the control host (`ansible-playbook`,
  `ansible-galaxy`);
- the brick's roles pinned and present locally; `plugins/*/roles/` is never
  committed, except first-party roles such as `plugins/cloudflare-tunnel/roles/`.

Without those, `snapshot` refuses and nothing is applied. That is the intended
behaviour, not a missing feature.

## Validation without a guest

    python scripts/run_brick.py --brick <id> --job <uuid-like-id> \
        --uuid <vm-uuid> --address <ip> --config @config.json --key <key>

The `prepare` step validates the manifest, the schema, the role lock and the
target evidence before any mutation. `scripts/validate_preset.py` does the same
for a multi-step stack and prints the one-brick jobs in dependency order.

## Honest limits

- The brick serves files. It does not terminate TLS, manage DNS, or obtain a
  certificate. Those belong to a reverse proxy or a tunnel in front of it.
- A deployment is not a backup of the served content. The snapshot records what
  existed before the apply; restoring it restores the previous deployment.
- Nothing here certifies that the site survives a full host rebuild. Test it
  before you rely on it, and keep the source of truth outside the guest.
