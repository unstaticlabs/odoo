"""Expose native tag scores only for model proposals the caller can already see."""

from math import isfinite


def wants_tag_scores(request):
    return request.GET.get("include_tag_scores") == "1"


def unscored_cache_validator(validator):
    # The legacy ETag and modification date do not cover a fresh model read.
    def validate(request, *args, **kwargs):
        if not wants_tag_scores(request):
            return validator(request, *args, **kwargs)
        return None
    return validate


def scored_cache_entry(entry):
    # Entries cached before scores were stored carry none. Recompute them once
    # instead of showing a proposal whose score is unknown.
    suggestions = getattr(entry, "suggestions", None)
    if isinstance(suggestions, dict) and "tag_scores" in suggestions:
        return entry
    return None


def suggested_tag_scores(classifier, content, matched_tags):
    auto_ids = {tag.pk for tag in matched_tags if tag.matching_algorithm == tag.MATCH_AUTO}
    if not auto_ids or classifier is None or classifier.tags_classifier is None:
        return {}
    classes = classifier.tags_binarizer.classes_
    if len(classes) < 2:
        # A single training outcome gives no evidence for the alternative.
        return {}
    probabilities = classifier.tags_classifier.predict_proba(classifier._vectorize(content))[0]
    if len(probabilities) != len(classes):
        return {}
    # Multiple tags have independent output columns. For a single auto tag
    # the native binary encoder has columns [-1, tag_id], where -1 means none.
    return {
        int(label): float(probability)
        for label, probability in zip(classes, probabilities, strict=True)
        if label in auto_ids and isfinite(probability) and 0 <= probability <= 1
    }
