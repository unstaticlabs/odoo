# Pocket ID SSO architecture

Status: implemented and locally validated

Decision date: 2026-07-29

Scope: Odoo Community `saas~19.3`, Pocket ID OIDC, every human user

## Decision

USL uses Pocket ID as the authentication authority and Odoo as the sole
authorization authority. The implementation combines:

1. the pinned OCA `auth_oidc` module as the maintained authorization-code OIDC
   foundation;
2. the isolated `usl_pocketid` add-on for issuer, state, nonce, per-request
   PKCE, key-selection, identity-linking and authorization-policy hardening;
3. environment-only provider credentials and explicit named-user policy;
4. one sealed Odoo emergency administrator that is never linked to Pocket ID.

The governed provider record rejects direct field changes and deletion.
Enablement, issuer, endpoints, client metadata and group gates are refreshed
only by the environment configuration helper; the client secret is never
stored in Odoo.

The OIDC redirect is exactly:

```text
https://<public-odoo-host>/auth_oauth/signin
```

Pocket ID claims prove identity. They never add an Odoo company or group.
Every successful login still resolves an existing, active, explicitly
governed Odoo user and uses that user's Odoo groups and allowed companies.
Installing the integration disables the bundled Odoo.com OAuth provider even
while Pocket ID is inert; activation enables only the governed Pocket ID
provider. With `USL_POCKET_ID_LOGIN_POLICY=sso_only`, Pocket ID is the only
normal interactive provider for internal and portal users. Odoo passwords,
local passkeys, signup, reset and alternate OAuth providers are rejected at
the backend. Bearer API keys remain available for governed integrations and
never fall back to a user password. Passkeys remain registered and verified
only in Pocket ID.

This separation follows Odoo's external API guidance: non-human clients use
revocable, expiring bearer API keys rather than a human account password:
[External API](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html).

## Alternatives considered

| Alternative | Strength | Reason not selected |
| --- | --- | --- |
| Native `auth_oauth` only | No external add-on | Its current flow is implicit-token oriented; nonce is not bound and explicit audience validation remains incomplete. It does not meet the security contract. |
| New Authlib-based OIDC client | Full local control | USL would own discovery, code exchange, JOSE, key rotation and protocol maintenance end to end. This is a larger security surface than the requested isolated hardening. |
| Pinned OCA `auth_oidc` plus USL hardening | Maintained code flow and narrow local policy | Selected. OCA supplies the protocol foundation; USL closes the environment- and identity-specific gaps without patching Odoo core or the vendored OCA source. |

The pinned OCA source is
[`OCA/server-auth` `auth_oidc`](https://github.com/OCA/server-auth/tree/19.0/auth_oidc).
Its exact commit is recorded in `scripts/sync-oca-addons` and
`docs/accounting/custom-addon-architecture.md`.

## Protocol and validation contract

The browser login transaction is stored only in the Odoo session and expires
after five minutes. It contains an opaque cryptographic state, nonce,
per-request PKCE verifier, database, provider, canonical callback and safe
relative post-login path. State is single-use.

The callback:

- exchanges only an authorization code;
- sends the exact registered redirect and per-request PKCE verifier;
- accepts only RS256 ID tokens;
- loads a bounded JWKS document from the discovered issuer origin;
- validates signature, exact issuer, client audience, expiry, issued-at,
  subject, nonce and `azp` where required;
- requires the configured Pocket ID group;
- rejects absolute or scheme-relative Odoo return URLs;
- emits a safe audit reason without storing the raw subject or token.

Discovery must advertise the code flow and RS256. The issuer and authorization,
token and JWKS endpoints must use the same HTTPS origin. HTTP is accepted only
for loopback addresses and RFC-reserved `.localhost` names in the isolated
local preproduction topology; staging and production require HTTPS.

## Identity model

The durable key is `(issuer, sub)`, unique across Odoo. A second uniqueness
constraint permits at most one identity from an issuer per Odoo user.
The stored audit fingerprint is a truncated SHA-256 fingerprint; the immutable
subject itself is visible only to system administrators.

Email can create the first link only when all of these conditions hold:

- the provider-wide environment flag permits it;
- Pocket ID asserts `email_verified=true`;
- exactly one active internal Odoo user matches the email exactly;
- that user has an explicit per-user first-link approval;
- that user has no identity for the issuer.

Unknown, unverified, ambiguous or conflicting identities are refused. Login
never creates a user, changes a profile email/name, or grants a group. A named
operator configuration may create a missing user only with
`create_if_missing=true`, an explicit name and an exact email.

Identity links and audit events cannot be deleted. Disable, archive and relink
operations preserve historical records and produce audit events. Disabling a
link clears the Odoo OAuth binding and token, invalidating sessions derived
from the old binding.

## Authorization profiles

Profile assignments use exact Odoo groups. Implied groups remain controlled by
Odoo's native group graph.

| Profile | Odoo groups | Company rule | Local password |
| --- | --- | --- | --- |
| `administrator` | Product administration plus Irreversible Actions | Explicit list or `all` | Randomized and denied while SSO-managed |
| `product_administrator` | Settings, Accounting, HR, Expenses and Project administration without Irreversible Actions | Explicit list or `all` | Randomized and denied while SSO-managed |
| `collaborator` | Internal user and Project user | Explicit list | Randomized and denied while SSO-managed |
| `accountant_reviewer` | Internal user and existing `USL Accountant Review` role | Explicit approved company list | Randomized and denied while SSO-managed |
| `break_glass` | Product administration plus Irreversible Actions | Explicit list or `all` | Sealed secret; accepted only through the time-limited emergency route |
| `portal` | Portal only | Explicit list | Pocket ID identity link or approved one-time verified-email link |
| `historical` | Existing groups retained for attribution | Existing scope retained | User archived; identity disabled |
| `decision` | Existing groups retained pending owner decision | Existing scope retained | User archived; identity disabled |

The external-accountant profile deliberately reuses
`rebuild_account_migration.group_rebuild_accountant_reviewer`. That role has
the existing company-scoped review surface and is composed with the
Distribution Accounting Reviewer role. The resulting profile may perform
reversible Accounting work in unlocked periods, but it does not receive
Accounting administration, lock management or permanent-deletion authority.

## Historical identity decisions

The read-only Odoo Online source inspection on 2026-07-29 found:

| Source identity | State | Target decision |
| --- | --- | --- |
| Valentin | Active internal; Unstatic Labs; system, accounting, HR, expense, project, sales, documents and signing administration | Active `administrator`; preserve the canonical imported partner and employee links |
| Roger | Active internal; all approved companies; Unstatic Labs remains the default company | Active `administrator` during initial configuration, then `product_administrator` |
| Yoshi SAS / Roger external address | Inactive shared/public-style user | `historical` if this user is present in the target; do not create it for SSO |
| Prosper | Not present in the source snapshot; the reconstruction workflow creates login `prosper` | Active `accountant_reviewer`; exact Pocket ID subject must be supplied by the owner |
| Public user and Portal User Template | Framework accounts | Protected framework records; never SSO-linked |
| OdooBot | Technical system account | Protected framework record; never SSO-linked |

Pocket ID policy applies only after users, companies and business data pass
access and release controls. SSO never weakens or mutates business records to
make authentication tests pass.

## Lifecycle and session policy

- First link: explicit immutable subject is preferred; controlled verified
  email is a one-time proposal only.
- Return login: issuer and subject resolve the existing link; email is profile
  metadata only.
- Pocket ID group removal: blocks the next authentication. The operator must
  also disable/archive the Odoo identity immediately to invalidate existing
  Odoo sessions; there is no claim-driven authorization downgrade.
- Odoo group removal: takes effect through the normal Odoo session-security
  token and remains authoritative.
- Archive or identity disable: clears the OAuth token, blocks new SSO and
  preserves business records.
- Email or display-name change: records last observed values on the identity
  but never rewrites the Odoo user or changes the durable link.
- Conflict: denies login and requires an explicit administrator decision.
- Pocket ID outage: existing Odoo sessions continue until normal expiry or
  revocation and new SSO login fails closed. Emergency access requires an
  incident-approved deployment flag and an expiry of at most one hour; the
  account never appears on the normal login page.

Sensitive Odoo actions reauthenticate against the same immutable Pocket ID
identity. A different Pocket person cannot approve the action. Logout clears
the Odoo session, then bridges through a same-origin page that sends the
browser to Pocket ID's end-session endpoint with the session-bound
`id_token_hint`. Pocket ID returns to the explicit SSO login page. Without a
stored ID token, logout stays on `/web/login` and does not open a broken
cross-origin fetch redirect.

Pocket ID client group membership is an authentication gate, not an Odoo role.
Pocket ID documents that a new client has no allowed groups by default and
must be configured deliberately:
[Allowed User Groups](https://pocket-id.org/docs/configuration/allowed-groups),
[OIDC client authentication](https://pocket-id.org/docs/guides/oidc-client-authentication).

Paperless-ngx is a second relying party, not a proxy for the Odoo session. It
uses its own confidential OIDC client and documented callback while resolving
the same Pocket person. Paperless group synchronization remains disabled;
Odoo's verified per-document grants are still authoritative. A Paperless-local
default group supplies only enough global capability for its UI and catalogs
to function; it does not make any document visible. The Odoo client secret,
Paperless client secret, and non-human Paperless API token are three separate
credentials.

## Activation state

The local runtime uses a digest-pinned Pocket ID tenant, uncommitted
credentials, stable immutable subjects, a restricted OIDC client and an
idempotent named-user policy. The local working database is authoritative and
must not be reset by identity tooling. Cross-application journeys have been
validated with administrator one-time links; production still requires the
approved HTTPS issuer, subjects, secret storage, passkey enrollment and
callback configuration. Local synthetic identifiers and
`preproduction.invalid` addresses must not be promoted.
