# Security profiles (design note, not implemented)

Status: agreed direction, deliberately deferred until one Ubuntu VM is provisioned
end to end with a verified SSH login. Date: 2026-11-16.

## Problem

All admission and safety controls are currently hardcoded in
`backend/kuberweaver/policy.py` and in the worker guards. There is no
configuration surface at all, which makes the controller all-or-nothing: the
operator pays the full approval cost even for throwaway lab iterations. The
obvious fix, a settings page full of independent checkboxes, is rejected: the
controls are chained (plan digest depends on source pins, which depend on the
reviewed config, which depends on the key binding), so unchecking one link
silently voids the guarantee for the whole chain.

## Decision

Replace the choice space with three named **security profiles**. A profile is
declarative data, validated against a schema, not a set of code branches.

    lab        automatic approval, explicit discovery tolerated, short plan TTL,
               single-VM concurrency. For iteration and demos.
    guarded    human approval required, explicit discovery required, managed SSH
               key required, protected exports. Default.
    strict     guarded plus restricted exports, revocation checks, refusal of
               targets not bound to pinned connections. For the graded demo.

Selection lives in the connection manifest, or per environment. The resolved
profile is written into the prepared plan and into the digest, the same way the
SSH key binding already is. Changing the profile between review and apply
invalidates the reviewed plan and forces a new one.

## Invariants (never optional, never in a menu)

- No private key or secret material in plans, logs, API responses or Git.
- No implicit writes triggered by a read, discovery stays explicit.
- PostgreSQL required, no silent fallback.
- A changed TLS pin is a refusal, no "ignore certificate" switch.
- The plan is always computed, hashed and pinned, even when human approval is
  automatic.
- Disabling a managed key invalidates the plans that reference it.

## Implementation constraint

Providers must not branch on the profile. The profile is resolved once, in
admission, and produces a plain policy record that the executor consumes like
any other config value. If providers start testing `profile == "lab"`, the
feature has failed and becomes the tangled system it was meant to avoid.

The resolved profile is also readable by agents and MCP clients, which lets an
external agent declare its intent and have it recorded and bounded in the plan.

## Not in scope

- Individual per-control toggles.
- Presentation preferences (theme, verbosity, notifications), which do not
  belong in this discussion.
