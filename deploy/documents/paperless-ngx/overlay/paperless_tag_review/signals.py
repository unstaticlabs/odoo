from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from documents.models import Document

from .models import TagDecision, TagRule
from .runtime import automatic_tag_change


@receiver(m2m_changed, sender=Document.tags.through)
def preserve_tag_edits(sender, instance, action, reverse, pk_set, **kwargs):
    if automatic_tag_change.get() or action not in {"pre_add", "pre_remove", "pre_clear"}:
        return
    if reverse:
        tag_ids = {instance.pk}
        document_ids = pk_set if pk_set is not None else sender.objects.filter(tag=instance).values_list("document_id", flat=True)
    else:
        document_ids = [instance.pk]
        tag_ids = pk_set if pk_set is not None else instance.tags.values_list("pk", flat=True)
    guarded = set(TagRule.objects.filter(tag_id__in=tag_ids).values_list("tag_id", flat=True))
    guarded.update(TagDecision.objects.filter(document_id__in=document_ids, tag_id__in=tag_ids).values_list("tag_id", flat=True))
    if not guarded:
        return
    # Acquire the same document locks as classification before the M2M change.
    ids = list(Document.objects.select_for_update().filter(pk__in=document_ids).order_by("pk").values_list("pk", flat=True))
    if action == "pre_add":
        # Only record an actual manual addition. Ordinary tags remain native.
        for document_id in ids:
            for tag_id in guarded:
                previous, created = TagDecision.objects.get_or_create(
                    document_id=document_id, tag_id=tag_id, defaults={"status": "kept"},
                )
                if not created and previous.status != "kept":
                    previous.status = "kept"
                    previous.save(update_fields=["status"])
        return
    for document_id, tag_id in sender.objects.filter(document_id__in=ids, tag_id__in=guarded).values_list("document_id", "tag_id"):
        decision, created = TagDecision.objects.get_or_create(document_id=document_id, tag_id=tag_id)
        if not created and decision.status != "removed":
            decision.status = "removed"
            decision.save(update_fields=["status"])
