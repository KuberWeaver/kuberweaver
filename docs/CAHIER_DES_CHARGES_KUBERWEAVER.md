# KuberWeaver requirements

Version 2.0, simplified specification · EFREI Paris

This document separates requirements and proposed priorities from delivered capabilities. Reference responsibilities are retained; priorities, dates and resources require team/mentor approval. Technical results cited here come from existing reports, not new acceptance runs. See the README for current operational scope.

## 1. Purpose and scope

### Need

Deploying an IT service often requires several tools and manual procedures. KuberWeaver brings that workflow into an internal portal: select a service, configure it, approve the change and verify the result. The intended benefit is reuse of reviewed organizational procedures without removing security rules or replacing existing automation engines.

### Users and workflow

- Operators prepare, save and submit requests.
- Administrators approve/reject, manage policy and authorize extensions.
- Viewers inspect permitted information without mutation.
- External AI agents use the API/MCP within rights explicitly delegated by a human administrator; an embedded model or AI subscription is not mandatory.

Expected flow: choose service → provide parameters → check policy and estimated cost → obtain approval → back up and deploy progressively where supported → verify independently.

On failure, stop progression and attempt service-appropriate restoration **only where supported**. Incomplete recovery remains visible and requires human intervention. Create-only provisioning is not universal rollback.

### Evidence and limits

| Situation | Scope |
| --- | --- |
| Exercised on real lab targets | Portal, catalogue, roles, approval, NGINX/Apache deployment, checks and previous-version restoration. |
| Locally tested; external integration still requires acceptance | Notifications, messaging approval, remote Git publication and execution-secret injection. |
| Separate provisioning contracts | VMware OVF import is create-only; blank-disk vSphere and libvirt paths retain their documented blockers/planned state. Local tests do not prove target OS or SSH startup. |
| Prepared but not validated on real targets | Persistent Redis, Cisco/pfSense and cloud placeholders. |
| Outside the delivered scope | Public marketplace, payments, multi-organization SaaS and execution of untrusted third-party code. |

Passing tests does not establish production maturity. KuberWeaver does not orchestrate Kubernetes merely because of its name. Cost estimates are not invoices; graph edges order deployments without configuring networks. PostgreSQL is supported and selected by the documented local service; see its recovery guide rather than assuming SQLite is current or an automatic fallback.

<!-- pagebreak -->

## 2. Required platform guarantees

Preserve and verify this baseline during acceptance. Historical IDs remain stable: CRT-001 corresponds to EXG-001, and so on.

| Reference | Required function | Acceptance criterion |
| --- | --- | --- |
| EXG-001 | Enforce roles server-side. | Viewers cannot submit/approve; operators cannot administer, including through direct API calls. |
| EXG-002 | Discover service templates. | Valid templates appear after reload; invalid templates are reported without hiding valid peers. |
| EXG-003 | Compose and save a graph. | Reload preserves nodes, configuration, positions and dependencies; cycles are rejected. |
| EXG-004 | Generate forms from templates. | Defaults/constraints are displayed; invalid values are also rejected server-side. |
| EXG-005 | Enforce policy, targets and quotas. | Unknown targets, excess quotas, forbidden configuration and arbitrary commands are blocked before mutation. |
| EXG-006 | Approve the exact plan. | Decision, actor and time persist; changes to the plan or approved sources block the affected execution. |
| EXG-007 | Trace each change. | Sanitized plan, actual Git version, decision and execution outcome are retrievable. |
| EXG-008 | Persist operations/history. | History survives restart; interrupted work awaits review without automatic replay. |
| EXG-009 | Deploy progressively. | Two actual targets exercise backup, canary, checks and rollout, with independent service verification. |
| EXG-010 | Recover after failure where supported. | Modified targets regain their prior state, including absence; failed restoration is never shown as success. Manual rollback is offered only when authorized and eligible. |
| EXG-011 | Display observed inventory. | Unavailable targets are explicit, with no fabricated results or confusion with desired graphs. |
| EXG-012 | Show cost before approval. | Total, per-resource breakdown, currency and estimated nature match server calculation. |
| EXG-013 | Govern extension activation. | Disabled code is not loaded; failed extensions do not break the portal or leave partial registrations. |

Automated tests do not replace actual resource acceptance when proving deployment or restoration. A service-specific rollback guarantee must not be generalized to providers advertising no rollback.

<!-- pagebreak -->

## 3. Proposed next work and options

The four “Next” entries are proposed priorities after consolidating the baseline. Options require explicit team/mentor selection. Naming an owner does not create a delivery commitment or imply the function is absent/present in every integration.

| Reference | Intended outcome | Acceptance criterion |
| --- | --- | --- |
| EXG-014 · Next | Add another service category. | Deploy, verify and restore without kernel business changes; document data-retention limits. |
| EXG-015 · Next | Use a secret vault for execution. | A real deployment obtains a least-privilege secret; expiry/revocation fail cleanly without disclosure. |
| EXG-016 · Next | Send an actual notification. | Recipient receives the event; failures and duplicate risks are explicit. |
| EXG-017 · Next | Observe portal operations. | Health, operations and errors are readable; controlled failure is visible and retention documented. |
| EXG-021 · Preserve | Permit external-agent control. | Real MCP exchanges read/submit through the API; insufficient grants, expiry and revocation deny access. Approval requires explicit delegation. |
| EXG-018 · Option | Provision through OpenTofu. | Approve the exact saved plan, create, independently verify and separately authorize deletion; protect state. A create-only provider must state its narrower scope. |
| EXG-019 · Option | Configure a network device. | Authorized change and restoration preserve a recovery access path. |
| EXG-020 · Option | Approve through messaging. | Authenticated actor binds to the exact plan; repeated, expired or unauthorized actions are rejected/audited. |
| EXG-022 · Option | Publish plans to remote Git. | Destination receives the expected sanitized version; publication failures/divergence remain visible. |
| EXG-023 · Option | Use measured consumption. | Each measurement identifies source, period and freshness; it remains distinct from estimates/invoices. |
| EXG-024 · Long term | Distribute third-party extensions. | Define/test provenance, integrity, versions, checks, revocation and isolation before untrusted execution. |

### Technical model

React/TypeScript uses a FastAPI API; PostgreSQL or explicitly selected SQLite preserves state, Git records approved versions and Ansible executes reviewed web-service changes on GNS3 guests. Replaceable providers handle inventory, execution, secrets and notifications. Plugins add capabilities; compatible templates feed catalogue/forms, not automatic specialized pages. No built-in AI model is required.

<!-- pagebreak -->

## 4. Organization and safety

### Reference team

The reference roles remain unchanged in substance. Practical allocation is proposed; security is shared across the team, with independent review of sensitive changes.

| Person | Reference role | Proposed principal contribution |
| --- | --- | --- |
| Etienne MURZEAU | Project lead / submitter, AI / infrastructure integration | Coordination, agent integration, architectural consistency and acceptance presentation; optional read-only AI diagnostics. |
| Rayan CHOUCHANE | Terraform / Ansible infrastructure + GitOPS, lint/container pipeline | Versioning, automated checks and delivery pipeline. |
| Alex YAO | Terraform / Ansible infrastructure | Resources, inventory and target preparation. |
| Vidjay VELAYOUDAM | FinOps / Business | Business needs, cost model and economic analysis. |
| Abdel-Waheb SAKKAL | Terraform / Ansible infrastructure | Services, extensions, verification and recovery. |
| Priyank SOLANKI | Full Stack web developer | Portal, API, authorization and user workflow. |

### Safety and quality rules

- Restrict actions to authorized targets and owned resources. Do not change production for acceptance.
- Keep secrets out of code, shared plans, logs and captures. Use least-privilege access; require authorization before sending infrastructure data to external AI.
- Explicitly trust executable extensions. Current Python extensions have server privileges and do not isolate untrusted code.
- Back up before mutation, verify outcomes and provide manual recovery. Container restoration does not prove transactional data consistency. PostgreSQL and vault key/ciphertext require their own backups and restoration tests.
- Maintain verified TLS or explicitly confirmed socket-level pins where supported. One driver's pin does not bypass another provider's PKI requirements.
- Provide understandable errors and status indicators beyond color. Measure performance before setting response-time commitments.
- Managed SSH-key creation/selection/disabling/export is separate from guest revocation or automatic remote rotation. Export requires explicit administrator/password confirmation; see `SSH_KEYS.md`.

<!-- pagebreak -->

## 5. Delivery and decisions

### Milestones and artifacts

1. Validate selected scope and acceptance criteria with the responsible team/mentor.
2. Have another operator reproduce the web-service flow and restoration.
3. Integrate the chosen service and next-work items, then an option only if resources permit.
4. Deliver code, service templates, installation/recovery procedures, test records and explicit limitations.

Acceptance must demonstrate a blocked forbidden request, an actually available service, independently verified recovery where supported and extension without kernel business changes. Each selected requirement needs evidence; deviations are corrected or explicitly accepted. A completed implementation plan is not itself evidence of a delivered capability.

### Open decisions

Choose the next service/optional integration; confirm tasks, dates, capacity and licenses. Set retention and decide whether the requester may approve their own change. Main risks are scope dispersion, data loss, lost network access and secret disclosure.

[Architecture direction](ARCHITECTURE_CIBLE.md) distinguishes completed PostgreSQL storage work from proposed executor separation, service lifecycle and package distribution. Planned architecture does not become delivered functionality through documentation.

### Terms and references

API is the programming interface; MCP is the external-agent protocol. FinOps covers economic resource management. AI, GitOPS, lint and Full Stack retain their technical meanings; rollback means a service-appropriate return to earlier state, not guaranteed universal undo.

Sources: original need/vision and the repository README, `COMPLETION_STATUS.md`, `EXECUTION.md`, `EXTENSIONS.md`. The 6 November 2026 report is historical, not fresh verification. Detailed contracts remain in those guides. Earlier specification: `archives/CAHIER_DES_CHARGES_KUBERWEAVER_v1.3.md`. Proposed decisions require human approval.
