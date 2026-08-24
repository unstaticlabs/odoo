"""Provision the isolated QA identity used for Odoo Sign archival."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.authtoken.models import Token


username = "odoo-sign-integration"
User = get_user_model()
service, _created = User.objects.get_or_create(username=username)
service.first_name = "Odoo Sign Integration"
service.is_active = True
service.is_staff = False
service.is_superuser = False

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
service_codenames = {
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
    codename__in=service_codenames,
)
permissions |= Permission.objects.filter(
    content_type__app_label="auth",
    codename="view_user",
)
if permissions.count() != len(service_codenames) + 1:
    raise RuntimeError("Paperless Sign integration permissions are incompatible")

service.user_permissions.set(permissions)
service.set_unusable_password()
service.save()
token, _created = Token.objects.get_or_create(user=service)

# These markers are captured by the stack wrapper and never printed to its
# terminal. The token is passed only to an ephemeral Odoo configuration shell.
print(f"USL_SIGN_PAPERLESS_TOKEN={token.key}")
print(f"USL_SIGN_PAPERLESS_SERVICE_ID={service.id}")
