# PostgreSQL storage and recovery

The documented local installation was migrated to the `kuberweaver` database. Independent readback matched the SQLite rows, historical order and fingerprints. Historical checks covered the selected backend, PostgreSQL controller lock, portal HTTP 200 and unauthenticated private-route HTTP 401. These are migration records, not a fresh health check. SQLite and its backups were retained intact. Never include secrets in API responses, displayed commands or reports.

Private import evidence: `data/evidence/postgresql-cutover.json`. The native archive `data/backups/postgresql-after-import.dump` was created with mode 0600; `pg_restore --list` could read its nine-table data catalogue. **Catalogue inspection is not a complete restore test.**

## Documented local infrastructure

- PostgreSQL **18.7**, pinned official image: `postgres@sha256:2ea8630df176ad090ec92136dee850397c5af8781e4bcf2e4162593691c61bbc`.
- Project-owned container: `kw-postgresql-v2`.
- Dedicated persistent volume: `kw-postgresql-v2-data`.
- Loopback publication only: `127.0.0.1:15433`.
- Separate databases: `kuberweaver` for migrated state and `kuberweaver_test` for schema-isolated tests.
- Application role owns these databases without superuser, database-creation, role-creation or replication privileges.
- Client dependency in `.venv`: `psycopg[binary]==3.3.9`.

Loopback connections are a lab configuration without end-to-end TLS. This single-host setup is not HA and does not authorize multiple workers.

## Private configuration

Connection URLs are stored in `data/postgresql/target-url` and `data/postgresql/test-url`, mode 0600 under a 0700 directory. Do not display their contents. Administrative bootstrap is private and is not the API account.

The active URL file is `data/config/database-url`, mode 0600, explicitly selected by `KUBERWEAVER_DATABASE_URL_FILE` in the local service. It was installed after migration and verification. An invalid PostgreSQL configuration or unavailable server must never silently fall back to SQLite.

## Data preservation and backups

A consistent SQLite backup, obtained with its backup API and checked with `integrity_check`, is recorded in:

`data/backups/pre-postgresql-20261115T214121Z/manifest.json`

The manifest preserves table counts and fingerprints. The source contained three accounts, fifteen requests, twenty-nine sessions and one hundred audit events. Preserve password hashes, IDs, sessions, settings and journals; reseeding accounts is not migration.

The backend changed without redesigning business objects. Existing JSON documents keep their contracts; migration does not introduce another orchestrator or universal CMDB schema. Notifications also use PostgreSQL when selected, without a new SQLite file. The detailed historical plan is `plans/postgresql-cutover.md`.

Back up PostgreSQL as well as private files, snapshots and vault material. Backing up `data/` alone does not capture the database volume. Test restoration before durable operation.

## Explicit rollback, never automatic

The retained SQLite backup was imported and verified in a disposable PostgreSQL database and exercised over HTTP. The original SQLite database represents the migration instant, not subsequent PostgreSQL writes.

1. Stop the portal with `systemctl --user stop kuberweaver.service` and keep a current PostgreSQL backup before deciding.
2. Compare PostgreSQL state with migration evidence. If writes occurred afterward, do not blindly resume SQLite: those writes would be lost. Reverse PostgreSQL-to-SQLite migration is not implemented; controlled export and reconciliation are required.
3. Reverting to the pre-migration snapshot requires explicitly choosing that old state, moving `data/config/database-url` to a retained private location and explicitly removing PostgreSQL selection variables from the service. Its environment deliberately requires `KUBERWEAVER_DATABASE_URL_FILE`: deleting the file alone makes startup fail rather than enabling SQLite.
4. Verify accounts and requests in the selected state before reopening the portal. Do not delete the PostgreSQL volume or backups to perform rollback.

## Startup and vault boundary

`ops/kuberweaver.service`, also used by the documented local user service, explicitly selects the private PostgreSQL URL file. It does not implicitly start the old remote vault tunnel. This does not change provider bindings or the historical vault configuration.

The optional Fernet `local-vault` extension is documented in `../examples/extensions/local-vault/README.md`. Its key does not protect against access to the OS account or root. ESXi discovery and provisioning are separate capabilities; storage migration is not evidence of an authenticated discovery or VM creation.
