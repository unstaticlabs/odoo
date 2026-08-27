# ruff: noqa: T201 - parsed by the isolated restoration runner
"""Seal the temporary Paperless identity after one-shot restoration."""

from django.contrib.auth import get_user_model
from documents.models import Workflow
from guardian.models import UserObjectPermission
from rest_framework.authtoken.models import Token

User = get_user_model()
user = User.objects.filter(username="odoo-migration").first()
if user:
    runtime_user = User.objects.filter(
        username="odoo-integration",
        is_active=True,
    ).first()
    workflows = Workflow.objects.filter(name="USL Odoo fail-closed ingestion")
    if runtime_user:
        for workflow in workflows:
            workflow.actions.filter(assign_owner=user).update(
                assign_owner=runtime_user,
            )
            if not workflow.enabled:
                workflow.enabled = True
                workflow.save(update_fields=["enabled"])
    else:
        # No ordinary ingestion may remain assigned to the identity that is
        # about to be sealed. Runtime provisioning re-enables this workflow.
        workflows.update(enabled=False)
    Token.objects.filter(user=user).delete()
    UserObjectPermission.objects.filter(user=user).delete()
    user.user_permissions.clear()
    user.groups.clear()
    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.set_unusable_password()
    user.save()
print("DOCUMENTS_PAPERLESS_MIGRATION_IDENTITY=sealed")
