"""Provision the non-human Paperless identity used by Odoo at runtime.

Archive migration owns roots as ``odoo-migration``. Runtime Odoo must own them
as ``odoo-integration`` before object-permission synchronization can succeed,
so this script also claims any remaining migration-owned documents.
"""

# Executed by ``manage.py shell`` where imports and stdout are the operator
# contract rather than an importable application module.
# ruff: noqa: EM101, I001, T201

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from documents.models import Document, PaperlessTask
from rest_framework.authtoken.models import Token


username = "odoo-integration"
migration_username = "odoo-migration"
legacy_sign_username = "odoo-sign-integration"
User = get_user_model()
user, _created = User.objects.get_or_create(username=username)
user.first_name = "Odoo Integration"
user.is_active = True
user.is_staff = False
user.is_superuser = False
user.set_unusable_password()

mutable_models = {
    "correspondent",
    "customfield",
    "customfieldinstance",
    "document",
    "documenttype",
    "savedview",
    "savedviewfilterrule",
    "tag",
}
workflow_models = {"workflow", "workflowaction", "workflowtrigger"}
read_models = {"paperlesstask", "storagepath"}
codenames = {
    f"{action}_{model}"
    for model in mutable_models
    for action in ("add", "change", "delete", "view")
} | {
    f"{action}_{model}"
    for model in workflow_models
    for action in ("add", "change", "view")
} | {f"view_{model}" for model in read_models}
permissions = Permission.objects.filter(
    content_type__app_label="documents",
    codename__in=codenames,
)
permissions |= Permission.objects.filter(
    content_type__app_label="auth",
    codename="view_user",
)
if permissions.count() != len(codenames) + 1:
    raise RuntimeError("Paperless integration permissions are incompatible")
user.save()
user.user_permissions.set(permissions)

claimed = 0
migration_user = User.objects.filter(username=migration_username).first()
if migration_user is not None and migration_user.id != user.id:
    # Paperless's default Document manager excludes Trash. Its Trash endpoint
    # is owner-filtered too, so leaving deleted migration roots behind would
    # make them invisible to the runtime identity and break recovery parity.
    claimed = Document.global_objects.filter(owner=migration_user).update(owner=user)

# A former Sign-only QA initializer created a second API identity even though
# Sign and Documents share one governed archive client in Odoo. Consolidate any
# objects it owned, preserve its completed task records under the canonical
# runtime owner, and revoke the redundant credential.
legacy_claimed = 0
legacy_tasks_claimed = 0
legacy_sign_user = User.objects.filter(username=legacy_sign_username).first()
if legacy_sign_user is not None and legacy_sign_user.id != user.id:
    legacy_claimed = Document.objects.filter(owner=legacy_sign_user).update(
        owner=user,
    )
    legacy_tasks_claimed = PaperlessTask.objects.filter(
        owner=legacy_sign_user,
    ).update(owner=user)
    Token.objects.filter(user=legacy_sign_user).delete()
    legacy_sign_user.user_permissions.clear()
    legacy_sign_user.groups.clear()
    legacy_sign_user.is_active = False
    legacy_sign_user.save(update_fields=["is_active"])

token, _created = Token.objects.get_or_create(user=user)
print(f"USL_PAPERLESS_TOKEN={token.key}")
print(f"USL_PAPERLESS_SERVICE_USER_ID={user.id}")
print(f"USL_PAPERLESS_OWNERS_CLAIMED={claimed + legacy_claimed}")
print(f"USL_PAPERLESS_TASKS_CLAIMED={legacy_tasks_claimed}")
