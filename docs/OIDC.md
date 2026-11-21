# Single sign-on (OpenID Connect)

KuberWeaver can delegate browser sign-in to an external OpenID Connect provider
(Authentik, Keycloak, Entra ID and similar). The feature is optional and
**disabled by default**: with no `KUBERWEAVER_OIDC_ISSUER`, the portal behaves
exactly as before and local accounts keep signing in with a password.

The provider never replaces the password path. `POST /api/auth/login` is
unchanged, local accounts remain the only accounts, and the identity provider
only proves which person is at the browser. Roles are owned by the local account
and are never read from a token.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `KUBERWEAVER_OIDC_ISSUER` | unset | Issuer URL of the provider, for example `https://authentik.example.test/application/o/kuberweaver/`. Setting it enables the feature. |
| `KUBERWEAVER_OIDC_CLIENT_ID` | unset | Client identifier registered at the provider. Required when the issuer is set. |
| `KUBERWEAVER_OIDC_CLIENT_SECRET` | unset | Client secret. Required when the issuer is set; it is only sent to the token endpoint and never logged. |
| `KUBERWEAVER_OIDC_REDIRECT_URL` | derived from the request | Exact callback URL registered at the provider, for example `https://portal.example.test/api/auth/oidc/callback`. Set it when the portal runs behind a reverse proxy, because the derived URL follows the request scheme and host. |
| `KUBERWEAVER_OIDC_SCOPES` | `openid profile email` | Space-separated scopes; `openid` is mandatory. |
| `KUBERWEAVER_OIDC_USERNAME_CLAIM` | `preferred_username` | Claim that names the local account. |
| `KUBERWEAVER_OIDC_LABEL` | `Single sign-on` | Button text advertised by `GET /api/auth/oidc/status`. |
| `KUBERWEAVER_OIDC_ALGORITHMS` | `RS256` | Comma-separated ID-token signature algorithms. `RS256`, `RS384`, `RS512`, `ES256`, `ES384` and `ES512` are accepted; symmetric and `none` algorithms are refused. |
| `KUBERWEAVER_OIDC_TIMEOUT` | `5` | Seconds allowed for discovery, JWKS and token requests, between 1 and 30. |

Issuer, redirect and discovered endpoints must be absolute `http(s)` URLs without
credentials, query or fragment. Cleartext `http` is only accepted for loopback
hosts, where a lab provider may run beside the portal. An issuer is compared
without its trailing slash.

A partial configuration is reported, not guessed: the portal still starts and
local password sign-in keeps working, `GET /api/auth/oidc/status` answers
`{"enabled": false, "reason": "configuration_error", "detail": "..."}` naming the
missing variable, and every OIDC route answers `503`.

## Endpoints

| Method and path | Behaviour |
| --- | --- |
| `GET /api/auth/oidc/status` | Public. `{"enabled": true, "label": "Single sign-on", "reason": null, "detail": null}`, or `enabled: false` with `reason` `not_configured` or `configuration_error`. |
| `GET /api/auth/oidc/start` | Public. Discovers the provider, stores a single-use sign-in attempt and redirects (`303`) to the authorization endpoint with `state`, `nonce` and an S256 PKCE challenge. Sets the short-lived `kw_oidc` state cookie (`HttpOnly`, `SameSite=Lax`, `Path=/api/auth/oidc`, 10 minutes, `Secure` over HTTPS). |
| `GET /api/auth/oidc/callback` | Public. Requires the `kw_oidc` cookie to match `state`, consumes the attempt, exchanges the code, verifies the ID token, links or resolves the local account, creates the normal `kw_session` cookie and redirects (`303`) to `/`. |

The callback is a `GET` because it is a top-level browser navigation from the
provider; the single-use `state` value plus the matching state cookie are its
CSRF protection. The state cookie is `SameSite=Lax` so the provider's redirect
can carry it back, while a callback URL replayed in another browser has no
matching cookie and is refused. The session cookie keeps the flags described in
[sessions](SESSIONS.md), including `SameSite=Strict` and `Secure` over HTTPS. The
frontend reads the CSRF token from `GET /api/auth/me` after the redirect, exactly
like a password login.

## Token and claim checks

The ID token is verified before any account is touched:

- signature against the provider's JWKS, using only the configured algorithms,
  with one forced key refresh when the token names an unknown `kid`;
- `iss` equal to the configured issuer (compared without a trailing slash),
  `aud` containing the client identifier, and a valid `exp`/`iat`;
- `nonce` equal to the value generated for this attempt, and `at_hash` equal to
  the returned access token when the token carries one;
- `sub` present and usable.

The username claim is lowercased and must match the local account pattern
(`^[a-z][a-z0-9._-]{2,63}$`). It is used only on the first sign-in:

- unknown username: `403`, no session, audit `auth.oidc.login_failed` with reason
  `account_refused`;
- known username, first sign-in: the `(issuer, subject)` pair is bound to that
  local account, audit `auth.oidc.linked`;
- later sign-ins: the stored binding wins, so a renamed claim cannot move the
  session to another local account, and a second subject claiming the same
  username is refused;
- bound account deleted: refused, never rebound silently.

No account is created from a token. Provision local accounts first with
`POST /api/users` or the seeded bootstrap accounts, then let the provider sign
them in.

## Audit

Every completed sign-in writes `auth.oidc.login` with actor and resource set to
the local account. Refusals that consumed a sign-in attempt write
`auth.oidc.login_failed` with `actor` set to `anonymous` and a reason code:
`token_invalid`, `account_refused` or `provider_unavailable`. A callback with an
unknown or replayed state is answered without an audit row, so unauthenticated
random-state requests cannot grow the audit table. Tokens, authorization codes,
the client secret and upstream error bodies never enter an audit row.

## Operational limits

- One provider at a time. Multiple providers would need a selection mechanism
  and are out of scope.
- Pending sign-in attempts live in process memory: 10 minutes, 1024 entries,
  pruned on each start. A restart invalidates attempts in flight, and the server
  already runs one worker per state directory.
- Discovery and JWKS are cached for five minutes; a failed discovery is not
  cached.
- There is no refresh token, no RP-initiated logout and no back-channel logout.
  Logging out deletes the local session only; the provider session remains until
  it is ended at the provider.
- Password login stays available for local administrators even when the provider
  is enabled, so a broken provider cannot lock the portal.

## Provider registration

Register a confidential web client at the provider with the authorization-code
flow enabled, PKCE supported, and the exact redirect URL from
`KUBERWEAVER_OIDC_REDIRECT_URL`. The client secret is sent in the token request
body (`client_secret_post`). Required scopes are `openid profile email` (or at
least `openid` plus whatever claim carries the username). The client must
publish a discovery document at
`<issuer>/.well-known/openid-configuration` and an RS256 (or configured
algorithm) signing key in its JWKS. Host clocks on the portal and the provider
must be synchronized; token lifetime is checked without a skew allowance.
