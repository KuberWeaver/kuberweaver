# Acceptance gates, KuberWeaver first prototype

This is a runnable vertical prototype of the supplied vision, not a claim that all proposed enterprise integrations are implemented. The source framing documents remain unchanged. Evidence is captured under the private runtime data/evidence directory.

## Mandatory modularity gate

- Adding a service blueprint means adding a manifest, JSON Schema and reviewed execution assets, without changing the API core or portal source.
- Adding an infrastructure/secret/notification/diagnostic/execution provider means registering a capability through the versioned extension interface, not adding vendor branches to the job kernel.
- A disabled executable plugin is not imported. Installation is not activation. Trust is explicit.
- Failed plugins must not leave partial registrations or break valid siblings.
- Removing/disabling an optional provider must leave the core usable and expose an honest unavailable state.
- Third-party Python extensions are trusted host code. Capability metadata, lint checks and registry contexts are not OS isolation.
- Plugins cannot remove identity, approval, schema validation or job serialization safeguards.

## Product paths to exercise

1. Login as viewer: inspect inventory/jobs, cannot submit/approve/change policies.
2. Login as operator: compose canvas from the catalogue, connect nodes, configure from schema, validate, save and reload a blueprint, request a deployment.
3. Admin: inspect requested plan and cost, approve/reject, view live phase events.
4. Actual deployment on GNS3 lab targets: snapshot, canary, healthcheck, rolling, final verification. Preserve pre-existing application services.
5. Intentional rolling healthcheck failure: restore all already modified prototype resources and verify previous HTTP response on both targets.
6. Manual rollback of a successful change: restore actual previous state, including removal when the resource did not exist.
7. Jobs/audit survive control-plane restart; an interrupted operation is not silently retried.
8. Git history contains an actual approved sanitized graph commit, not a generated placeholder hash.
9. Governed requests fail closed: unknown targets, arbitrary commands, planned plugins, quota excess, invalid schema, graph cycles, public critical-port access, missing role/CSRF.
10. Desired canvas and observed GNS3 infrastructure are distinct, inventory comes from live API readback.
11. FinOps numbers are labelled internal tariff estimates, not measured cloud bills.
12. Diagnostics state their provider and source. Deterministic rules are not presented as an LLM.
13. Secret storage source, local-only transport, bootstrap custody, unseal and fallback limitations are documented.
14. Desktop and mobile browser workflows have no blocking JS/network errors, silent action failures or horizontal page overflow.

## Safety boundaries

Only supplied GNS3 project 2c796f12-d163-44d9-8ebd-2a739c383b7b and explicitly named guest paths/resources are writable. Services deployed by the prototype use kw-* resource names, /opt/kuberweaver and management ports 18090 through 18109. The optional OpenBao guest service has separate kw-openbao ownership. Do not change the GNS3 host routing/firewall, another project, physical LAN appliances, existing 8080 application or production deployments.

Control-plane HTTP listens on workstation loopback. Production exposure requires HTTPS, real identity hardening and a threat model beyond this lab prototype. The GNS3 server itself remains an existing unauthenticated LAN API, not made secure by this portal.

## Feature honesty

Functional in this iteration only when exercised: local declarative plugin catalogue, service deployment, governance, approvals, Git/audit, inventory, tariff estimation, diagnostics and local notification outbox. OpenBao and OpenTofu are reported based on their actual acceptance evidence.

Cisco IOS, pfSense, public-cloud VM provisioning, marketplace payments, external ChatOps approval buttons and model-based diagnostics require real adapters, licensed/authorized target images or user configuration. Their contracts and availability may be represented, but planned entries must never produce simulated deployment success. Local static checks do not establish marketplace-wide security verification. No deployment image or private key is silently downloaded from unauthorized sources.
