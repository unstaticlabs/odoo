from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import SimpleTestCase, override_settings
from documents.classifier import DocumentClassifier
from documents.models import Document, Tag
from rest_framework.test import APITestCase

from evaluate_tag_scores import evaluate
from paperless_usl_tag_scores import suggested_tag_scores
from test_tag_scores import tag, train


@override_settings(NUMBER_OF_SUGGESTED_DATES=0)
class TestSuggestionTagScoresApi(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="scores-reader")
        self.other = User.objects.create_user(username="other-reader")
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=["view_document", "view_tag"]))
        self.client.force_authenticate(user=self.user)
        self.document = Document.objects.create(
            title="Synthetic score fixture", content="masonry window roof orchestra concert",
            checksum="synthetic-score-fixture", mime_type="text/plain", owner=self.user,
        )
        self.auto = Tag.objects.create(id=17, name="Masonry", owner=self.user, matching_algorithm=Tag.MATCH_AUTO)
        self.hidden = Tag.objects.create(id=41, name="Music", owner=self.other, matching_algorithm=Tag.MATCH_AUTO)
        self.rule = Tag.objects.create(
            name="Literal match", owner=self.user, matching_algorithm=Tag.MATCH_ANY, match="window",
        )
        fitted = train()
        self.classifier = DocumentClassifier()
        self.classifier.tags_classifier = fitted.tags_classifier
        self.classifier.tags_binarizer = fitted.tags_binarizer
        self.classifier.data_vectorizer = fitted.data_vectorizer
        self.path = f"/api/documents/{self.document.pk}/suggestions/"

    def test_opt_in_scores_only_visible_model_matches(self):
        with (
            TemporaryDirectory() as directory,
            override_settings(MODEL_FILE=Path(directory) / "classifier", CLASSIFIER_LANGUAGE=None),
            patch("documents.views.get_suggestion_cache") as read_cache,
            patch("documents.views.set_suggestions_cache") as write_cache,
        ):
            self.classifier.save()
            response = self.client.get(self.path, {"include_tag_scores": "1"}, HTTP_IF_NONE_MATCH='"old-score"')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn(self.auto.pk, body["tags"])
        self.assertIn(self.rule.pk, body["tags"])
        self.assertNotIn(self.hidden.pk, body["tags"])
        self.assertEqual(set(body["tag_scores"]), {str(self.auto.pk)})
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertFalse(self.document.tags.exists())
        read_cache.assert_not_called()
        write_cache.assert_not_called()

    def test_existing_suggestion_contract_still_uses_legacy_cache(self):
        cached = {"tags": [self.rule.pk]}
        with (
            patch("documents.views.get_suggestion_cache", return_value=SimpleNamespace(suggestions=cached)),
            patch("documents.views.refresh_suggestions_cache"),
            patch("documents.views.load_classifier") as load,
        ):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), cached)
        load.assert_not_called()

    def test_other_person_cannot_request_document_scores(self):
        self.document.owner = self.other
        self.document.save(update_fields=["owner"])
        with patch("documents.views.load_classifier") as load:
            response = self.client.get(self.path, {"include_tag_scores": "1"})
        self.assertEqual(response.status_code, 403)
        load.assert_not_called()


class TestHeldOutTagScores(SimpleTestCase):
    def test_precision_and_missed_labels_on_a_separate_synthetic_corpus(self):
        examples = [
            ("replace loose facade bricks", [17]),
            ("repair the leaking roof", [17]),
            ("window masonry installation", [17]),
            ("concert of strings and brass", [41]),
            ("orchestra rehearsal with singers", [41]),
            ("bassoon solo on stage", [41]),
            ("orchestra concert in a window factory", [41]),
            ("roof work above the orchestra rehearsal", [17, 41]),
            ("masonry and string orchestra schedule", [17, 41]),
            ("plain unmarked sheet of paper", []),
            ("cancelled concert postcard for sale", []),
            ("a note stuck to the window", []),
            ("refit timber entrance frame", [17]),
            ("chamber recital auditions", [41]),
            ("disused sheet music for recycling", []),
            ("electricity meter receipt", []),
        ]

        fitted = train()
        model = DocumentClassifier()
        model.tags_binarizer = fitted.tags_binarizer
        model.tags_classifier = fitted.tags_classifier
        model._vectorize = fitted._vectorize
        rows = []
        for content, expected in examples:
            native = model.predict_tags(content)
            scores = suggested_tag_scores(model, content, [tag(int(label)) for label in native])
            rows.append({"expected_tag_ids": expected, "tag_scores": {str(k): v for k, v in scores.items()}})
        observed = []
        for threshold in (.5, .7, .85, .95):
            quality = evaluate(rows, threshold)
            observed.append((quality["correct"], quality["incorrect"], quality["missed"]))
        self.assertEqual(observed, [(11, 8, 2), (3, 2, 10), (1, 0, 12), (0, 0, 13)])
