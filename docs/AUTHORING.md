# Authoring a service or an extension

Two ways to add a capability. Pick by what you are adding, not by preference.

| You want to | Put it in | Manifest |
| --- | --- | --- |
| Deploy reviewed software on an existing guest (nginx, PostgreSQL, a tunnel) | `plugins/<id>/` | `manifest.yaml`, kind `workload` |
| Add a provider, hook or MCP tool to the kernel without deploying anything | `examples/extensions/<id>/` | `plugin.yaml`, protocol v1 |

Adding a service never means editing the kernel. Adding a provider never means
adding a plugin directory with Ansible playbooks.

## Shortest path: copy the reference brick

`plugins/web-nginx/` is the reviewed reference. Copy the directory, then change
only what the manifest says:

```sh
cp -r plugins/web-nginx plugins/my-service
ansible-galaxy role install -r plugins/my-service/requirements.yml \
  -p plugins/my-service/roles
```

Then edit, in this order:

1. `manifest.yaml`: `id`, `name`, `version`, `description`, `category`,
   `kind: workload`, `capability`, `engine`, `executor`, `execution_provider`,
   `requires`, `data_class`, `implementation` (role, exact version, license,
   source, reason), `schema`, `defaults`, `lifecycle`, `verify.assertions`.
2. `iac/snapshot.yml`, `apply.yml`, `verify.yml`, `rollback.yml`, `remove.yml`:
   the five lifecycle steps, paths relative to the plugin directory.
3. `README.md`: the contract and the refusals, so a reviewer sees them without
   reading the playbooks.
4. Tests for the refusal cases: unknown target, missing provisioning evidence,
   schema violation, mismatched role version, absent executable.

Register nothing. The catalogue loads every directory that has a `manifest.yaml`.

## What the workload provider checks before anything reaches a guest

- the manifest parses, its `id` matches the directory name, and the path is not a
  symlink;
- `kind: workload`;
- `executor` equals `ansible-workload-v1`;
- `implementation.role` and `implementation.version` are set, with a plain
  version string;
- `lifecycle.snapshot`, `apply`, `verify`, `rollback` exist and each resolved
  path stays inside the plugin directory (an escaping playbook is refused);
- `data_class` is declared;
- `verify.assertions` is non-empty and every entry has an `id`;
- the pinned role is installed and `roles.lock.json` matches its version;
- the submitted config validates against the closed JSON Schema;
- `target_uuid` matches a succeeded provisioning job in the same environment,
  which recorded a UUID, a guest IP and a powered-on state;
- `ssh_key_id` names a managed key that exists and is enabled.

`remove` is not checked at preparation time. Declare it anyway: a brick without
removal is one the operator cannot clean up.

If any of these fails, the plan is refused and no SSH connection is attempted.

## Verify assertions are the acceptance criteria

Each entry in `verify.assertions` must be proven on the guest by `verify.yml`
and read back by the runner: the observed set must equal the declared set, and
every value must be true. Assert the served bytes, not just a 200 status, when
the content matters. A brick whose assertion can pass by accident is not
reviewable.

## Naming the pieces

Four words are used for three things, so they are fixed here:

- **brick** (`plugins/<id>/`) is what deploys something;
- **extension** (`examples/extensions/<id>/`) is Python code that extends the
  kernel through the SDK;
- **module** is a studio artifact: a pinned OCI image plus an offering. It is
  metadata, not a deployment;
- **catalogue entry** is one row of the service list, which comes from either a
  brick or an extension.

## Tests and gates

See `CONTRIBUTING.md`. In short: `python3 ops/ci/check.py --fast` locally, and
the same three CI jobs on the pull request. A new brick must add tests, not only
a manifest.
