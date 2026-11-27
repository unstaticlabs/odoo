from django.urls import path

from .views import BatchDocuments, DocumentHistory, TagPolicy

urlpatterns = [
    path("tags/<int:pk>/", TagPolicy.as_view()),
    path("documents/<int:pk>/", DocumentHistory.as_view()),
    path("batches/<uuid:batch>/", BatchDocuments.as_view()),
]
