# redis-runtime

Trusted lifecycle provider for an isolated-lab Redis: start, stop and health check
on one guest. It is an example of a workload execution provider, not a catalogue
entry.

## Contract

| Field | Value |
| --- | --- |
| `id` | `redis-runtime` |
| `api_version` | `1` |
| `capabilities` | `providers:execution`, `data:read-write` |

It is trusted administrator code, like every extension. Review the host package it
installs and the blueprint it runs before enabling it.

## Why this exists

To show that an execution provider can be an extension rather than a plugin
directory. The kernel calls the registered provider; it does not branch on an ID.
A provider that cannot verify its result must refuse instead of reporting success.

Read `docs/AUTHORING.md` first if you intend to write one.
