from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_documents.models.paperless_client import PaperlessClient

JSON_MARKERS = (None, False, True, 0, 1, 2.5, "", "True", {}, {"authorized": True}, [])


@tagged("post_install", "-at_install", "usl_document_checked_callers")
class TestDocumentCheckedCallers(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create(
            {"name": "[QA] Document Other Company"},
        )
        cls.manager_group = cls.env.ref("usl_documents.group_documents_manager")
        cls.irreversible = cls.env.ref("usl_access_control.group_irreversible_actions")

        def user(login, groups):
            return cls.env["res.users"].with_context(no_reset_password=True).create({
                "name": "[QA] " + login,
                "login": login,
                "company_id": cls.company.id,
                "company_ids": [Command.set(cls.company.ids)],
                "group_ids": [Command.set(groups.ids)],
            })

        cls.manager = user("qa.document.manager", cls.manager_group)
        cls.authorized = user("qa.document.authorized", cls.manager_group | cls.irreversible)
        cls.reader = user("qa.document.reader", cls.env.ref("usl_documents.group_documents_user"))
        cls.agent = cls.env["usl.agent"].with_user(cls.manager).create({
            "name": "[QA] Document Agent",
            "purpose": "Disposable native document controls.",
            "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "delegated_group_ids": [Command.set(cls.manager_group.ids)],
            "read_only_group_ids": [Command.clear()],
            "access_mode": "read_write",
        })
        cls.sequence = 980000

    def _document(self, **values):
        type(self).sequence += 1
        return self.env["usl.document"].create({
            "name": "[QA] Invented expired disposable file",
            "paperless_id": self.sequence,
            "company_id": self.company.id,
            "confidentiality": "internal",
            "review_state": "classified",
            "permission_sync_state": "synchronized",
            "availability_state": "trashed",
            "retention_until": values.pop(
                "retention_until", fields.Datetime.now() - timedelta(days=1),
            ),
            **values,
        })

    def _approved_document(self, **values):
        return self._document(
            deletion_approved_by_id=self.authorized.id,
            deletion_approved_at=fields.Datetime.now(),
            deletion_reason="[QA] Invented disposable file.",
            **values,
        )

    def test_client_context_cannot_approve_without_existing_authority(self):
        for marker in JSON_MARKERS:
            with self.subTest(marker=marker):
                document = self._document()
                with self.assertRaises(AccessError):
                    document.with_user(self.manager).with_context(
                        usl_document_deletion_approval_guarded=marker,
                    ).approve_permanent_deletion("[QA] Forged reason must not be saved.")
                self.assertFalse(document.deletion_approved_at)
                self.assertFalse(document.deletion_reason)

    def test_client_context_cannot_delete_without_existing_authority(self):
        for marker in JSON_MARKERS:
            with self.subTest(marker=marker):
                document = self._approved_document()
                with patch.object(
                    PaperlessClient, "permanently_delete_trashed_documents",
                    return_value={"result": "OK"},
                ) as provider:
                    with self.assertRaises(AccessError):
                        document.with_user(self.manager).with_context(
                            usl_document_permanent_delete_guarded=marker,
                        ).permanently_delete_from_trash()
                    provider.assert_not_called()
                self.assertEqual(document.availability_state, "trashed")
                self.assertFalse(document.permanently_deleted_at)

    def test_checked_actions_keep_native_workflow_and_one_audit_per_decision(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                document = self._document(
                    deletion_reason="[QA] Synthetic expired evidence.",
                )
                native = document.with_user(self.authorized)
                if direct:
                    self.assertTrue(native.approve_permanent_deletion(document.deletion_reason))
                else:
                    self.assertTrue(native.action_approve_permanent_deletion())
                self.assertEqual(document.deletion_approved_by_id, self.authorized)
                self.assertTrue(document.deletion_approved_at)
                with patch.object(
                    PaperlessClient, "permanently_delete_trashed_documents",
                    return_value={"result": "OK"},
                ) as provider:
                    result = (native.permanently_delete_from_trash() if direct
                              else native.action_permanently_delete_from_trash())
                    self.assertTrue(result)
                    provider.assert_called_once_with([document.paperless_id])
                self.assertEqual(document.availability_state, "permanently_deleted")
                self.assertTrue(document.permanently_deleted_at)
                self.assertTrue(document.exists())
                events = self.env["usl.audit.event"].search([
                    ("actor_id", "=", self.authorized.id),
                    ("event_type", "=", "protected_action"),
                    ("model_name", "=", "usl.document"),
                    ("record_ids", "=", str(document.ids)),
                ])
                self.assertEqual(len(events), 2)
                self.assertEqual(set(events.mapped("action_key")), {
                    "guard:documents.permanent_deletion.approve",
                    "guard:documents.permanent_delete",
                })
                self.assertTrue(all(events.mapped("policy_digest")))
                with self.assertRaises(UserError):
                    events.write({"operation": "read"})

    def test_public_agent_dispatch_remains_protected_for_both_entry_points(self):
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        identity = self.env["usl.agent"].with_user(self.agent.user_id).current_identity()
        self.assertEqual(identity["agent"]["id"], self.agent.id)
        for method in (
            "approve_permanent_deletion", "action_approve_permanent_deletion",
            "permanently_delete_from_trash", "action_permanently_delete_from_trash",
        ):
            with self.subTest(method=method), self.assertRaises(AccessError):
                UslAgentJson2Controller._check_agent_call(
                    agent=self.agent,
                    model_name="usl.document",
                    method_name=method,
                    kwargs={"reason": "[QA] Synthetic reason"} if method == "approve_permanent_deletion" else {},
                )

    def test_native_retention_and_provider_failures_do_not_complete_deletion(self):
        for values, message in (
            ({"retention_hold": True}, "retention hold"),
            (
                {"retention_until": fields.Datetime.now() + timedelta(days=1)},
                "retention period",
            ),
        ):
            document = self._approved_document(**values)
            with patch.object(
                PaperlessClient, "permanently_delete_trashed_documents", return_value={},
            ) as provider:
                with self.assertRaisesRegex(UserError, message):
                    document.with_user(self.authorized).action_permanently_delete_from_trash()
                provider.assert_not_called()
            self.assertEqual(document.availability_state, "trashed")
        document = self._approved_document()
        with patch.object(PaperlessClient, "permanently_delete_trashed_documents", side_effect=UserError("[QA] Offline provider unavailable")) as provider:
            with self.assertRaisesRegex(UserError, "Offline provider"):
                document.with_user(self.authorized).action_permanently_delete_from_trash()
            provider.assert_called_once_with([document.paperless_id])
        self.assertEqual(document.availability_state, "trashed")
        self.assertFalse(document.permanently_deleted_at)

    def test_native_company_and_manager_authority_still_apply(self):
        other = self._approved_document(company_id=self.other_company.id)
        with patch.object(
            PaperlessClient, "permanently_delete_trashed_documents", return_value={},
        ) as provider:
            with self.assertRaises(AccessError):
                other.with_user(self.authorized).with_context(allowed_company_ids=self.company.ids).action_permanently_delete_from_trash()
            provider.assert_not_called()
        self.reader.write({"group_ids": [Command.link(self.irreversible.id)]})
        document = self._document()
        with self.assertRaises(AccessError):
            document.with_user(self.reader).action_approve_permanent_deletion()
        self.assertFalse(document.deletion_approved_at)

    def test_native_reason_approval_and_active_relationship_requirements_remain(self):
        document = self._document()
        with self.assertRaises(ValidationError):
            document.with_user(self.authorized).approve_permanent_deletion("  ")
        with patch.object(
            PaperlessClient, "permanently_delete_trashed_documents", return_value={},
        ) as provider:
            with self.assertRaisesRegex(UserError, "Approve permanent deletion"):
                document.with_user(self.authorized).permanently_delete_from_trash()
            provider.assert_not_called()
        document = self._approved_document()
        partner = self.env["res.partner"].create({"name": "[QA] Retained relationship", "company_id": self.company.id})
        link = self.env["usl.document.link"].create({
            "document_id": document.id, "res_model": "res.partner", "res_id": partner.id,
            "record_name": partner.name, "company_id": self.company.id,
        })
        with patch.object(
            PaperlessClient, "permanently_delete_trashed_documents", return_value={},
        ) as provider:
            with self.assertRaisesRegex(UserError, "Remove every Odoo relationship"):
                document.with_user(self.authorized).action_permanently_delete_from_trash()
            provider.assert_not_called()
        self.assertTrue(link.active)
        self.assertEqual(document.availability_state, "trashed")
