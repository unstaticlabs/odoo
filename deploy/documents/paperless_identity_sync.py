"""Reconcile governed Pocket identities into Paperless.

Run through ``manage.py shell`` in the pinned Paperless image. Odoo remains
the authorization authority; this creates only the individual account and
immutable OpenID Connect link needed to receive Odoo's object grants.
"""

# This script runs inside ``manage.py shell``. Literal fail-closed errors and
# its machine-readable stdout marker are part of the deployment contract.
# ruff: noqa: EM101, I001, T201

import json
import os

from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction


provider_id = os.environ.get("PAPERLESS_OIDC_PROVIDER_ID", "pocket-id").strip()
group_name = os.environ["PAPERLESS_SSO_BASE_GROUP"].strip()
raw_plan = os.environ["PAPERLESS_IDENTITY_PLAN_JSON"].strip()
if not provider_id or not group_name or not raw_plan:
    raise RuntimeError("Paperless identity synchronization is not configured")

try:
    plan = json.loads(raw_plan)
except json.JSONDecodeError as error:
    raise RuntimeError("Paperless identity plan is invalid JSON") from error
if not isinstance(plan, list) or not plan:
    raise RuntimeError("Paperless identity plan must contain at least one user")

apps = [
    app
    for app in get_adapter().list_apps(None)
    if app.provider_id == provider_id
]
if len(apps) != 1:
    raise RuntimeError("The configured Paperless Pocket provider is ambiguous")

User = get_user_model()
base_group = Group.objects.get(name=group_name)
required_keys = {"subject", "username", "email", "display_name"}
subjects = set()
usernames = set()
emails = set()
normalized = []
for item in plan:
    if not isinstance(item, dict) or set(item) != required_keys:
        raise RuntimeError("A Paperless identity plan entry is incomplete")
    values = {
        key: str(item[key]).strip()
        for key in required_keys
    }
    if not all(values.values()):
        raise RuntimeError("A Paperless identity plan value is empty")
    subject = values["subject"]
    username = values["username"]
    email_key = values["email"].casefold()
    if subject in subjects or username.casefold() in usernames or email_key in emails:
        raise RuntimeError("The Paperless identity plan contains a duplicate")
    subjects.add(subject)
    usernames.add(username.casefold())
    emails.add(email_key)
    normalized.append(values)


def _exactly_one_candidate(values):
    account = SocialAccount.objects.filter(
        provider=provider_id,
        uid=values["subject"],
    ).select_related("user").first()
    if account:
        return account.user, account

    username_users = list(User.objects.filter(username=values["username"]))
    email_users = list(User.objects.filter(email__iexact=values["email"]))
    candidates = {user.pk: user for user in username_users + email_users}
    if len(candidates) > 1:
        raise RuntimeError(
            f"Paperless username/email disagree for {values['username']!r}",
        )
    user = next(iter(candidates.values()), None)
    if user is None:
        # Older release cohorts sanitized the Django user row but accidentally
        # retained its allauth email row. Reclaim only that exact, sealed
        # placeholder. Any active, privileged, or otherwise named owner remains
        # an ambiguity and fails closed below.
        email_candidates = {
            item.user_id: item.user
            for item in EmailAddress.objects.filter(
                email__iexact=values["email"],
            ).select_related("user")
        }
        if len(email_candidates) > 1:
            raise RuntimeError(
                f"Paperless email {values['email']!r} has multiple owners",
            )
        candidate = next(iter(email_candidates.values()), None)
        if candidate is not None:
            expected_username = f"release-disabled-{candidate.pk}"
            if (
                candidate.username != expected_username
                or candidate.is_active
                or candidate.is_staff
                or candidate.is_superuser
                or candidate.has_usable_password()
            ):
                raise RuntimeError("Paperless identity ownership is ambiguous")
            user = candidate
    return user, None


results = []
with transaction.atomic():
    for values in normalized:
        user, social_account = _exactly_one_candidate(values)
        username_owner = User.objects.filter(username=values["username"]).first()
        if username_owner and user and username_owner.pk != user.pk:
            raise RuntimeError(
                f"Paperless username {values['username']!r} is already owned",
            )
        email_owners = {
            item.user_id
            for item in EmailAddress.objects.filter(email__iexact=values["email"])
        }
        email_owners.update(
            User.objects.filter(email__iexact=values["email"]).values_list(
                "pk",
                flat=True,
            ),
        )
        if user and email_owners - {user.pk}:
            raise RuntimeError(
                f"Paperless email {values['email']!r} is already owned",
            )
        if not user:
            if username_owner or email_owners:
                raise RuntimeError("Paperless identity ownership is ambiguous")
            user = User(username=values["username"])

        other_accounts = (
            SocialAccount.objects.filter(
                provider=provider_id,
                user=user,
            ).exclude(uid=values["subject"])
            if user.pk
            else SocialAccount.objects.none()
        )
        if other_accounts.exists():
            raise RuntimeError(
                f"Paperless user {values['username']!r} has another Pocket identity",
            )

        user.username = values["username"]
        user.email = values["email"]
        user.first_name = values["display_name"]
        user.last_name = ""
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()
        user.groups.add(base_group)

        EmailAddress.objects.filter(user=user).exclude(
            email__iexact=values["email"],
        ).update(primary=False)
        email_address = EmailAddress.objects.filter(
            user=user,
            email__iexact=values["email"],
        ).first()
        if not email_address:
            email_address = EmailAddress(user=user, email=values["email"])
        email_address.email = values["email"]
        email_address.verified = True
        email_address.primary = True
        email_address.save()

        extra_data = {
            "sub": values["subject"],
            "preferred_username": values["username"],
            "email": values["email"],
            "email_verified": True,
            "name": values["display_name"],
            "usl_odoo_managed": True,
        }
        if social_account:
            social_account.user = user
            social_account.extra_data = extra_data
            social_account.save(update_fields=["user", "extra_data"])
        else:
            social_account = SocialAccount.objects.create(
                user=user,
                provider=provider_id,
                uid=values["subject"],
                extra_data=extra_data,
            )
        results.append(
            {
                "subject": values["subject"],
                "username": values["username"],
                "paperless_user_id": user.pk,
            },
        )

    stale_accounts = SocialAccount.objects.filter(
        provider=provider_id,
        extra_data__usl_odoo_managed=True,
    ).exclude(uid__in=subjects).select_related("user")
    for account in stale_accounts:
        account.user.groups.remove(base_group)
        account.user.is_active = False
        account.user.save(update_fields=["is_active"])

print(
    "USL_PAPERLESS_IDENTITIES="
    + json.dumps(results, separators=(",", ":"), sort_keys=True),
)
