# KuberWeaver
## Historical project overview and requirements

Version 1.3, 6 November 2026

Institution/campus: EFREI Paris

Audience: mentor, technical reviewers and project team

Status: historical framing proposal requiring human review and approval

> This English edition retains the historical scope, requirement IDs, acceptance criteria and reference responsibilities. It is not a current capability report. Future-tense requirements are not delivered features, and proposed priorities/milestones are not an approved schedule. Later storage and provisioning work is documented in the current README and operator guides.

## Contents

1. Overview
2. Need and objectives
3. Scope, users and responsibilities
4. Historically documented implementation
5. Expected workflows
6. Architecture and technical choices
7. Plugins and frontend integration
8. Functional requirements and acceptance criteria
9. Nonfunctional requirements
10. Data, safety and operations
11. Testing and acceptance presentation
12. Organization, milestones and deliverables
13. Risks and open decisions
14. Framing method, traceability and sources

## 1. Overview

KuberWeaver is an internal developer/infrastructure platform (IDP). Users describe an environment graphically, then request controlled, traceable deployment. Catalogue components may represent a web server, application, database, VM or network configuration. Each supplies parameters and a deployment contract. The platform validates requests, estimates cost, enforces roles/approval and orchestrates execution/checks.

A modular kernel carries shared guarantees; plugins and replaceable providers add capabilities. The goal is not to rebuild a hypervisor, cloud or Ansible but to expose existing automation through governed workflows.

At the historical reference date, real NGINX/Apache deployment on virtualized lab targets had been exercised with approval, backup, canary, rolling, verification and restoration. That scope did not establish a general-purpose production-ready platform. The name does not imply Kubernetes: the documented implementation did not install or orchestrate a Kubernetes cluster.

### Example

An operator adds a web server to Canvas, selects targets/resources and submits. An administrator reviews cost and approves. The platform backs up actual state, deploys to a first target, verifies, then continues. On failed checks it attempts to restore already modified resources and reports evidence, including failed recovery rather than fabricated success.

### Value to demonstrate

- Reduce repetitive manual work and inconsistent procedures.
- Expose forms rather than unrestricted commands.
- Centralize roles, policy, approval, estimates and traces.
- Limit error impact through staged execution and recovery.
- Add capabilities without vendor-specific kernel branches.

These are intended benefits, not claims of market uniqueness. Commercial positioning requires a documented comparison.

## 2. Need and objectives

### 2.1 Initial need

The original request was an initially empty visual self-service infrastructure interface: frontend/backend environments on VMs/containers, bulk configuration, Cisco/pfSense, governance, GitOps, secrets, messaging, monitoring, FinOps and read-only AI diagnostics. Separate tools/procedures risk losing consistency between requested, approved, executed and observed state.

### 2.2 External-agent capability

AI integration primarily means making business functions accessible to an external agent through the API and typed MCP adapter. The portal need not embed a language model. Agents retain the same role ceilings, explicitly allowed actions, quotas, validation, approval and audit boundaries.

Agent tokens are individual, expiring and revocable, with only digests stored server-side. Operators submit but do not approve. Administrative/approval authority requires explicit human-administrator delegation within role/scopes. An agent cannot create its own privileged access. Read-only LLM diagnostics is an optional separate integration, not the product's mandatory AI dependency or a required subscription.

### 2.3 Objectives

| Objective | Intended result |
| --- | --- |
| O1, governed self-service | Compose requests without writing the entire automation implementation. |
| O2, governance | Reject noncompliance and require approval for sensitive changes. |
| O3, verifiable execution | Perform real deployment and distinguish success, failure, restoration and manual review. |
| O4, modularity | Add compatible components/providers without kernel changes. |
| O5, traceability | Link identity, plan version, approval, journal and result evidence. |
| O6, operations | Explain incidents, estimates and observed resources. |

### 2.4 Required evidence

Demonstrate an end-to-end lifecycle on actual resources, not merely a topology or plausible logs. Add another service category to test modularity beyond the two web servers.

## 3. Scope, users and responsibilities

### 3.1 Scope levels

- **P0, acceptance baseline:** declarative catalogue, Canvas, forms, governance, roles, approval, durable jobs, real deployment/restoration, local Git, audit and modularity evidence. Preserve/revalidate existing capabilities.
- **P1, proposed priority extension:** a genuinely different service category, vault-sourced execution secrets, an actual notification connector, operations and reproducible documentation.
- **P2, conditional extensions:** OpenTofu with a selected provider, Cisco/pfSense, interactive ChatOps, LLM diagnostics, measured FinOps and external observability.
- **Long-term vision:** broader hybrid platform, secure community marketplace, multi-tenancy and possible commercial services.

The team/mentor must choose P1/P2 according to time, expertise and authorized access. The full vision is not the minimum academic deliverable.

### 3.2 Actors

| Actor | Responsibility |
| --- | --- |
| Viewer | Inspect permitted resources/executions without mutation. |
| Operator | Compose, configure, save and submit requests. |
| Administrator | Approve/reject, manage policy/providers, authorize extensions and handle incidents. |
| Plugin author | Deliver documented schemas, execution, verification and service-appropriate recovery. |
| Lab operator | Supply targets, access, backups and safe test conditions. |
| Mentor/expert | Challenge scope, choices, risks and evidence; not replaced by AI. |
| Project team | Implement, test, document and obtain approval. |

Roles separate operator/approver powers. A strict ban on approving one's own request remains a governance decision to define and test.

### 3.3 Non-goals

Do not build a replacement hypervisor/cloud/automation engine, promise universal rollback or zero failure, run untrusted code inside the server, grant agents implicit privileges, bypass policy/approval, implement every vendor in one iteration, deliver payments/commercial billing/multi-tenant SaaS in P0, or expose the local installation publicly without hardening.

## 4. Historically documented implementation

The following reflects reports available at the reference date, not a fresh acceptance run. It must not override later operator documentation.

| Capability | Historical evidence | Boundary at that date |
| --- | --- | --- |
| React/TypeScript portal and React Flow Canvas | Implemented. | Standard interface; no documented third-party-page SDK. |
| Manifest catalogue/forms | Implemented. | Supported JSON Schema subset, not arbitrary schema/UI interpretation. |
| Blueprint save/reload | Implemented. | Local application state. |
| Sessions, roles, CSRF, admission, quotas | Implemented. | Local accounts, no SSO/per-object permissions. |
| Approval/rejection | Portal workflow. | Messaging approval not then delivered. |
| NGINX/Apache | Actual deployment records. | Static web containers on two lab guests. |
| Canary/rolling/rollback | Exercised for web services. | Not database consistency or network recovery. |
| GitOps | Local approved-plan commits. | No historical remote push or continuous Git reconciliation in that baseline. |
| GNS3 inventory | Actual reads. | No new VM provisioning. |
| FinOps | Internal estimates. | Not bills or measured consumption. |
| OpenBao | KV v2 source/persistence tested. | No Ansible-key injection; manual bootstrap/unseal. |
| Python extensions | Install/activate/select exercised. | Trusted server-privileged code, not a sandbox. |
| Notifications | Local outbox. | No configured external delivery in that baseline. |
| Diagnostics | Deterministic rules. | No configured LLM inference. |
| Health/metrics | Local endpoints. | No accepted integrated Prometheus/Grafana/Loki stack. |
| Cloud, OpenTofu, Cisco, pfSense, frontend/backend stack | Planned entries. | Disabled placeholders are not delivered services. |
| Marketplace, signatures, payments | Not implemented. | Local checks are not security certification. |

### 4.1 Historical evidence

`VERIFICATION.md` records an earlier 159-test backend suite, 35 frontend passes, successful TypeScript/Vite build, browser/API acceptance, actual two-target rollback and extension load/unload without kernel Python changes. These are historical numbers, not a rolling measure of the current specification or source tree. Raw evidence remains private and must be reviewed for secrets before sharing. Passing tests does not establish production maturity.

## 5. Expected workflows

### 5.1 Compose and submit

Authenticate; open an empty Canvas or existing blueprint; add catalogue components; configure forms; express dependencies; server-validate schema, roles, targets, quotas and policy; display errors or plan/estimate; save and submit.

### 5.2 Approve and execute

The administrator reviews exact plan, targets, plugin provenance and estimate, then approves/rejects with a recorded decision. Pin the plan and approved executable sources. The job preflights, snapshots actual state, applies/verifies canary, continues in batches only on success, and persists events/result evidence. Plan/source drift must invalidate authority or block execution, never silently change the approved operation.

### 5.3 Failure

Stop progression and restore modified resources only when the plugin contract supports it. Distinguish original failure from restoration outcome. Failed restoration stays explicitly `rollback_failed` and requires human intervention. An unreachable service may not be recoverable through the same broken management path; future network integrations need out-of-band access or another tested mechanism before claiming rollback.

### 5.4 Extension

Authors deliver documented/tested packages. Administrators review/install them. Compatible blueprints are catalogue data; executable extensions remain disabled until explicitly trusted/enabled. Disabling an optional capability must not break unrelated portal functions.

## 6. Architecture and technical choices

### 6.1 Logical view

```text
Browser: catalogue, Canvas, forms, approval, tracking
                         |
                         v
Kernel: identity, validation, policy, approval, state, audit, jobs
                         |
              Provider registries/contracts
        /          |         |          |          \
   Inventory   Execution   Secrets  Notifications Diagnostics
                   |
          Automation and target systems
          Ansible, containers and lab guests
          Optional later OpenTofu/cloud/network providers
```

Plugins cannot remove identity, approval, schema validation or serialization invariants.

### 6.2 Historical stack and options

| Layer | Historical implementation | Possible extension, not mandatory |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite, React Flow. | Specialized UI only for a demonstrated need. |
| API | FastAPI, Python models/policy. | Versioned public contracts. |
| State | SQLite and private files at that date. | PostgreSQL when concurrency/operations require it; later migration is documented separately. |
| Jobs | Durable serialized application worker. | Distributed queue only for a proven need. |
| Execution | Ansible container lifecycle. | OpenTofu or specialized provider. |
| Secrets | Configurable sources including OpenBao. | Execution injection/rotation. |
| Versioning | Local Git. | Remote repository/PR workflow if selected. |
| Operations | User systemd, health and local metrics. | External metric/log collection. |

Early vision mentions of Next.js, PostgreSQL, Celery/Redis or Temporal, OPA and monitoring tools were proposals, not mandatory dependencies or proof of delivery.

### 6.3 Desired versus observed

A blueprint is intent; inventory/probes describe actual resources/responses. A Canvas node proves neither creation nor health. Edges impose ordering, not cabling, firewall rules, discovery or frontend/backend URLs. Any such future effect needs an explicit contract.

## 7. Plugins and frontend integration

### 7.1 Complementary mechanisms

A **declarative blueprint** uses an existing executor and supplies manifest, schema, defaults and lifecycle assets:

```text
plugins/mon-service/
  manifest.yaml
  iac/snapshot.yml
  iac/playbook.yml
  iac/verify.yml
  iac/rollback.yml
```

A **Python extension** adds an inventory/execution/secrets/notification/diagnostic provider through the versioned SDK, using `plugin.yaml` and `module.py`. Adding a blueprint is not the same operation as installing an executable extension.

### 7.2 Automatic frontend behavior

The backend discovers valid manifests from disk; catalogue reload exposes metadata and a standard schema form without rebuilding React for a compatible blueprint. This does not imply arbitrary schemas/icons or specialized consoles work without implementation. Supported field/rendering contracts need documentation. Activating a provider does not create a UI page. Interactive consoles, network editors or advanced service-specific views require a separate interface; plugin-delivered frontend component loading is not a documented capability.

### 7.3 New-component contract

Declare ID/version/description/compatibility/provenance; defaults/schema constraints; allowed targets and privileges; understandable plan/estimate; snapshot of actual state including absence; repeatable application and service checks; documented recovery/limits/manual procedure; success/failure/restore tests; no embedded secrets. Persistent services must not blindly reuse static-web rollback: container restoration does not prove transactional data consistency.

### 7.4 Modularity criterion

Demonstrate a different category's lifecycle without business-condition branches in the kernel. Record integration effort, changed files and contract limits; no universal integration-time promise is made.

## 8. Functional requirements and acceptance criteria

Priorities are proposals. Every requirement has an observable criterion; listing a row does not mean it passed.

| ID | Priority | Requirement | Acceptance criterion |
| --- | --- | --- | --- |
| EXG-001 | P0 | Authenticate/enforce roles server-side. | CRT-001: viewer cannot submit/approve, operator cannot administer; direct API denial tests. |
| EXG-002 | P0 | Discover blueprints without kernel edits. | CRT-002: valid manifest appears on reload; invalid one is reported without hiding peers. |
| EXG-003 | P0 | Compose/save Canvas graphs. | CRT-003: reload preserves nodes, positions, parameters and dependencies; cycles denied. |
| EXG-004 | P0 | Render supported schema forms. | CRT-004: defaults, choices, numeric bounds render; server also rejects invalid values. |
| EXG-005 | P0 | Enforce policy/quotas before deployment. | CRT-005: unknown target, arbitrary command, excess quota and forbidden configuration denied before mutation. |
| EXG-006 | P0 | Approve/reject the exact plan. | CRT-006: decision/actor/time persist; changed plan or sources block nonconforming execution. |
| EXG-007 | P0 | Version/audit executed requests. | CRT-007: approved run links sanitized plan, actual Git commit and events without secrets. |
| EXG-008 | P0 | Persist/serialize jobs. | CRT-008: restart retains history; interruption remains under review without silent replay. |
| EXG-009 | P0 | Deploy progressively on actual resources. | CRT-009: traces and independent HTTP reads prove backup, canary, checks and rolling on two targets. |
| EXG-010 | P0 | Recover after failure and offer eligible manual rollback. | CRT-010: rolling failure also restores canary; absence is restored; failed recovery never reports success. |
| EXG-011 | P0 | Separate observed inventory from intent. | CRT-011: actual API read; outages remain explicit, never fabricated data. |
| EXG-012 | P0 | Show FinOps estimate before approval. | CRT-012: total, breakdown, currency and estimated nature visible and consistent with server calculation. |
| EXG-013 | P0 | Explicit installation/activation. | CRT-013: disabled code not imported; failed plugin leaves no partial registrations or kernel failure. |
| EXG-014 | P1 | Add a non-web service category. | CRT-014: deploy/check/restore without kernel changes; persistence limits documented. |
| EXG-015 | P1 | Vault-sourced execution secrets. | CRT-015: real deployment uses least-privilege vault secret; expiry/revocation fail cleanly; no Git/log leak. |
| EXG-016 | P1 | Actual notification connector. | CRT-016: a job event produces a received message with failure/duplicate handling. |
| EXG-017 | P1 | Observable portal operations. | CRT-017: health/jobs/errors readable, controlled failure visible, retention documented. |
| EXG-018 | P2 | Selected OpenTofu provider. | CRT-018: plan/create/independent read/authorized destruction demonstrated; state/locking protected. |
| EXG-019 | P2 | Pilot network device. | CRT-019: authorized change/restore independently verified through retained recovery access on an allowed lab target. |
| EXG-020 | P2 | Secure ChatOps approval. | CRT-020: authenticated actor approves exact plan; replay/expired/unauthorized clicks denied/audited. |
| EXG-021 | P1 | Limited external-agent API/MCP access. | CRT-021: real MCP reads/submissions through API; out-of-scope actions/operator approval denied; expiry/revocation block access; authorized changes remain approved/audited. |
| EXG-022 | P2 | Controlled remote GitOps. | CRT-022: test destination receives sanitized plan and links request/commit/result; Git failure/divergence explicit. |
| EXG-023 | P2 | Measured consumption/FinOps recommendations. | CRT-023: measurements state source/period/freshness; estimates remain separate from invoices. |
| EXG-024 | Vision | Third-party plugin trust chain. | CRT-024: define/test integrity, provenance, versions, scans, review, revocation and isolation before untrusted execution. |

## 9. Nonfunctional requirements

### 9.1 Safety and reliability

Deny targets/actions/capabilities by default; validate server-side independently of forms; reject arbitrary user commands; enforce ownership against adoption/deletion of foreign resources; lock incompatible operations; do not silently replace disabled providers. Explicitly report partial recovery, vault outage, source drift and unreachable targets. Exercise portal backup/restore before durable operation.

### 9.2 Usability and accessibility

Non-authors of playbooks should understand the main path. Errors name the issue and possible action. Label fields, identify sensitive operations and avoid color-only status. Historical mobile-width checks are not a full accessibility audit. Keyboard navigation and Canvas alternatives still require acceptance.

### 9.3 Performance and capacity

No production SLA is established. Measure catalogue load, submission, event-display latency and lab job duration before approving thresholds, users/resources or load tests. Recovery time depends on provider, data and target access; the original seconds-level network-rollback aspiration is not a commitment.

### 9.4 Maintainability and reversibility

Require versioned contracts, documented schemas, explicit dependencies, automated tests and reproducible procedures. Export plans in a documented format. State migration/incompatible upgrade needs backup and a return procedure.

### 9.5 Costs, licenses and confidentiality

Prefer components without mandatory subscriptions; free software is not zero hosting/API/model/messaging cost. Check tool/image/network-system licenses before distribution. Project licensing remains to be confirmed. Do not send infrastructure data/logs to external AI without authorization/content review.

## 10. Data, safety and operations

### 10.1 Business objects

Users/roles, blueprints, nodes/dependencies, requests, approvals, versions/digests, jobs/events, snapshots, observed targets, estimates, configured providers, secret references and notifications must remain traceable. Secret values are not catalogue metadata or publishable plan fields.

### 10.2 Trust zones

1. Browser/user input, untrusted.
2. Kernel, responsible for authorization.
3. Executable extensions, currently administrator-trusted code.
4. Automation tools and target access.
5. Vault/bootstrap operator.
6. Optional external messaging/AI services.

Limit technical-account privileges to functions. Do not give plugins recovery/admin secrets or ship them with code.

### 10.3 Lab boundaries

Workstation control plane, GNS3 guest deployment, loopback portal, isolated management network—not automatic LAN/Internet publication. For the web executor, disk declarations are budgeting inputs, not enforced quotas; CIDR is an admission guard, not a firewall rule. The portal does not secure GNS3 for other clients. Host/guest administrators remain trusted.

### 10.4 Before production exposure

Require appropriate HTTPS, identity, resource permissions, secret protection/rotation, privilege separation, execution isolation, restorable backups, retention, dependency hardening and threat-model review. SSO, multi-tenancy and HA are not established. Local encryption does not defeat OS-account/root compromise.

## 11. Testing and acceptance presentation

### 11.1 Test layers

- Unit: schema, policy, calculations, state transitions, contracts.
- Integration: API, storage, registry, actual provider selection, secrets.
- Frontend: forms, rights, errors, navigation.
- End-to-end: real browser and backend.
- Infrastructure: independent service/state readback after mutation.
- Resilience: canary/rolling failure, partial restoration, controller restart.
- Security: roles, replay, invalid input, post-approval drift, foreign resources.

Injected Ansible boundaries are useful but do not prove an actual induced lab outage.

### 11.2 Minimum presentation

Show real Canvas/catalogue; create a rejected web request; correct it and show estimate; approve/deploy to two targets; independently verify responses; update with controlled failure and prove restoration; retrieve plan/decision/commit/events; add/activate a capability without kernel edits. Future features are presented as functional only after their own acceptance. An explicit unavailable state is preferable to simulated success.

### 11.3 Protect the test environment

Mutate only owned resources on authorized targets. Record baseline, use free ports, preserve existing applications, restore temporary resources and do not touch physical appliances/production for acceptance.

## 12. Organization, milestones and deliverables

### 12.1 Reference team and proposed allocation

Six EFREI Paris team members and reference roles were supplied by the project owner. Work packages/reviews below are proposed coordination, adjustable without changing those roles. Security is shared throughout, not a final task assigned to one person.

| Person | Reference role | Main proposed scope |
| --- | --- | --- |
| Etienne MURZEAU | Project lead / submitter, AI / infrastructure integration | Architecture, coordination, read-only AI integration, cross-cutting integrations and presentation. |
| Rayan CHOUCHANE | Terraform / Ansible infrastructure + GitOPS, lint/container pipeline | GitOps/CI/CD, checks, execution-tool containers and automation integration. |
| Alex YAO | Terraform / Ansible infrastructure | Provisioning, inventory, targets and conditional IaC provider. |
| Vidjay VELAYOUDAM | FinOps / Business | Cost model, indicators, user need, value proposition and economics. |
| Abdel-Waheb SAKKAL | Terraform / Ansible infrastructure | Service/device configuration, plugins, rollout, verification and restoration. |
| Priyank SOLANKI | Full Stack web developer | Portal/API, contracts, workflow, application rights and UI integration. |

The original approximately 510-hour envelope is a framing hypothesis, not actual time, confirmed individual allocation or remaining-work estimate. Capacity requires scope approval.

#### 12.1.1 Etienne MURZEAU

Coordinate mentor discussions/scope decisions; maintain architecture, contracts and lab consistency; integrate contributions/dependencies; define diagnostics and optional read-only AI over authorized data; require sources/limits and a new human request before corrective mutations; coordinate secrets/observability/connectors; prepare reviews and final evidence.

Deliverables: architecture, decision register, diagnostic contract/selected demonstration and technical synthesis. Security: threat model, least privilege, authorized external-AI flows, sanitization and no mutation tools in diagnostics. Collaborate with Priyank on API/UI, infrastructure owners on evidence and Vidjay on FinOps; coordination does not replace others' delivery/review duties.

#### 12.1.2 Rayan CHOUCHANE

Maintain the GitOps chain linking sources, approved plans, versions and outcomes; pipeline manifest/code/IaC lint, tests and selected image builds; reproducible execution images with explicit dependencies (a proposed deliverable, not an assumed existing capability); integrate others' playbooks without duplicating logic; retain artifacts and failures; add remote publication only if EXG-022 is chosen; document contribution/review/release.

Deliverables: pipeline, lint rules, images/build recipes, GitOps conventions, check reports and delivery procedure. Security: least-privilege runners, secret-free artifacts/images, dependency/image checks, branch protection and approval. Lint/scans are not plugin certification or sandboxing. Collaborate with Alex/Abdel-Waheb on automation, Priyank on jobs and Etienne on version/approval coherence.

#### 12.1.3 Alex YAO

Define allowed inventory/prerequisites; prepare owned resources without altering foreign workloads; design IaC modules/variables only if the provisioning milestone is approved; plan/create/read/authorize deletion; protect/lock state and define configuration outputs; supply inventory distinguishing intent from observation; document provider limits/reproducibility/cleanup.

Deliverables: inventory, lab prerequisites, selected-provider modules/output contracts, creation/cleanup procedures and independent readback. Security: least privilege, sensitive-state protection, isolated management, ownership and no unauthorized destruction. Coordinate targets with Abdel-Waheb, pipeline with Rayan, API with Priyank and lab dependencies with Etienne.

#### 12.1.4 Vidjay VELAYOUDAM

Define users/priorities/use cases; document value/comparison without unsupported uniqueness claims; tariff units/currency/assumptions/calculation; per-resource/blueprint/environment indicators and measured trends only where data exists; specify server-enforced budgets with Priyank; distinguish estimates/measurements/invoices with freshness; analyze hosting/maintenance/API/service costs and marketplace hypotheses without mandatory MVP payments.

Deliverables: business framing, cost model/reference calculations, dashboard specification and economic assumptions/limits. Security: financial confidentiality, role-based visibility and external-data minimization. Collaborate with Priyank on calculations/UI, infrastructure owners on actual allocations and Etienne on recommendations.

#### 12.1.5 Abdel-Waheb SAKKAL

Maintain service roles/playbooks; deliver manifests/schema/defaults/assets; add a different category to test contract limits; implement preflight, backup, canary, batches and service checks; test automatic/manual restoration including absence, partial failure and persistent-data limits; conditionally configure authorized Cisco/pfSense with recovery access; document effects/recovery without equating ordering and network wiring.

Deliverables: documented plugins/playbooks, verification/recovery contracts, lifecycle tests and independent outcome evidence. Security: service hardening, allowed ports/targets, change controls, protected backups and recovery that preserves data/foreign resources. Coordinate inventory with Alex, pipeline with Rayan, UI/events with Priyank and secrets/limits with Etienne.

#### 12.1.6 Priyank SOLANKI

Maintain frontend/backend without moving playbook business logic into the kernel; Canvas/catalogue/forms/blueprints/request/deployment views; API/state/transitions with execution owners; sessions/RBAC/CSRF/server validation; cost/inventory/automation-result views with domain owners; provider selection/configuration and diagnostics rendering; frontend/API tests, accessibility, actionable errors and interface docs.

Deliverables: portal/API, contracts, form components, approval/tracking, integrated dashboards and tests. Security: authorization for every mutation, input validation, injection/response-leak prevention, session controls, secret masking and sensitive-action protection. UI restrictions never substitute for API enforcement. Priyank integrates domain data but does not solely own playbooks, economics, AI or infrastructure acceptance.

#### 12.1.7 Infrastructure boundaries

| Topic | Proposed lead | Interface |
| --- | --- | --- |
| Resources/provisioning/inventory | Alex YAO | Supplies documented targets/outputs. |
| Service configuration/rollback | Abdel-Waheb SAKKAL | Consumes inventory, delivers plugins/lifecycle evidence. |
| GitOps/lint/pipeline/execution containers | Rayan CHOUCHANE | Checks, versions and assembles without reimplementing provisioning/configuration. |

Terraform in reference roles identifies IaC expertise, not a mandated engine change. The historical baseline uses Ansible; Terraform/OpenTofu selection depends on licenses, costs, provider and scope. Documentary allocation triggers no provisioning.

#### 12.1.8 Requirement coverage and independent review

Leads coordinate evidence; contributors implement their parts; reviewers provide a distinct check. Neither academic approval nor production authorization is delegated to one person by this table.

| Requirements | Proposed lead | Contribution/review |
| --- | --- | --- |
| EXG-001–EXG-006, EXG-008, EXG-013 | Priyank | Etienne checks invariants; Abdel-Waheb schema/parameters; Rayan executed-source links. |
| EXG-007, EXG-022 | Rayan | Priyank links jobs/decisions/commits; Etienne reviews approved-plan fidelity. |
| EXG-009, EXG-010, EXG-014, EXG-019 | Abdel-Waheb | Alex supplies targets/independent checks; Rayan assets; Etienne restoration limits. |
| EXG-011, EXG-018 | Alex | Priyank API; Abdel-Waheb inventory use; Rayan pipeline/state protection. |
| EXG-012, EXG-023 | Vidjay | Priyank calculations; infrastructure owners resources; Etienne recommendations. |
| EXG-015 | Etienne | Rayan/Abdel-Waheb injection; Alex target accounts; Priyank API/UI protections. |
| EXG-016, EXG-017, EXG-020, EXG-021 | Etienne | Priyank workflows; Rayan events/metrics; Vidjay economics; infrastructure owners privileges/sources. |
| EXG-024 | Rayan | Etienne trust model; Priyank admin workflow; Alex/Abdel-Waheb target effects. |

P2/Vision entries remain conditional, even with a named lead.

#### 12.1.9 Security across stages

| Stage | Expected controls | Main coordination |
| --- | --- | --- |
| Framing/design | Sensitive data, trust zones, roles, risks, mutation limits. | Etienne and whole team. |
| Implementation/catalogue | Validate inputs, schemas, paths, contracts, dependencies; no secrets in code. | Priyank for app, Alex/Abdel-Waheb for IaC/plugins. |
| Integration/build | Lint/tests, dependency/image checks, artifact review before promotion. | Rayan and relevant authors. |
| Request/approval | Roles, quotas, estimates, policy, exact-version verification. | Priyank, Vidjay for economics, Etienne for cross-cutting coherence. |
| Access/execution | Least-privilege accounts, authorized secret source, ownership and allowed targets. | Etienne/Rayan/Alex/Abdel-Waheb by layer. |
| Deployment/recovery | Backup, canary, independent probes, interruption and service-appropriate rollback. | Abdel-Waheb, independent Alex checks, Rayan pipeline. |
| Operations/diagnostics | Sanitized logs, metrics, retention, access management, read-only AI. | Etienne/Priyank and domain owners. |
| Presentation/sharing | Review captures/exports/plans/evidence; exclude private material. | Every author, cross-review coordinated by Etienne. |

Each member documents risks/tests. Sensitive changes need another reviewer. A security gap blocks affected delivery until fixed or formally treated within the agreed risk framework; sprint completion is not acceptance.

#### 12.1.10 Coordination and handover

Each work package has an owner, documented interfaces, criteria and reviewer. Define cross-layer contracts before dependent work. Every member supplies code/configuration, tests, reproduction and known limits. Integration review follows a complete user path, not isolated component startup. Etienne centralizes blockers; team/mentor approve relevant decisions. Dates/capacity remain collective inputs; assignment does not mean completion.

### 12.2 Proposed milestones

| Milestone | Deliverable | Exit criterion |
| --- | --- | --- |
| J0, scope approval | Approved P0/P1 scenarios/criteria. | Mentor confirms evaluated/out-of-scope work. |
| J1, reproducible baseline | Startup/tests/web evidence. | Another operator reproduces deployment/rollback. |
| J2, broader extensibility | Different service category. | Lifecycle passes without kernel business edits. |
| J3, minimum operations | Selected execution secrets/notification. | End-to-end success and unavailability tested. |
| J4, selected extension | One prioritized P2 axis. | Accepted on an actual authorized target. |
| J5, acceptance/handover | Documentation, sanitized evidence, presentation, limitations. | Selected requirements have outcomes; gaps accepted/deferred explicitly. |

Confirm dates/effort/dependencies after selection. Do not start cloud, network, marketplace and AI concurrently before stabilizing the baseline.

### 12.3 Deliverables

Versioned specification/decisions; portal/provider contracts; plugins with manifests/schema/assets/docs; installation/start/stop/backup/recovery procedures; acceptance environment/inventory; tests/completed acceptance matrix; sanitized evidence/limitations; plugin-author and operator/admin guides; presentation and AI-use disclosure.

## 13. Risks and decisions

### 13.1 Initial risk register

| Risk | Consequence | Proposed mitigation |
| --- | --- | --- |
| R1, scope dispersion | Many screens, few complete paths. | Freeze P0, select few extensions. |
| R2, over-specialized modularity | Each component needs kernel edits. | Test a different category early and record changes. |
| R3, data-inappropriate rollback | Loss/inconsistency despite success status. | Service-specific backup contracts, independent restore evidence. |
| R4, lost network access | Broken path cannot restore itself. | Out-of-band access/authorized lab target. |
| R5, privileged third-party code | Host/secret compromise. | Explicit trust; no public marketplace before real isolation. |
| R6, post-approval drift | Execution differs from review. | Digests, pinned sources, pre-mutation revalidation. |
| R7, secret leakage | Git/capture/AI disclosure. | Sanitization, least privilege, review before sharing. |
| R8, lab dependency | Portal reachable but acceptance unavailable. | Precheck guests/access/vault/ports. |
| R9, estimate/invoice confusion | Misread costs. | Visible source/currency/assumptions/period. |
| R10, overconfident AI | Dangerous fix/invented cause. | Sources, uncertainty, read-only diagnosis, human decision. |

### 13.2 Mentor/team decisions

Confirm priority user problem/audience, academic scope versus commercial vision, next service category, one P2 priority, target environment/authorized images/access, work-package allocation/capacity/dates/budget/evaluation, requester/approver separation, evidence retention/sharing, local-trusted versus distributed third-party plugins, and specialized versus generic UI needs.

### 13.3 Definition of done

A delivered capability has a documented contract, real implementation, suitable tests, observable acceptance result and explicit limitations. A catalogue row, interface or screenshot alone is insufficient. The agreed scope is accepted only when selected criteria pass or responsible people explicitly accept deviations. This document does not approve itself.

## 14. Framing method, traceability and sources

### 14.1 Method

The historical review consulted `charroux/assistant-IA-projets` revision `b9c0eae5a64c80c7714f27021cee18a5bfd69483`. Its method separates need, requirements, criteria, design, decisions, evidence and gaps. Deterministic checks are distinct from AI audit and do not replace the mentor.[1]

EXG/CRT pairs form the traceability matrix; evidence applies only to its tested scope. A documentary export supports structural checking, not academic approval or technical software audit. The project owner confirmed EFREI Paris, encoded as `paris`. Historical strict coherence reported 24 requirements/24 criteria, zero errors/warnings; `cadrage-mentor/rapport-coherence.md` retains that result. Dates/institutional grading remain unconfirmed; calendar milestones were intentionally empty, not inherited from the upstream example.

That historical check validated export structure, not all Markdown facts, execution or this translation. The `coherence` tool made no AI call. New decisions remain proposed until approved.

### 14.2 Local reference sources

Paths below identify original documents, not instructions to publish private source material. Vision suggestions are not verified capability claims.

| Reference | Document | Use |
| --- | --- | --- |
| S1 | `Prototype/prompt humain` | Original project-owner need; private source not reproduced. |
| S2 | `Prototype/Cette vision décrit l'architecture complète d'un Internal Developer & Infrastructure Platform (IDP) orienté SaaS_Marketplace.md` | Broader vision, technical proposals and organizational hypothesis. |
| S3 | `Prototype/Le flux d'orchestration étape par étape (De la demande au Rollback).md` | Intended workflow/subsystem relationships. |
| S4 | `kuberweaver/README.md` | Implemented scope, startup and limitations. |
| S5 | `kuberweaver/docs/ACCEPTANCE.md` | Acceptance gates/safety boundaries. |
| S6 | `kuberweaver/docs/VERIFICATION.md` | Historical results/evidence index. |
| S7 | `kuberweaver/docs/EXECUTION.md` | Execution, ownership and recovery contract. |
| S8 | `kuberweaver/docs/EXTENSIONS.md` | SDK, activation, registry and trust boundaries. |
| S9 | `kuberweaver/backend/kuberweaver/catalog.py` and `frontend/src/App.tsx` | Manifest discovery/catalogue loading. |

Raw private evidence is not included. Detailed credentials/secrets are unnecessary for understanding this specification and must not be reproduced.

### 14.3 Assistance and human approval

This historical specification was drafted with AI assistance from the listed sources. Structure, priorities, future criteria and risk analysis are synthesis proposals requiring team/mentor review for correctness and feasibility. No budget commitment, institutional deadline or production authorization is created by drafting or translating it.

## Sources

[1] https://github.com/charroux/assistant-IA-projets — project-assistance method referenced by the historical specification.
