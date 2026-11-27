from types import SimpleNamespace
from unittest import TestCase, mock

from sklearn.feature_extraction.text import CountVectorizer
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import LabelBinarizer, MultiLabelBinarizer

from evaluate_tag_scores import evaluate
from paperless_usl_tag_scores import suggested_tag_scores, unscored_cache_validator


def tag(pk, auto=True):
    return SimpleNamespace(pk=pk, matching_algorithm=6 if auto else 3, MATCH_AUTO=6)


def train(single=False):
    corpus = [
        ("facade window masonry", [17]), ("masonry roof window", [17]),
        ("bassoon strings orchestra", [41]), ("strings concert bassoon", [41]),
        ("window orchestra concert", [17, 41]), ("blank unrelated sheet", []),
    ] * 8
    vectorizer = CountVectorizer(ngram_range=(1, 2), min_df=0.01)
    text, tags = zip(*corpus, strict=True)
    matrix = vectorizer.fit_transform(text)
    if single:
        encoder = LabelBinarizer()
        targets = encoder.fit_transform([17 if 17 in labels else -1 for labels in tags]).ravel()
    else:
        encoder = MultiLabelBinarizer()
        targets = encoder.fit_transform(tags)
    classifier = MLPClassifier(tol=0.01, random_state=0).fit(matrix, targets)
    return SimpleNamespace(
        tags_classifier=classifier, tags_binarizer=encoder, data_vectorizer=vectorizer,
        _vectorize=lambda content: vectorizer.transform([content]),
    )


class TestTagScores(TestCase):
    def test_per_tag_review_points_to_false_positives_and_counts_missed_labels(self):
        labelled = [
            {"expected_tag_ids": [17, 41], "tag_scores": {"17": .88, "41": .1}},
            {"expected_tag_ids": [41], "tag_scores": {"17": .90, "41": .95}},
            {"expected_tag_ids": [17], "tag_scores": {}},
            {"expected_tag_ids": [], "tag_scores": {"41": .99}},
        ]
        self.assertEqual(evaluate(labelled, .85, tag_id=17), {
            "tag_id": 17, "documents": 4, "threshold": .85, "correct": 1, "incorrect": 1,
            "missed": 1, "precision": .5, "recall": .5, "false_positive_rows": [2],
        })
        other = evaluate(labelled, .85, tag_id=41)
        self.assertEqual(other["false_positive_rows"], [4])
        self.assertIsNone(evaluate(labelled, 1, tag_id=17)["precision"])

    def test_independent_native_columns_and_visible_automatic_proposals(self):
        classifier = train()
        text = "window masonry orchestra"
        native = classifier.tags_classifier.predict_proba(classifier._vectorize(text))[0]
        self.assertEqual(suggested_tag_scores(classifier, text, [tag(17), tag(41)]), {
            17: float(native[0]), 41: float(native[1]),
        })
        self.assertEqual(suggested_tag_scores(classifier, text, [tag(41), tag(17, False)]), {
            41: float(native[1]),
        })
        self.assertEqual(suggested_tag_scores(classifier, text, []), {})

    def test_binary_uses_positive_tag_column_and_unusable_models_abstain(self):
        classifier = train(single=True)
        text = "window roof"
        native = classifier.tags_classifier.predict_proba(classifier._vectorize(text))[0]
        self.assertEqual(suggested_tag_scores(classifier, text, [tag(17)]), {17: float(native[1])})
        self.assertEqual(suggested_tag_scores(None, text, [tag(17)]), {})
        classifier.tags_binarizer = SimpleNamespace(classes_=[17])
        self.assertEqual(suggested_tag_scores(classifier, text, [tag(17)]), {})

    def test_scored_requests_do_not_reuse_conditional_cached_data(self):
        validate = mock.Mock(return_value="legacy-etag")
        guard = unscored_cache_validator(validate)
        request = SimpleNamespace(GET={"include_tag_scores": "1"})
        self.assertIsNone(guard(request, 5))
        validate.assert_not_called()
        request.GET.clear()
        self.assertEqual(guard(request, 5), "legacy-etag")

    def test_evaluator_counts_wrong_proposals_and_unseen_expected_tags(self):
        rows = [
            {"tag_scores": {"17": .85, "41": .81}, "expected_tag_ids": [17]},
            {"tag_scores": {}, "expected_tag_ids": [41]},
            {"tag_scores": {"41": .79}, "expected_tag_ids": []},
        ]
        result = evaluate(rows, .8)
        self.assertEqual((result["correct"], result["incorrect"], result["missed"]), (1, 1, 1))
        self.assertEqual((result["precision"], result["recall"]), (.5, .5))
        self.assertIsNone(evaluate(rows, 1.)["precision"])
        with self.assertRaises(ValueError):
            evaluate([{"tag_scores": {"17": True}, "expected_tag_ids": [17]}], .8)
