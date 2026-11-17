# Architecture direction and plugin lifecycle

This document separates **delivered architecture** from **proposed lifecycle and isolation work**. See the README and `COMPLETION_STATUS.md` for capability boundaries, and `POSTGRESQL.md` for the completed local storage migration. Design proposals do not establish distributed execution or third-party code isolation.

## Platform choices

- React/TypeScript portal and Python/FastAPI API. PHP/Laravel and Node.js are alternatives, not selected migrations.
- PostgreSQL is supported and selected by the documented local service; SQLite remains a retained source and standalone option, not an automatic fallback.
- Keep a modular kernel and separate execution concerns from portal requests. Microservices, Kubernetes and Redis are not mandatory dependencies.
- Use an internal catalogue of reviewed, trusted packages rather than a public executable-code marketplace.

## Service lifecycle: proposed model

Distinguish a **request** (desired change), an **execution** (one attempt) and a **service** (the delivered resource that remains managed). A complete lifecycle needs authorized create/read/update/delete, service-specific backup/restore and periodic desired/observed comparison. The current create-only VMware path does not imply that full lifecycle is delivered.

| Domain | Proposed contents |
| --- | --- |
| Access | Users, teams, projects, roles and agent grants; separate organizations only if required. |
| Catalogue | Versioned templates, parameter schemas, capabilities and approved extensions. |
| Requests | Configuration, exact plan, estimate and approval decisions. |
| Services | Ownership, template version, desired/observed state and actual resource IDs. |
| Execution | Jobs, steps, attempts, reservations, checks and recovery. |
| Operations | Audit, notification outbox, backup references and cost data. |

Private keys and passwords belong in a vault; the database stores references. Large backups and logs may use controlled file/object storage.

## Concurrency and recovery

SQLite permits one write transaction per file. An in-process Python lock does not coordinate multiple servers. PostgreSQL supports stronger coordination primitives, but a database migration alone does not make the application a reliable distributed system. The current controller remains serialized.

Further work requires explicit tests for atomic job reservation, incompatible-service locks, fencing of interrupted workers, idempotency, infrastructure readback before retry and manual review for ambiguous states. A PostgreSQL queue is a possible starting point, subject to concurrency tests. Test restoration of every required state store, not just backup creation.

A SQL transaction cannot undo a provider resource. Each integration must define recovery and compensation; deletion is not a universal rollback.

## Three extension surfaces

| Type | Use | Boundary |
| --- | --- | --- |
| Declarative service template | Parameters and procedures using an existing engine. | Compatible templates need no kernel or React business logic. |
| Executable plugin | New inventory, execution, secrets, notification or diagnostic provider. | Explicit family contract, dependencies and privileges. |
| Specialized interface | Console/editor beyond a standard schema form. | Separate UI contract and implementation; no automatic third-party JavaScript loading. |

The SDK exposes versioned contracts, inputs, results, errors and operations. Plugins should not depend on kernel internals or write its tables directly. Platform authorization and approval remain mandatory. Generic registry membership does not prove a provider satisfies its consumer's interface. Current Python extensions are trusted in-process code, not isolated code.

## Packages and publication

Packages should declare ID, version, compatibility, maintainer, dependencies and actual capabilities, plus parameters, procedures, tests, prerequisites and limitations. Unsupported operations remain unavailable. Declarative templates and Python extensions retain distinct formats documented in `EXECUTION.md` and `EXTENSIONS.md`.

Proposed distribution: Git sources → automated checks and review → immutable package → catalogue publication → authorized installation. A repository with one directory per package is sufficient initially. OCI is an optional later distribution format. Current installation accepts local directories; registry downloads and upgrades are not delivered.

Service records should retain package version/digest and engine provenance. Publication must not silently upgrade existing services. Upgrades need compatibility checks and approval; an older package does not guarantee data restoration. Keep replaceable code separate from persistent plugin data. Disabling a package for new use does not delete existing services; continued management, migration or retirement requires a defined path.

## Maintenance and safety

Use common tests for configuration, contracts, errors, activation and recovery, plus real target-specific acceptance. Two web servers do not prove a persistent database lifecycle. Check manifests, secrets, dependencies and tests before publication; report the exact validation scope rather than an unjustified security badge.

External executable packages would require effective file/network/secret/resource limits. A separate process alone is not a security sandbox. Avoid placing every plugin dependency in the API environment. Define owners, supported versions, maintenance and retirement procedures; vulnerabilities must be able to block new installations and identify affected services.

## Remaining architecture work

1. Validate the request/execution/service model and provider contracts.
2. Extend concurrency and recovery evidence beyond the completed local PostgreSQL migration.
3. Qualify separate workers, reservations, interruption and duplicate prevention.
4. Exercise another service category through the shared test kit.
5. Build distribution and effective isolation before public untrusted-code use.

Dates, capacity, registry choice and public availability are not commitments. These proposals do not replace approved requirements.

## References

- `README.md`, `docs/COMPLETION_STATUS.md`: delivered scope and limitations.
- `docs/EXTENSIONS.md`, `docs/EXECUTION.md`: existing contracts.
- https://www.sqlite.org/whentouse.html : SQLite usage boundaries.
- https://www.postgresql.org/docs/current/explicit-locking.html : PostgreSQL locking primitives.
