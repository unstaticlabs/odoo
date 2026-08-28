"""Provision Paperless's non-business SSO capability group.

Run through ``manage.py shell`` in the pinned Paperless image. The group only
allows the web application and shared catalogs to load. Odoo continues to
grant view/change access on each document object.
"""

import os

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.models import Group, Permission

group_name = os.environ["PAPERLESS_SSO_BASE_GROUP"].strip()
if not group_name:
    msg = "PAPERLESS_SSO_BASE_GROUP must not be empty"
    raise RuntimeError(msg)

view_models = {
    "correspondent",
    "customfield",
    "customfieldinstance",
    "document",
    "documenttype",
    "note",
    "savedview",
    "savedviewfilterrule",
    "storagepath",
    "tag",
    "uisettings",
}
permission_codenames = {
    f"view_{model}" for model in view_models
} | {
    f"{action}_uisettings"
    for action in ("add", "change", "delete")
}
permissions = Permission.objects.filter(
    content_type__app_label="documents",
    codename__in=permission_codenames,
)
actual_codenames = set(permissions.values_list("codename", flat=True))
missing = sorted(permission_codenames - actual_codenames)
if missing:
    raise RuntimeError(
        "Paperless SSO base permissions are incompatible: " + ", ".join(missing),
    )

group, _created = Group.objects.get_or_create(name=group_name)
required_permission_ids = set(permissions.values_list("id", flat=True))
current_permission_ids = set(group.permissions.values_list("id", flat=True))
if current_permission_ids != required_permission_ids:
    group.permissions.set(permissions)

# The documented default group covers future social-account signups. Reconcile
# existing Pocket users as well, which makes restart and restore idempotent.
social_accounts = list(
    SocialAccount.objects.filter(provider="pocket-id").select_related("user"),
)
for social_account in social_accounts:
    social_account.user.groups.add(group)

print(  # noqa: T201 - operational bootstrap confirmation
    f"Paperless SSO base group ready: {group_name} "
    f"({permissions.count()} capabilities, {len(social_accounts)} users)",
)
