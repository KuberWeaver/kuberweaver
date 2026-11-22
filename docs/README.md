# Documentation index

Every `docs/<NAME>.md` page states the contract of one subsystem, or records a
decision. Read only the page for what you are changing. If a page contradicts the
code, the code wins and the page is wrong: fix it in the same change.

## Start here

| Page | What it answers |
| --- | --- |
| `README.md` (root) | what the project is, what it does not do, how to install and verify |
| `CONTRIBUTING.md` (root) | how to change it safely, branches, checks, commit rules |
| `SECURITY.md` (root) | threat model, supported revisions, private reporting |
| `SUPPORT.md` (root) | where to ask, what is supported, expected response |
| `docs/AGENT_GUIDE.md` | the working contract for an AI coding agent in this repository |

## Product and contracts

| Page | Scope |
| --- | --- |
| `docs/EXECUTION.md` | manifest-driven execution, real transaction semantics, ownership, evidence |
| `docs/WORKLOADS.md` | workload bricks: what a brick must declare, how one is validated |
| `docs/PRESETS.md` | multi-brick stacks as data, one job per step |
| `docs/EXTENSIONS.md` | Python extension protocol v1, providers and hooks |
| `docs/MCP.md` | MCP server surface and its limits |
| `docs/STATIC_SITE.md` | serving the `site/` showcase through KuberWeaver itself |
| `docs/DISCORD.md` | intended community server structure |
| `docs/OIDC.md` | optional OpenID Connect sign-in |
| `docs/SSH_KEYS.md` | managed SSH keys: generation, delivery, export |

## Infrastructure and operations

| Page | Scope |
| --- | --- |
| `docs/POSTGRESQL.md` | durable PostgreSQL store, connection URLs, backups |
| `docs/REMOTE_GITOPS.md` | pushing an approved artifact to a remote, limits |
| `docs/DECLARATIONS.md` | git-backed declarations verified against one commit |
| `docs/DRIFT.md` | read-only nightly drift reports |
| `docs/NOTIFICATIONS.md` | delivery state, providers, retries |
| `docs/VERIFICATION.md` | what has actually been executed and recorded as evidence |

## Decisions, not instructions

These pages record why something was done, or the prototype's own plan. They are
not current capability statements and they are not user documentation.

- `docs/plans/` — dated plans, including this community pass.
- `docs/CLEANUP.md` — internal note on prototype entries that may be deleted.
- `docs/THEMING.md`, `docs/NAVIGATION.md`, `docs/SERVICE_STUDIO.md` historical
  sections describing the removed React portal.
- `docs/cadrage-2026-11/`, `docs/cahier-des-charges-kw-cli.md`,
  `presentations/` — school project artifacts, kept for provenance. They are not
  part of the public documentation and can be removed before publication without
  changing the product.

## Conventions

Public documentation is in English. Commit messages and code comments may be in
any language, but the subject line of a commit follows
`type(area): what`.
