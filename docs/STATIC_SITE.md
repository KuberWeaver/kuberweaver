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
exists for file payloads. `web-nginx` therefore writes a generated marker page
and relies on the upstream role for the rest.

Three options, in order of preference:

1. **Upstream role that fetches the site itself.** Use a role or collection that
   pulls from a pinned source (a tarball URL plus checksum, or a git ref). The
   brick then only declares the source and the pin. This matches the existing
   `implementation` block and needs no kernel change.
2. **A small new brick with its own pinned role** that installs the payload from
   a checksummed archive published alongside the release. Same contract, one
   more plugin directory, no kernel edit.
3. **Extend the executor with a content field.** Rejected for now: it would add
   a transfer path (size limits, resumability, secrets) to the core for one use
   case, which is exactly what the plugin boundary exists to avoid.

Do not invent a fourth option that puts base64 payloads into blueprint config.
It breaks the "one topic per commit" audit model and leaks into plan digests.

## What a static-site brick would declare

Copy `plugins/web-nginx/manifest.yaml` and change:

- `id`, `name`, `description`, `version`;
- `document_root` default, typically `/var/www/<site-id>`;
- `defaults.listen_port`, chosen inside the allowed range for the target;
- `verify.assertions`, keeping at least an HTTP readback assertion. Assert the
  served bytes, not just a 200 status, if the content matters;
- `implementation`, naming the pinned role that performs the fetch.

The site payload then comes from the role's own pinned requirement, never from a
commit in this repository.

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
