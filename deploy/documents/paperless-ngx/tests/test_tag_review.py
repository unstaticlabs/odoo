from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.db import connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from documents.models import Document, Tag
from documents.signals.handlers import set_tags
from rest_framework.test import APITestCase

from paperless_tag_review.models import TagDecision, TagRule
from paperless_tag_review.service import undo_document_batch


@override_settings(
    AUDIT_LOG_ENABLED=False,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class TestReviewedClassifierTags(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="review-fixture")
        self.other = User.objects.create_user(username="other-fixture")
        for user in (self.user, self.other):
            user.user_permissions.add(*Permission.objects.filter(
                content_type__app_label="documents", codename__in=["view_tag", "change_tag", "view_document", "change_document"],
            ))
        self.document = Document.objects.create(title="Concert programme", content="Orchestra concert", mime_type="text/plain", checksum="synthetic-concert", owner=self.user)
        self.tag = Tag.objects.create(name="Music", owner=self.user, matching_algorithm=Tag.MATCH_AUTO)
        self.other_tag = Tag.objects.create(name="Reviewed by hand", owner=self.user)
        self.client.force_authenticate(user=self.user)
        self.policy = f"/api/tag_review/tags/{self.tag.pk}/"
        self.history = f"/api/tag_review/documents/{self.document.pk}/"

    def classify(self, score, **kwargs):
        with (
            patch("documents.matching.match_tags", return_value=[self.tag]),
            patch("paperless_tag_review.service.suggested_tag_scores", return_value={self.tag.pk: score} if score is not None else {}),
        ):
            return set_tags(None, self.document, classifier=object(), **kwargs)

    def configure(self, threshold=85):
        response = self.client.put(self.policy, {"minimum_score": threshold}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_unconfigured_tag_keeps_native_behavior(self):
        self.assertIsNone(self.client.get(self.policy).json()["minimum_score"])
        self.classify(None)
        self.assertTrue(self.document.tags.filter(pk=self.tag.pk).exists())
        self.assertFalse(TagDecision.objects.exists())

    def test_threshold_preview_equality_retry_and_manual_removal(self):
        self.configure()
        self.document.tags.add(self.other_tag)
        for score in [None, .2, .84999]:
            self.assertEqual(self.classify(score)[0], set())
        self.assertEqual(self.classify(.85, dry_run=True)[0], {self.tag})
        self.assertFalse(TagDecision.objects.exists())
        self.assertFalse(self.document.tags.filter(pk=self.tag.pk).exists())
        batch = uuid4()
        self.classify(.85, tag_review_batch=batch)
        self.classify(.99, replace=True)
        self.assertEqual(TagDecision.objects.count(), 1)
        event = self.client.get(self.history).json()["events"][0]
        self.assertEqual((event["batch"], event["score"], event["minimum_score"]), (str(batch), .85, 85))
        self.document.tags.remove(self.tag)
        self.classify(.99, replace=True)
        self.assertEqual(set(self.document.tags.all()), {self.other_tag})
        self.assertEqual(TagDecision.objects.get().status, "removed")
        self.document.tags.add(self.tag)
        undone = self.client.post(self.history, {"undo_batch": str(batch)}, format="json")
        self.assertEqual(undone.json()["removed"], 0)
        self.assertEqual(set(self.document.tags.all()), {self.tag, self.other_tag})

    def test_undo_only_this_batch_and_keep_native_parent_used_by_another_child(self):
        parent = Tag.objects.create(name="Folder", owner=self.user)
        self.tag.tn_parent = parent
        self.tag.save()
        sibling = Tag.objects.create(name="Separate series", owner=self.user, tn_parent=parent)
        self.configure()
        batch = uuid4()
        self.classify(.97, tag_review_batch=batch)
        self.document.add_nested_tags([sibling])
        response = self.client.post(self.history, {"undo_batch": str(batch)}, format="json")
        self.assertEqual(response.json(), {"removed": 1, "preserved": 0})
        self.assertEqual(set(self.document.tags.all()), {sibling, parent})
        self.assertEqual(self.client.post(self.history, {"undo_batch": str(batch)}, format="json").json()["removed"], 0)
        self.classify(.99, replace=True)
        self.assertFalse(self.document.tags.filter(pk=self.tag.pk).exists())

    def test_manual_preexisting_tag_keeps_ownership(self):
        self.configure()
        self.document.tags.add(self.tag)
        self.configure(None)
        self.classify(.95, replace=True)
        self.assertTrue(self.document.tags.filter(pk=self.tag.pk).exists())
        self.assertEqual(TagDecision.objects.get().status, "kept")
        self.document.tags.remove(self.tag)
        self.classify(.95)
        self.assertFalse(self.document.tags.filter(pk=self.tag.pk).exists())
        self.assertIsNone(TagDecision.objects.get().batch)

    def test_native_access_and_validation(self):
        for threshold in [True, 101, -1, .8, "90"]:
            self.assertEqual(self.client.put(self.policy, {"minimum_score": threshold}, format="json").status_code, 400)
        self.configure()
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(self.history).status_code, 403)
        self.assertEqual(self.client.post(self.history, {"undo_batch": str(uuid4())}, format="json").status_code, 403)
        self.assertEqual(self.client.get(self.policy).status_code, 403)
        self.assertEqual(self.client.put(self.policy, {"minimum_score": None}, format="json").status_code, 403)
        self.assertTrue(TagRule.objects.filter(tag=self.tag, minimum_score=85).exists())

    def test_batch_preview_and_replay_follow_each_document_and_tag(self):
        self.configure()
        batch = uuid4()
        self.classify(.93, tag_review_batch=batch)
        hidden_document = Document.objects.create(
            title="Unshared rehearsal", content="Concert", checksum="unshared-concert",
            mime_type="text/plain", owner=self.other,
        )
        hidden_tag = Tag.objects.create(name="Private music", owner=self.other, matching_algorithm=Tag.MATCH_AUTO)
        TagRule.objects.create(tag=hidden_tag, minimum_score=90)
        with (
            patch("documents.matching.match_tags", return_value=[self.tag, hidden_tag]),
            patch("paperless_tag_review.service.suggested_tag_scores", return_value={self.tag.pk: .93, hidden_tag.pk: .97}),
        ):
            for document in (self.document, hidden_document):
                set_tags(None, document, classifier=object(), tag_review_batch=batch)
        preview = self.client.get(f"/api/tag_review/batches/{batch}/")
        self.assertEqual(preview.json()["events"], [{"document_id": self.document.pk, "tag_id": self.tag.pk}])
        self.assertEqual(len(self.client.get(self.history).json()["events"]), 1)
        for allowed, expected in [([], 0), ([self.tag.pk], 1), ([self.tag.pk], 0)]:
            result = self.client.post(self.history, {"undo_batch": str(batch), "tag_ids": allowed}, format="json")
            self.assertEqual(result.json()["removed"], expected)
        self.assertTrue(self.document.tags.filter(pk=hidden_tag.pk).exists())
        self.assertTrue(hidden_document.tags.filter(pk=self.tag.pk).exists())
        self.assertEqual(self.client.get(f"/api/tag_review/batches/{batch}/").json()["events"], [])

    def test_accepted_ancestor_keeps_a_direct_receipt_and_undo_keeps_preexisting_tags(self):
        ancestor = Tag.objects.create(name="Arts", owner=self.user, matching_algorithm=Tag.MATCH_AUTO)
        self.tag.tn_parent = ancestor
        self.tag.save()
        self.configure(70)
        TagRule.objects.create(tag=ancestor, minimum_score=75)
        batch = uuid4()
        with (
            patch("documents.matching.match_tags", return_value=[self.tag, ancestor]),
            patch("paperless_tag_review.service.suggested_tag_scores", return_value={self.tag.pk: .80, ancestor.pk: .77}),
        ):
            set_tags(None, self.document, classifier=object(), tag_review_batch=batch)
        self.assertEqual(TagDecision.objects.filter(batch=batch).count(), 2)
        self.document.tags.add(self.other_tag)
        self.assertEqual(self.client.post(self.history, {"undo_batch": str(batch)}, format="json").json()["removed"], 2)
        self.assertEqual(set(self.document.tags.all()), {self.other_tag})

    def test_automatic_child_does_not_restore_a_manually_removed_parent(self):
        child = Tag.objects.create(name="Programme", owner=self.user, tn_parent=self.tag)
        self.configure()
        self.classify(.92)
        self.document.tags.remove(self.tag)
        with patch("documents.matching.match_tags", return_value=[child]):
            set_tags(None, self.document, replace=True)
        self.assertFalse(self.document.tags.exists())
        self.document.tags.add(self.tag)
        with patch("documents.matching.match_tags", return_value=[child]):
            set_tags(None, self.document, replace=True)
        self.assertEqual(set(self.document.tags.all()), {child, self.tag})


class TestConcurrentClassification(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_retagging_creates_one_receipt_and_retries_the_same_undo(self):
        document = Document.objects.create(title="Parallel recital", content="Orchestra", checksum="parallel-sample",
                                           mime_type="text/plain")
        tag = Tag.objects.create(name="Concert", matching_algorithm=Tag.MATCH_AUTO)
        TagRule.objects.create(tag=tag, minimum_score=85)
        batch = uuid4()

        def parallel(action):
            barrier = Barrier(2, timeout=15)

            def execute():
                connections.close_all()
                try:
                    item = Document.objects.get(pk=document.pk)
                    barrier.wait()
                    return action(item)
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(execute) for _ in range(2)]
                return [future.result(timeout=25) for future in futures]

        with (
            patch("documents.matching.match_tags", return_value=[tag]),
            patch("paperless_tag_review.service.suggested_tag_scores", return_value={tag.pk: .93}),
        ):
            parallel(lambda item: set_tags(None, item, replace=True, classifier=object(), tag_review_batch=batch))
        self.assertEqual(TagDecision.objects.filter(document=document, tag=tag, batch=batch).count(), 1)
        results = parallel(lambda item: undo_document_batch(item, batch, {tag.pk}))
        self.assertEqual(sorted(result["removed"] for result in results), [0, 1])
        self.assertFalse(document.tags.exists())
