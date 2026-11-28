from django.apps import AppConfig


class PaperlessTagReviewConfig(AppConfig):
    name = "paperless_tag_review"
    verbose_name = "Reviewed classifier tags"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401
