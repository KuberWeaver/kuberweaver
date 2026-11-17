# Sessions, CSRF and login throttling

A human signs in once and receives a `kw_session` cookie. The cookie is the only
credential the browser sends; every state-changing request must also carry the
`X-CSRF-Token` bound to that session. Machine access uses separate bearer
tokens, documented in [agent access](AGENT_ACCESS.md); a request carrying both a
cookie and an `Authorization` header is refused as ambiguous.

## Cookie

| Attribute | Value |
| --- | --- |
| Name | `kw_session` |
| `HttpOnly` | always |
| `SameSite` | `Strict` |
| `Path` | `/` |
| `Secure` | set when the request arrived over HTTPS |
| `Max-Age` | `28800` (the absolute timeout) |

The cookie value is not the database key. The store keeps only the SHA-256 of
the token, so a database read cannot be replayed as a cookie.

## Token format and absolute timeout

A token is `<issued-at>.<secret>`: the issue time in whole seconds, a dot, and
256 bits of randomness. The hash covers the whole string, so the issue time
cannot be edited without invalidating the row, and the absolute deadline stays
derivable after a restart.

The absolute timeout is **8 hours** from issue. A session older than that is
refused and deleted even when it was used a minute earlier. Tokens issued before
this format existed do not parse and are refused; the upgrade invalidates live
sessions and users sign in again.

## Idle timeout

The idle timeout is **30 minutes** without a request. The `sessions.expires`
column holds the idle deadline. A request that arrives inside the last minute of
the window pushes the deadline forward by another full window, capped at the
absolute deadline. This keeps a session alive while it is in use and bounds
writes to roughly one per idle period rather than one per request.

An expired session is deleted when it is presented, and the response clears the
cookie with `Max-Age=0`. Expired rows are also pruned on the next successful
login. A session that is never presented again keeps its row until that prune;
it cannot be used in the meantime.

## CSRF

`POST /api/auth/login` returns a 32-byte `csrf_token`, and `GET /api/auth/me`
returns the current one. Every non-idempotent `/api` request except login and
the signed ChatOps callback must send it as `X-CSRF-Token`. The comparison is
constant-time. A token from another session is refused, so possession of a CSRF
token alone grants nothing. The session cookie is `SameSite=Strict`, which
already blocks cross-site form posts in current browsers; the header is the
second, explicit gate.

## Login and logout

- A successful login replaces any session presented in the same request instead
  of promoting it. This closes session fixation.
- `POST /api/auth/logout` deletes the session row and clears the cookie. A
  replayed cookie is refused because the row no longer exists.
- Rotating a password revokes every session of that account; a self rotation
  issues a fresh one to the caller (see [users](USERS.md)).

## Throttling

Failed logins are counted in two sliding windows of 60 seconds:

| Bucket | Limit | Purpose |
| --- | --- | --- |
| client and account | 10 failures | A single account cannot be guessed at |
| client | 30 failures | One source cannot spray guesses across accounts |

The eleventh failure for a pair, or the thirty-first for a client, returns
`429` with `Retry-After` in seconds. The check runs before the password is
verified, so a blocked request is refused whether or not the password is
correct. Unknown accounts are counted exactly like wrong passwords and return
the same `401 Invalid credentials`, which keeps the endpoint from confirming
which names exist. A successful login clears the pair bucket; the per-client
bucket keeps its failures for the rest of the window.

The limiter holds timestamps only, drops entries older than the window, and is
capped at 1024 keys, so unauthenticated traffic cannot grow it without bound.
It is per process: the control plane runs one server worker per state directory,
so a restart clears the counters.

## Audit

| Action | When |
| --- | --- |
| `auth.login` | Credentials accepted, before the cookie is returned |
| `auth.login_failed` | Unknown account or wrong password, with the attempted name |
| `auth.login_throttled` | A request refused by the limiter before verification |
| `auth.logout` | Session deleted through the API |

Passwords, hashes and session tokens never appear in audit detail.

## Code

`sessions.py` owns token creation, resolution, revocation and cookie
attributes. `login_throttle.py` owns the two windows. The app wires them in
`app.py`; no route reads the `sessions` table directly.
