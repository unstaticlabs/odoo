from odoo import _, models
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    def _documents_visible_for_permission_sync(self):
        return {
            user.id: set(
                self.env["usl.document"]
                .with_user(user)
                .search([])
                .ids,
            )
            for user in self
        }

    def write(self, values):
        if self.env.context.get("usl_documents_user_access_no_sync"):
            return super().write(values)
        access_fields = {"company_ids", "company_id", "group_ids", "active"}
        tracked = self.filtered(
            lambda user: self.env["usl.paperless.user.mapping"].sudo().search_count(
                [
                    ("user_id", "=", user.id),
                    ("active", "=", True),
                    ("sync_state", "=", "synchronized"),
                ],
            ),
        )
        before = (
            tracked._documents_visible_for_permission_sync()
            if access_fields.intersection(values)
            else {}
        )
        result = super().write(values)
        if not before:
            return result
        after = tracked._documents_visible_for_permission_sync()
        changed_ids = set().union(
            *(
                before[user.id] ^ after[user.id]
                for user in tracked
            ),
        )
        if {"group_ids", "active"}.intersection(values):
            # A role change can alter Paperless change permission without
            # changing the Odoo read set. Re-evaluate every root visible before
            # or after so manager/edit grants are revoked fail-closed as well.
            changed_ids.update(
                set().union(
                    *(
                        before[user.id] | after[user.id]
                        for user in tracked
                    ),
                ),
            )
        if not changed_ids:
            return result
        documents = self.env["usl.document"].browse(changed_ids).exists()
        documents.with_user(self.env.ref("base.user_admin")).action_sync_permissions()
        removed_ids = set().union(
            *(before[user.id] - after[user.id] for user in tracked),
        )
        if documents.filtered(
            lambda document: (
                document.id in removed_ids
                and document.permission_sync_state == "failed"
            ),
        ):
            raise UserError(
                _(
                    "Access was not changed because Paperless could not safely "
                    "revoke one or more document permissions. Retry after restoring "
                    "the archive service.",
                ),
            )
        return result
