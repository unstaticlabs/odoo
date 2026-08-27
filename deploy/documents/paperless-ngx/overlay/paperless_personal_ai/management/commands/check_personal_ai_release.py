import os

from django.core.management.base import BaseCommand, CommandError
from paperless.models import ApplicationConfiguration

from paperless_personal_ai.crypto import PersonalAIKeyServiceError, load_master_key_ring

NATIVE_GENERATIVE_ENVIRONMENT = (
    "PAPERLESS_AI_LLM_BACKEND",
    "PAPERLESS_AI_LLM_MODEL",
    "PAPERLESS_AI_LLM_API_KEY",
    "PAPERLESS_AI_LLM_ENDPOINT",
    "PAPERLESS_AI_LLM_OUTPUT_LANGUAGE",
)
NATIVE_GENERATIVE_FIELDS = (
    "llm_backend",
    "llm_model",
    "llm_api_key",
    "llm_endpoint",
    "llm_output_language",
)
FORBIDDEN_INLINE_KEY_ENVIRONMENT = (
    "PAPERLESS_USL_PERSONAL_AI_MASTER_KEYS",
    "PAPERLESS_USL_PERSONAL_AI_MASTER_KEYS_FILE",
    "USL_PERSONAL_AI_MASTER_KEYS",
)


class Command(BaseCommand):
    help = "Validate the non-secret USL personal AI release boundary."

    def handle(self, *args, **options):
        inline_key_environment = [
            name for name in FORBIDDEN_INLINE_KEY_ENVIRONMENT if os.getenv(name)
        ]
        if inline_key_environment:
            raise CommandError(
                "Personal AI master keys must be mounted by path, never placed "
                "in process environment variables: "
                + ", ".join(inline_key_environment),
            )
        configured_environment = [
            name for name in NATIVE_GENERATIVE_ENVIRONMENT if os.getenv(name)
        ]
        if configured_environment:
            raise CommandError(
                "Native global generative AI environment settings must be empty: "
                + ", ".join(configured_environment),
            )
        configured_database = []
        for name in NATIVE_GENERATIVE_FIELDS:
            if ApplicationConfiguration.objects.exclude(
                **{f"{name}__isnull": True},
            ).exclude(**{name: ""}).exists():
                configured_database.append(name)
        if configured_database:
            raise CommandError(
                "Native global generative AI database settings must be empty: "
                + ", ".join(configured_database),
            )
        try:
            key_ring = load_master_key_ring()
        except PersonalAIKeyServiceError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "USL_PERSONAL_AI_RELEASE_READY="
            f"{key_ring.active.key_id}:{key_ring.active.version}",
        )
