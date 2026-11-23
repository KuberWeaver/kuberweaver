# web-nginx

Reference workload brick. It wraps the pinned upstream `geerlingguy.nginx` role
(3.3.1, MIT) and serves one virtual host on an existing Ubuntu guest.

## Contract

| Field | Value |
| --- | --- |
| kind | `workload` |
| requires | `compute.vm` |
| executor | `ansible-workload-v1` |
| data class | `config` |

Inputs: `server_name` (required), `listen_port` (default 80),
`document_root` (default `/var/www/kuberweaver`), `proxy_pass` (optional), plus
the two portal inputs `target_uuid` and `ssh_key_id` (both required).

## In the portal

The brick is submitted as a one-node job. Preparation validates this contract,
resolves `target_uuid` against the deployment evidence of a succeeded
provisioning job, and saves a plan the administrator approves; execution stages
the managed SSH key, runs snapshot, apply and verify, and records the declared
assertions in the job result. See the README for the full path, the
refusals and the evidence layout.

## Lifecycle

    snapshot   package, unit and existing site state
    apply      role installs nginx, writes kuberweaver-<server_name>, reloads
    verify     unit active, nginx -t, HTTP 200, X-KuberWeaver-Brick header
    rollback   restore the previous site, remove what did not exist before
    remove     drop the site and the served files, keep the package

## Install the pinned role

    ansible-galaxy role install -r plugins/web-nginx/requirements.yml \
        -p plugins/web-nginx/roles

Then record the resolved version and checksum:

    python scripts/run_brick.py --brick web-nginx --job lock-01 \
        --uuid <vm-uuid> --address <ip> --config '{"server_name":"app.lab"}' \
        --key <private-key> --lock-role

The brick refuses to run when the installed role does not match the pinned
version, when a declared assertion is not reported, or when the configuration
names a field the schema does not declare.

## Run it

    python scripts/run_brick.py --brick web-nginx --job app-01 \
        --uuid <vm-uuid> --address <ip> --config '{"server_name":"app.lab"}' \
        --key <private-key>

Add `--remove` to reverse it. Evidence is written to
`data/plugin-data/workloads/<job>/evidence.json`, mode 0600.

## Why this role

`geerlingguy.nginx` is actively maintained. The vendor collection
`nginxinc.nginx_core` has had no release in roughly three years, so it is not the
safer default despite being official.
