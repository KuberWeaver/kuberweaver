# opentofu-runtime

Execution provider that runs one saved OpenTofu plan against a single stopped
libvirt lab VM. It is an opt-in boundary, not the default execution path.

## Contract

| Field | Value |
| --- | --- |
| `id` | `opentofu-runtime` |
| `api_version` | `1` |
| `capabilities` | `providers:execution`, `data:read-write` |

The plan must already be saved and pinned. The provider applies it as-is; it does
not run `init`, does not generate a new plan and does not retry after an ambiguous
failure. See `docs/DECLARATIONS.md` for how a declaration is verified against one
commit before execution.

## Limits stated up front

- Not live-certified. No real run of this path is recorded as evidence here.
- One VM at a time; it is not a scheduler for a fleet.
- A failed or interrupted apply requires manual review, not an automatic re-run.

Enable it only if you accept those limits. The same provider can be replaced by a
different execution extension without touching the kernel.
