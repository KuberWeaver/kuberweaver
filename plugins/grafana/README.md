# grafana

Grafana brick for an existing Ubuntu guest. It wraps the pinned
`grafana.grafana.grafana` role (official Grafana Labs collection 6.1.0,
GPL-3.0-or-later) and serves the UI on the configured port (default 3000).

## Contract

| Field | Value |
| --- | --- |
| kind | `workload` |
| requires | `compute.vm` |
| executor | `ansible-workload-v1` |
| data class | `config` |

Inputs: `http_port` (1025-65535, default 3000), `admin_user` (default `admin`),
`grafana_version` (`latest` or an exact `x.y.z`, default `latest`), plus the two
portal inputs `target_uuid` and `ssh_key_id` (both required).

## Secrets

The admin password is generated on the guest at apply time (32 random
alphanumeric characters) and stored in `/etc/kuberweaver/grafana/admin.password`
(root:root, 0600, directory 0700). It is reused on re-apply, passed to the role
through a `no_log` fact, and never enters a plan, a report, argv or git. The
rendered `/etc/grafana/grafana.ini` (root:grafana, 0640) carries it because
Grafana reads its admin credentials from there; snapshot/rollback tasks that
touch that file are `no_log`.

## Lifecycle

    snapshot   package, unit, configuration, secret and repository presence
    apply      grafana.grafana.grafana installs the Grafana apt repository, the package and the service
    verify     unit active, port open, /login returns the Grafana page, ini port matches, secret 0600
    rollback   restore the previous configuration, remove what did not exist before
    remove     stop the server, remove package, configuration, repository and secret; keep the data

The brick owns its package, configuration, apt repository and generated secret.
`/var/lib/grafana` (dashboards, internal database) and `/var/log/grafana` stay on
the guest, mirroring how `zabbix-server` keeps the `db-postgresql` database.

## Install the pinned collection

    ansible-galaxy collection install -r plugins/grafana/requirements.yml \
        -p plugins/grafana/collections

The collection must sit beside the lifecycle playbooks because the isolated
executor sets no global collection path. After installation, dereference any
symlinks inside `collections/` (the catalogue fingerprint forbids them) and
record the role checksum in `roles.lock.json` (sha256 over the sorted relative
paths and bytes of `collections/ansible_collections/grafana/grafana/roles/grafana`).
The brick refuses to run when the installed role does not match the locked
version, when a declared assertion is not reported, or when the configuration
names a field the schema does not declare.

## Why this role

`grafana.grafana` is the official Grafana Labs collection: it installs the
signed apt.grafana.com repository (deb822 on Ubuntu 24.04), the package and the
systemd unit, and renders `grafana.ini` from a bounded variable tree. The
datasource/dashboard/API-key provisioning paths of the role are left empty here;
the `grafana-dashboard` track provisions those as code on top of this brick.