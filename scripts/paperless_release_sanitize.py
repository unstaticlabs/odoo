"""Sanitize a cloned Paperless database before cohort capture.

The caller must point Paperless at a disposable ``paperless_release_*`` clone.
This script uses only the supported Django ORM and never touches the live
Paperless database manually.
"""

# ruff: noqa: EM101, F821, T201 - manage.py shell supplies the Django runtime.

import json
import os

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction

confirmation = os.environ.get("USL_RELEASE_SANITIZE_CONFIRMED")
database_name = str(connection.settings_dict.get("NAME") or "")
service_username = os.environ.get(
    "USL_RELEASE_SERVICE_USERNAME",
    "odoo-integration",
).strip()
if confirmation != "paperless-release-clone":
    raise RuntimeError("Paperless release sanitation requires explicit confirmation")
if not database_name.startswith("paperless_release_"):
    raise RuntimeError("Paperless release sanitation requires a disposable clone")
if not service_username:
    raise RuntimeError("Paperless release service username is required")


def model(label):
    return apps.get_model(label)


with transaction.atomic():
    User = get_user_model()
    service = User.objects.filter(username=service_username).get()
    deleted = {}
    for label in (
        "authtoken.Token",
        "sessions.Session",
        "account.EmailAddress",
        "socialaccount.SocialToken",
        "socialaccount.SocialAccount",
        "socialaccount.SocialApp",
        "paperless_personal_ai.PersonalAIProfile",
        "paperless_mail.MailRule",
        "paperless_mail.MailAccount",
        "documents.WorkflowActionEmail",
        "documents.WorkflowActionWebhook",
    ):
        queryset = model(label).objects.all()
        deleted[label] = queryset.count()
        queryset.delete()

    # Object grants belong to the QA identity mapping and are rebuilt from Odoo
    # only after approved production identities exist.
    object_permissions = model("guardian.UserObjectPermission").objects.all()
    deleted["guardian.UserObjectPermission"] = object_permissions.count()
    object_permissions.delete()

    configuration = model("paperless.ApplicationConfiguration").objects.first()
    cleared_global_fields = []
    if configuration:
        for name in ("llm_api_key", "llm_model", "llm_endpoint", "llm_backend"):
            if hasattr(configuration, name) and getattr(configuration, name):
                setattr(configuration, name, "")
                cleared_global_fields.append(name)
        if cleared_global_fields:
            configuration.save(update_fields=cleared_global_fields)

    sanitized_users = 0
    for user in User.objects.exclude(pk=service.pk).order_by("pk"):
        user.username = f"release-disabled-{user.pk}"
        user.email = ""
        user.first_name = ""
        user.last_name = ""
        user.is_active = False
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()
        user.groups.clear()
        user.user_permissions.clear()
        sanitized_users += 1

    service.email = ""
    service.first_name = ""
    service.last_name = ""
    service.is_active = True
    service.is_staff = False
    service.is_superuser = False
    service.set_unusable_password()
    service.save()
    service.groups.clear()
    service.user_permissions.clear()

result = {
    "schema": "usl-paperless-release-sanitation-v1",
    "database": database_name,
    "service_user_id": service.pk,
    "sanitized_human_users": sanitized_users,
    "deleted": deleted,
    "cleared_global_fields": sorted(cleared_global_fields),
    "remaining_personal_profiles": model(
        "paperless_personal_ai.PersonalAIProfile",
    ).objects.count(),
    "remaining_tokens": model("authtoken.Token").objects.count(),
    "remaining_social_accounts": model("socialaccount.SocialAccount").objects.count(),
    "status": "passed",
}
print("USL_PAPERLESS_RELEASE_SANITATION=" + json.dumps(result, sort_keys=True))
