from django.urls import path

from paperless_personal_ai.views import (
    PersonalAIConnectionTestView,
    PersonalAIDisableView,
    PersonalAIProfileView,
)

urlpatterns = [
    path("", PersonalAIProfileView.as_view(), name="usl_personal_ai_profile"),
    path(
        "test/",
        PersonalAIConnectionTestView.as_view(),
        name="usl_personal_ai_test",
    ),
    path(
        "disable/",
        PersonalAIDisableView.as_view(),
        name="usl_personal_ai_disable",
    ),
]
