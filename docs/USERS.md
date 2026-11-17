# Users, roles and password policy

KuberWeaver stores human accounts in the same durable store as the rest of the
control plane: the `users` table holds `id`, `username`, `role` and
`password_hash`. Usernames are lowercase, 3 to 64 characters, start with a
letter and may then contain letters, digits, `.`, `_` or `-`. They are unique
case-insensitively.

## Password storage

Passwords are hashed with **Argon2id** using `m=19456` KiB, `t=2`, `p=1`, a
16-byte random salt and a 32-byte tag. The encoded string is the only value
persisted; plaintext passwords never are. Each hash uses a fresh salt, so two
accounts with the same password have different digests.

Stores created before Argon2id contain `pbkdf2_sha256` verifiers. They remain
valid: a successful login verifies with PBKDF2, immediately rewrites the row
with an Argon2id hash, and the next request uses the stronger verifier. No
offline migration and no password reset are required.

## Roles

| Role | Portal authority |
| --- | --- |
| `admin` | Policies, providers, extensions, approvals, rollbacks, credentials, user management |
| `operator` | Compose, save and submit requests; no approval, rollback or user management |
| `viewer` | Read-only access |

The role is stored on the account and re-read on every request through the
session join, so a role change applies to existing sessions immediately.

## Bootstrap accounts

The first startup against an empty store creates one `admin`, one `operator` and
one `viewer` account, each with an independent 32-character random password. The
passwords are written once to `bootstrap-credentials.txt` in the private state
directory (`0600`, directory `0700`), one `username:password` line per account.
The file is the only copy: it is never logged, returned by the API or embedded in
the frontend.

The file is single-use. When an account password is rotated, by the account
itself or by an administrator, its line is removed from the file; when the last
line is removed, the file is deleted. A file that cannot be read as a private
regular file owned by the service user makes the rotation fail with `409` rather
than silently leaving a valid plaintext credential behind.

## Password policy

Every creation and every rotation is checked before hashing:

| Rule | Refusal reason |
| --- | --- |
| Not empty or whitespace-only | `blank` |
| At least 12 characters | `too_short` |
| At most 512 characters | `too_long` |
| Not in the built-in common-password list | `common` |
| Does not contain the username (3+ characters) | `contains_username` |
| At least 4 distinct characters | `repetitive` |

There is deliberately no character-class rule: a long passphrase without digits
or symbols is accepted. The list is local and small; pair it with a
breach-corpus check at deployment if one is available. A refusal never echoes
the submitted password.

## HTTP contract

All routes require a human browser session with `X-CSRF-Token`, except the
password route which also accepts the account itself. Agent tokens cannot call
any of them: `agent_auth.required_scope` maps no scope to `/api/users`.

### `GET /api/users` (admin)

Returns `{"users":[{"id","username","role"}],"roles":["admin","operator","viewer"]}`.
Password hashes are never included.

### `POST /api/users` (admin)

```json
{"username":"deploy.bot","password":"tidal harbour lantern 47","role":"operator"}
```

Returns `201` with `{"user":{"id","username","role"}}`. The username is
normalized to lowercase. Refusals: `409` duplicate username (case-insensitive),
`422` invalid username, unknown role or weak password.

### `PATCH /api/users/{id}` (admin)

```json
{"role":"viewer"}
```

Returns `200` with the updated metadata. Refuses `409` when the change would
remove the last administrator, `404` for an unknown id, `422` for an unknown
role.

### `POST /api/users/{id}/password`

```json
{"current_password":"...","new_password":"..."}
```

An account rotating its own password must supply the current one; a wrong or
missing value returns `403`. An administrator rotating another account omits
`current_password`, and a non-admin attempting it gets `403`. Reusing the
current password returns `422`. On success every existing session of that
account is revoked; when the caller rotated its own password the response
carries a fresh `csrf_token` and sets a new `kw_session` cookie, so the browser
stays signed in while every other session is invalidated.

Audit records are `user.created`, `user.role_changed` and
`user.password_rotated`, with the actor, the target id and the role or the
`self` flag. Passwords and hashes never appear in audit detail.
