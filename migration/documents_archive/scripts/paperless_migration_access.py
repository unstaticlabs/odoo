"""Provision the scoped Paperless identity used only by archive migration."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.authtoken.models import Token


username = "odoo-migration"
User = get_user_model()
user, _created = User.objects.get_or_create(username=username)
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
    raise RuntimeError("Paperless 3.0.5 migration permissions are incompatible")
user.save()
user.user_permissions.set(permissions)
token, _created = Token.objects.get_or_create(user=user)
print(f"DOCUMENTS_PAPERLESS_TOKEN={token.key}")
print(f"DOCUMENTS_PAPERLESS_SERVICE_USER_ID={user.id}")
