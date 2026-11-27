from uuid import uuid4

from django.db import transaction
from documents.models import Document, Tag
from paperless_usl_tag_scores import suggested_tag_scores

from .models import TagDecision, TagRule
from .runtime import classifier_run


def reviewed_tag_scope(document):
    """Serialize opted-in classification with manual edits and preserve their decisions."""
    guarded = set(TagRule.objects.filter(tag__matching_algorithm=Tag.MATCH_AUTO).values_list("tag_id", flat=True))
    history = TagDecision.objects.filter(document=document)
    if not guarded and not history.exists():
        return guarded, set()
    Document.objects.select_for_update().get(pk=document.pk)
    rejected = set()
    for tag_id, status, parents in history.values_list("tag_id", "status", "added_parents"):
        guarded.add(tag_id)
        if status == "applied":
            guarded.update(int(pk) for pk in parents)
        elif status in {"undone", "removed"}:
            rejected.add(tag_id)
    # The person may have intentionally added the tag again.
    if rejected:
        rejected.difference_update(document.tags.filter(pk__in=rejected).values_list("pk", flat=True))
    return guarded, rejected


def respects_manual_removals(tag, rejected):
    return not rejected or (tag.pk not in rejected and rejected.isdisjoint(tag.get_ancestors_pks()))


def _memberships(document, tag_ids):
    return dict(Document.tags.through.objects.filter(
        document=document, tag_id__in=tag_ids,
    ).values_list("tag_id", "pk"))


@transaction.atomic
def apply_reviewed_tags(document, classifier, matched, protected, *, dry_run=False, batch=None):
    """Apply only eligible predictions. Existing tags always keep their ownership."""
    if not protected:
        return set()
    candidates = [tag for tag in matched if tag.pk in protected]
    scores = suggested_tag_scores(classifier, document.suggestion_content, candidates)
    if not scores:
        return set()
    rules = dict(TagRule.objects.filter(tag_id__in=scores).values_list("tag_id", "minimum_score"))
    known = set(TagDecision.objects.filter(document=document, tag_id__in=scores).values_list("tag_id", flat=True))
    already_tagged = set(document.tags.filter(pk__in=scores).values_list("pk", flat=True))
    unavailable = known | already_tagged
    accepted = {
        tag for tag in candidates
        if tag.pk in rules and tag.pk not in unavailable and scores[tag.pk] >= rules[tag.pk] / 100
    }
    if dry_run or not accepted:
        return accepted
    batch = batch or uuid4()
    # Apply accepted ancestors first so each direct match keeps its own receipt.
    with_ancestors = [(tag, set(tag.get_ancestors_pks())) for tag in accepted]
    for tag, ancestors in sorted(with_ancestors, key=lambda item: (len(item[1]), item[0].pk)):
        before = _memberships(document, ancestors | {tag.pk})
        document.add_nested_tags([tag])
        after = _memberships(document, ancestors | {tag.pk})
        if tag.pk in before:
            continue
        TagDecision.objects.create(
            tag=tag, document=document, batch=batch, score=scores[tag.pk],
            minimum_score=rules[tag.pk], membership=after[tag.pk], status="applied",
            added_parents={str(pk): after[pk] for pk in ancestors - before.keys()},
        )
    return accepted


@transaction.atomic
@classifier_run()
def undo_document_batch(document, batch, visible_tag_ids):
    """Remove only links created by this batch and still owned by it."""
    document = Document.objects.select_for_update().get(pk=document.pk)
    events = list(TagDecision.objects.select_for_update().filter(
        document=document, batch=batch, status="applied", tag_id__in=visible_tag_ids,
    ))
    memberships = _memberships(document, [event.tag_id for event in events])
    owned = {e.tag_id for e in events if memberships.get(e.tag_id) == e.membership}
    # A parent this run created stays owned by the run until it is removed, whatever happened to the receipt naming it.
    receipts = TagDecision.objects.filter(document=document, batch=batch).values_list("added_parents", flat=True)
    parents = {int(pk): membership for added in receipts for pk, membership in added.items()}
    current_parents = _memberships(document, parents)
    inherited = {pk for pk, member in parents.items() if current_parents.get(pk) == member and pk in visible_tag_ids}
    remaining = document.tags.exclude(pk__in=owned | inherited)
    needed = {pk for tag in remaining for pk in tag.get_ancestors_pks()}
    to_remove = (owned | inherited) - needed
    if to_remove:
        document.tags.remove(*to_remove)
    for event in events:
        event.status = "undone" if event.tag_id in to_remove else "kept"
        event.save(update_fields=["status"])
    return {"removed": len(owned & to_remove), "preserved": len(events) - len(owned & to_remove)}
