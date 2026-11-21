# Security policy

## Status and scope

KuberWeaver is an early-stage prototype. The repository is private today and has
no external users. This file states what the project does and does not protect,
which versions receive fixes, and how to report a vulnerability privately.

The documented installation binds the API to a workstation loopback address. It
is not a multi-tenant service. There is no per-object authorization beyond the
three local roles (administrator, operator, viewer), and there is no sandbox
around installed extensions. Browser sign-in through an external OpenID Connect
provider exists but is optional and disabled by default; it does not replace
local accounts, and roles always belong to those accounts.

Run KuberWeaver behind an authenticated HTTPS boundary that you operate and have
reviewed. Do not expose the loopback service directly to a network you do not
control, and do not treat a successful guest probe as proof of a hardened host.

## Supported versions

Only the tip of `main` receives security fixes. There is no long-term support
branch, no backport process and no service level agreement. Older commits and
untagged revisions are not maintained. A fix reaches `main` when a maintainer has
time to review it, with no promised timeline and no notification guarantee.

## Reporting a vulnerability

Report privately. Never open a public issue or discussion for a suspected
vulnerability, even a partial one or one you believe is theoretical.

1. Open a private vulnerability report from the repository Security tab
   ("Report a vulnerability"), which creates a draft advisory visible only to
   maintainers.
2. If you cannot use that form, contact the maintainer through a private channel
   listed on the repository owner's GitHub profile.

Include the affected commit or version, the component, minimal reproduction
steps, the observed impact and a suggested mitigation if you have one. Do not
include credentials, private keys or real third-party data.

There is no guaranteed acknowledgement time and no guaranteed fix time: this is a
volunteer-maintained prototype. You can help by proposing a patch, but a report
alone is still useful.

## Secret handling

Never commit:

- `data/`: runtime state, configuration, snapshots, GitOps plans, vault keys and
  bootstrap credential files.
- `docs/prompt/`: private notes.
- `*.pem`, `*.key`, `*.p12`, `*.pfx`, tokens and `.env` files.

`ops/ci/secret_scan.py` blocks these paths and several credential patterns in CI,
including a history scan (`--history`). Treat any secret that reached Git as
compromised: rotate it first, then remove it. Deleting the file or rewriting the
commit does not unexpose it. Local vault encryption protects ciphertext at rest,
not against compromise of the OS account or root.

## Trust boundaries to know before deploying

- Extensions execute inside the server process with its OS permissions. There is
  no sandbox, no signature verification, no remote fetching and no dependency
  solver. Install and enable only code you reviewed and trust.
- Guest root accounts and workstation administrators remain trusted. The kernel
  stages private keys for execution and removes them afterwards, but it does not
  turn an untrusted host into a trusted one.
- Passwords are stored as Argon2id hashes. Session cookies carry the flags
  described in `docs/SESSIONS.md`, CSRF protection is bound to the session, role
  checks are enforced in the kernel and authenticated actions leave audit rows.
  These are deliberate measures, not a certification and not a guarantee.
- Secrets providers keep custody local. Transport boundaries and OS-level
  compromise are outside their protection.
- The README capability table lists what each feature does not cover. Read it
  before assuming a control exists.

## Out of scope

No bug bounty, no paid incident response, no CVE assignment on request, and no
support for infrastructure KuberWeaver did not touch. Reports about third-party
components vendored under `plugins/*/roles/` or `plugins/*/collections/` go to
their upstream projects.
