# Configuration

KuberWeaver reads its process configuration from the environment. Every setting
has a default that works for a source checkout, so no export is required to run
tests or start the service locally. Values are read when they are used, never
cached at import time except where a module intentionally keeps a path constant.

Backend code contains no developer-specific absolute path. A deployment that
differs from the checkout layout selects its paths with the variables below.

## Settings

| Variable | Default | Required | Purpose |
| --- | --- | --- | --- |
| `KUBERWEAVER_STATE_DIR` | `<checkout>/data` | No | Durable state: database selection, jobs, audit, ownership, plugin data. Created with mode `0700` if absent. |
| `KUBERWEAVER_PLUGINS_DIR` | `<checkout>/plugins` | No | Reviewed plugin manifests and playbooks served to the portal. |
| `KUBERWEAVER_FRONTEND_DIR` | `<checkout>/frontend/dist` | No | Built portal assets. When absent, the API still runs and asset requests return 404. |
| `KUBERWEAVER_SSH_CONFIG` | `~/.ssh/config` | For execution and OpenTofu runs | OpenSSH client configuration holding the aliases `infra-app`, `infra-branch-client` and `infra-hypervisor`. |

Existing database and MCP settings keep their documented behaviour:
`KUBERWEAVER_DATABASE_URL_FILE`, `KUBERWEAVER_DATABASE_URL`,
`KUBERWEAVER_MCP_BASE_URL` and `KUBERWEAVER_MCP_TOKEN_FILE`. See
[PostgreSQL](POSTGRESQL.md) and [MCP](MCP.md).

## Input rules

- An explicit path must be absolute. A relative value is refused with
  `ConfigurationError` naming the variable, because a supervised process does
  not have a predictable working directory.
- A blank value is treated as unset and falls back to the default.
- `~` expands to the current home directory.
- `KUBERWEAVER_SSH_CONFIG` must point to a regular file. The executor and the
  OpenTofu boundary check it before running any remote command and refuse with a
  message that names the variable and the path.

## Example

```bash
export KUBERWEAVER_STATE_DIR=/var/lib/kuberweaver
export KUBERWEAVER_SSH_CONFIG=/etc/kuberweaver/ssh_config
export KUBERWEAVER_PLUGINS_DIR=/opt/kuberweaver/plugins
PYTHONPATH=backend .venv/bin/uvicorn kuberweaver.app:app --host 127.0.0.1 --port 8088
```

The SSH configuration is an operator input, not application state. Keep it
outside the repository, together with its `known_hosts` file, and restrict it to
the service account that runs KuberWeaver.
