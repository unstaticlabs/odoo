from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from documents.models import Document, Workflow, WorkflowAction
from documents.serialisers import WorkflowActionSerializer
from documents.workflows.ai import apply_ai_suggestions_to_document
from rest_framework import serializers


class TestWorkflowAISuggestionsDisabled(TestCase):
    """A workflow has no initiating person, so it never reaches Gemini."""

    def setUp(self) -> None:
        self.owner = User.objects.create_user(username="document-owner")
        self.document = Document.objects.create(
            title="original title",
            content="OCR text that a workflow must not send to a provider",
            checksum="workflow-ai-disabled",
            mime_type="application/pdf",
            owner=self.owner,
        )

    def test_api_refuses_the_action_type(self):
        with self.assertRaises(serializers.ValidationError) as raised:
            WorkflowActionSerializer().validate(
                {
                    "type": WorkflowAction.WorkflowActionType.APPLY_AI_SUGGESTIONS,
                    "ai_suggestion_fields": [
                        WorkflowAction.AISuggestionField.TITLE,
                    ],
                },
            )

        self.assertIn("type", raised.exception.detail)

    def test_api_keeps_other_action_types(self):
        attrs = WorkflowActionSerializer().validate(
            {"type": WorkflowAction.WorkflowActionType.ASSIGNMENT},
        )

        self.assertEqual(
            attrs["type"],
            WorkflowAction.WorkflowActionType.ASSIGNMENT,
        )

    @patch("documents.workflows.ai.AIConfig")
    @patch("documents.workflows.ai.get_ai_document_classification")
    def test_stored_action_changes_nothing_and_never_generates(
        self,
        classify,
        ai_config,
    ):
        ai_config.return_value.ai_enabled = True
        workflow = Workflow.objects.create(name="legacy AI workflow", order=0)
        action = WorkflowAction.objects.create(
            type=WorkflowAction.WorkflowActionType.APPLY_AI_SUGGESTIONS,
            ai_suggestion_fields=[WorkflowAction.AISuggestionField.TITLE],
            ai_overwrite_existing=True,
        )
        workflow.actions.add(action)

        updated = apply_ai_suggestions_to_document(action, self.document)

        self.assertEqual(updated, [])
        classify.assert_not_called()
        ai_config.assert_not_called()
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "original title")
