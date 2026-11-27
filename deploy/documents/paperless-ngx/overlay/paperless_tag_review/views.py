from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from documents.models import Document, Tag
from documents.permissions import has_perms_owner_aware, permitted_object_ids
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import TagDecision, TagRule
from .service import undo_document_batch


def permitted_object(user, model, pk, verb):
    permission = f"{verb}_{model._meta.model_name}"
    if not user.has_perm(f"documents.{permission}"):
        raise PermissionDenied()
    obj = get_object_or_404(model, pk=pk)
    if not has_perms_owner_aware(user, permission, obj):
        raise PermissionDenied()
    return obj


class TagPolicy(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        tag = permitted_object(request.user, Tag, pk, "view")
        threshold = TagRule.objects.filter(tag=tag).values_list("minimum_score", flat=True).first()
        return Response({"minimum_score": threshold})

    @transaction.atomic
    def put(self, request, pk):
        tag = permitted_object(request.user, Tag, pk, "change")
        if not isinstance(request.data, dict) or set(request.data) != {"minimum_score"}:
            raise ValidationError("Provide a whole-number score from 0 to 100, or null to disable the rule.")
        score = request.data["minimum_score"]
        if score is None:
            TagRule.objects.filter(tag=tag).delete()
        else:
            if type(score) is not int or not 0 <= score <= 100 or tag.matching_algorithm != Tag.MATCH_AUTO:
                raise ValidationError("Choose a learned tag and a whole-number score from 0 to 100.")
            TagRule.objects.update_or_create(tag=tag, defaults={"minimum_score": score})
        return Response({"minimum_score": score})


class DocumentHistory(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def visible_tags(user):
        if not user.has_perm("documents.view_tag"):
            return Tag.objects.none().values_list("pk", flat=True)
        return permitted_object_ids(user, Tag, "view_tag")

    def get(self, request, pk):
        document = permitted_object(request.user, Document, pk, "view")
        events = TagDecision.objects.filter(
            document=document, tag_id__in=self.visible_tags(request.user), batch__isnull=False,
        ).order_by("-created", "pk")
        return Response({"events": list(events.values("tag_id", "batch", "score", "minimum_score", "status", "created"))})

    def post(self, request, pk):
        document = permitted_object(request.user, Document, pk, "change")
        try:
            batch = UUID(request.data["undo_batch"])
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValidationError("Choose a batch from the document history.") from error
        allowed = self.visible_tags(request.user)
        if "tag_ids" in request.data:
            ids = request.data["tag_ids"]
            if not isinstance(ids, list) or any(type(item) is not int or item <= 0 for item in ids):
                raise ValidationError("Choose accessible tags.")
            allowed = allowed.filter(pk__in=ids)
        return Response(undo_document_batch(document, batch, set(allowed)))


class BatchDocuments(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, batch):
        if not request.user.has_perm("documents.change_document"):
            raise PermissionDenied()
        documents = permitted_object_ids(request.user, Document, "change_document", parent_field="root_document")
        events = TagDecision.objects.filter(
            batch=batch, status="applied", document_id__in=documents,
            tag_id__in=DocumentHistory.visible_tags(request.user),
        )
        return Response({"events": list(events.order_by("document_id", "tag_id").values("document_id", "tag_id"))})
