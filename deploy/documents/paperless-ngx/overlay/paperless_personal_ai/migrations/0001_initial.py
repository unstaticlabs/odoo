import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def clear_native_generative_configuration(apps, schema_editor):
    application_configuration = apps.get_model("paperless", "ApplicationConfiguration")
    application_configuration.objects.update(
        llm_backend=None,
        llm_model=None,
        llm_api_key=None,
        llm_endpoint=None,
        llm_output_language=None,
    )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("paperless", "0013_applicationconfiguration_llm_request_timeout"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonalAIProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("metadata_suggestions_enabled", models.BooleanField(default=False)),
                ("document_chat_enabled", models.BooleanField(default=False)),
                (
                    "provider",
                    models.CharField(default="gemini", editable=False, max_length=32),
                ),
                (
                    "model_name",
                    models.CharField(default="gemini-3.7-flash", max_length=64),
                ),
                (
                    "api_key_ciphertext",
                    models.TextField(blank=True, default="", editable=False),
                ),
                (
                    "api_key_nonce",
                    models.CharField(
                        blank=True,
                        default="",
                        editable=False,
                        max_length=32,
                    ),
                ),
                (
                    "wrapped_dek",
                    models.TextField(blank=True, default="", editable=False),
                ),
                (
                    "wrapped_dek_nonce",
                    models.CharField(
                        blank=True,
                        default="",
                        editable=False,
                        max_length=32,
                    ),
                ),
                (
                    "master_key_id",
                    models.CharField(
                        blank=True,
                        default="",
                        editable=False,
                        max_length=64,
                    ),
                ),
                (
                    "master_key_version",
                    models.PositiveIntegerField(default=0, editable=False),
                ),
                (
                    "credential_revision",
                    models.PositiveIntegerField(default=0, editable=False),
                ),
                (
                    "last_tested_at",
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="usl_personal_ai_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "personal AI profile",
                "verbose_name_plural": "personal AI profiles",
            },
        ),
        migrations.RunPython(
            clear_native_generative_configuration,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
