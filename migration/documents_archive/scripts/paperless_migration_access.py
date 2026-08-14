# ruff: noqa: T201 - parsed by the isolated restoration runner
"""Provision the scoped Paperless identity used only by archive migration."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from documents.models import Document
from rest_framework.authtoken.models import Token

username = "odoo-migration"
User = get_user_model()
user, _created = User.objects.get_or_create(username=username)
user.is_active = True
# Paperless deliberately returns 404 for object-scoped documents and protects
# Trash/owner transitions beyond model permissions. This identity exists only
# inside the one-shot runner, has an unusable password, and is sealed by the
# paired cleanup script on success or failure.
user.is_staff = True
user.is_superuser = True
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
    message = "Paperless 3.0.4 migration permissions are incompatible"
    raise RuntimeError(message)
user.save()
user.user_permissions.set(permissions)
# Evaluate the authority boundary while provisioning so an incompatible model
# registry fails before a source archive is touched.
_document_count = Document.objects.count()
token, _created = Token.objects.get_or_create(user=user)
print(f"DOCUMENTS_PAPERLESS_TOKEN={token.key}")
print(f"DOCUMENTS_PAPERLESS_SERVICE_USER_ID={user.id}")
