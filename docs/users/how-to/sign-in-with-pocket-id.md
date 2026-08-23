# Sign in with Pocket ID

Pocket ID is the only normal sign-in method for Odoo and Documents.

1. Open Odoo and select **Continue with Pocket ID**.
2. Authenticate in Pocket ID with your passkey.
3. Return to the existing Odoo user and its assigned companies and roles.

For local QA, an operator can generate a temporary Pocket ID link with
`make login-link USER=<username>`. This tests the real SSO flow; it is not an
Odoo password and is not used in production.

If access is refused, confirm that the intended Pocket account is active and
belongs to the allowed group. Odoo never creates a user during login. Ask an
administrator to link or preapprove the existing Odoo identity.

Signing out closes the Odoo session and, when supported by Pocket ID, its SSO
session. A provider outage never enables an Odoo password fallback.
