# USL Platform Billing Pocket ID

This small auto-installed bridge keeps authorization ownership explicit when
both `usl_platform_billing` and `usl_pocketid` are installed.

It adds the Platform Billing Administrator group only to the governed Pocket
ID `administrator` and local `break_glass` profiles. Collaborators, Accounting
reviewers, ordinary Accountants and portal users do not receive application
access.

Keeping this mapping outside both parent modules avoids a reverse dependency:
Platform Billing remains installable without SSO, and Pocket ID remains
installable without Accounting.
