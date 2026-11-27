from django.db import models


class TagRule(models.Model):
    tag = models.OneToOneField("documents.Tag", on_delete=models.CASCADE)
    minimum_score = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(minimum_score__lte=100), name="usl_tag_score_at_most_100"),
        ]


class TagDecision(models.Model):
    """One decision per document and tag. A human removal suppresses future runs."""

    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE)
    tag = models.ForeignKey("documents.Tag", on_delete=models.CASCADE)
    batch = models.UUIDField(null=True, db_index=True)
    score = models.FloatField(null=True)
    minimum_score = models.PositiveSmallIntegerField(null=True)
    membership = models.BigIntegerField(null=True)
    added_parents = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default="removed")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("document", "tag"), name="usl_document_tag_decision"),
        ]
