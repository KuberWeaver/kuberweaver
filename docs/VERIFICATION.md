# Verification history

This document indexes recorded acceptance of the web-service workflow. It is **historical evidence**, not a new run against the current tree or proof of production maturity. Later storage/hypervisor records are described in `V2_HYPERVISORS.md`. Runtime evidence remains private and requires secret review before sharing.

## Independent portal acceptance

FastAPI served the built frontend at `http://127.0.0.1:8088`. A real Chromium browser, without a mocked backend for these acceptances, exercised:

- Login, an initially empty canvas, NGINX addition and schema-generated form.
- Server validation, blueprint save and request submission.
- Browser approval followed by real Ansible execution on `app` and `branch-client`.
- HTTP responses on both management IPs, independently checked through SSH.
- Server-calculated cost and per-resource breakdown before approval.
- Browser rejection with rejected status read back through the API.
- Nine sections at 390-pixel width without horizontal page overflow.
- Disabled viewer editing controls and no JavaScript exceptions during final acceptance.

Evidence: `data/evidence/browser-acceptance.json`, `browser-final.json` and `portal-*.png` captures.

## Actual rollback

NGINX `1.0.0` was installed on both targets. An update to `2.0.0` with intentional rolling-healthcheck failure restored `1.0.0` on both, including the previously successful canary. A successful second update served `2.0.0`, then manual rollback restored `1.0.0`.

Independent SSH processes checked both guests. Direct executor acceptance also covered Apache and restoration to absence when no container previously existed. Evidence: `data/evidence/api-live-acceptance.json`, `execution-live.json`, and private journals/snapshots under `data/jobs/` and `data/execution/`.

Retry of partially failed rollback was tested through the API and real executor with an injected Ansible boundary. That is not a newly induced network failure on the real lab. Web-container rollback does not prove database consistency, network-device recovery or VM rollback.

## Exercised modularity

The separately packaged `lab-notes` extension was installed disabled, explicitly enabled and selected through Integrations. A real job emitted `job_finished`; the extension persisted the event and `/api/diagnostics` returned its job ID/provenance. Disabling the still-selected provider made only that endpoint unavailable while inventory and jobs remained readable. Explicitly selecting `rules` restored diagnostics. Kernel Python fingerprints remained unchanged throughout.

Evidence: `data/evidence/modularity-live.json`. The example was left disabled. Historical final bindings included OpenBao, which did not imply deployment keys were injected from it.

## Historical tests and build

The earlier integration record reported 159 backend/SDK/executor/secrets tests without failures/errors, 35 frontend passes, successful TypeScript/Vite build and no `npm audit` findings at that time. JUnit: `data/evidence/parent-suite.xml`. These are not current test counts and an audit result is not security certification.

## Persistence and operations

The earlier record describes active user systemd services for the portal and OpenBao tunnel, linked but not enabled at boot. A real application restart preserved jobs and provider bindings; interrupted work was not automatically replayed. Evidence: `data/evidence/restart.json`. See `POSTGRESQL.md` for the later service configuration, which does not implicitly start the historical tunnel.

Historical OpenBao readback through its supervised tunnel covered initialized/unsealed state, persistent storage, a limited application token and out-of-policy denial. Evidence: `data/evidence/openbao.json`. Bootstrap custody, unseal, expiry and transport limitations remain in `SECRETS.md`; do not infer current reachability from the old record.

## Preserved scope

The recorded acceptance retained four original framing-file SHA-256 values, existing GNS3 project IDs and twenty lab link IDs. The original port-8080 application still served `1.0.2`; KuberWeaver deployments used only owned resources and management ports. No GitHub push or production deployment was part of that acceptance. Evidence: `data/evidence/source-brief-hashes.json`, `parent-baseline.json`, `parent-boundaries.json`.

## Review and cleanup records

An independent specification review initially blocked provider-selection bypasses. Fixes added provider-consumption/event tests; the second review passed the four identified gaps. Unsupported OpenTofu/cloud/Cisco/pfSense paths and unconfigured ChatOps, execution-secret and AI inference integrations were not given simulated deployment success.

A quality review then found incomplete approval fingerprinting of shared executable dependencies. The correction included the shared lifecycle directory, actual executor and inherited sources with command-boundary revalidation. Twenty-three additional tests covered drift. The recorded parent rerun covered the 159-test suite and API/browser/modularity acceptance on that build. Targeted review passed without remaining blockers **within its reviewed scope**, not as a sandbox or hostile-filesystem-race certification.

The direct example's port-18090 resources were restored to initial absence on both targets; the portal example on 18091 remained at `1.0.0`. Evidence: `data/evidence/default-port-cleanup.json`. Inspect actual current ownership before reuse; historical cleanup is not a live port check.
