from odoo import _, models
from odoo.exceptions import AccessError, UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    def _documents_visible_for_permission_sync(self):
        visible = {}
        for user in self:
            try:
                visible[user.id] = set(
                    self.env["usl.document"]
                    .with_user(user)
                    .search([])
                    .ids,
                )
            except AccessError:
                # Losing the Documents role means the user can no longer read
                # the model at all. Treat that as an empty visible set so the
                # existing Paperless grants are revoked fail-closed.
                visible[user.id] = set()
        return visible

    def write(self, values):
        if (
            self.env.context.get("usl_documents_user_access_no_sync")
            and self.env.su
        ):
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
        before_manager = {
            user.id: user.has_group("usl_documents.group_documents_manager")
            for user in tracked
        }
        before_active = {user.id: user.active for user in tracked}
        result = super().write(values)
        if {
            "active",
            "usl_pocketid_access",
            "usl_identity_classification",
        }.intersection(values):
            self._invalidate_unsafe_paperless_mappings()
        if not before:
            return result
        after = tracked._documents_visible_for_permission_sync()
        changed_ids = set().union(
            *(
                before[user.id] ^ after[user.id]
                for user in tracked
            ),
        )
        manager_or_active_changed = tracked.filtered(
            lambda user: (
                before_manager[user.id]
                != user.has_group("usl_documents.group_documents_manager")
                or before_active[user.id] != user.active
            ),
        )
        if manager_or_active_changed:
            # An effective manager or active-state change can alter Paperless
            # permissions without changing the Odoo read set.  A no-op group
            # command must not enqueue an update for every visible document.
            changed_ids.update(
                set().union(
                    *(
                        before[user.id] | after[user.id]
                        for user in manager_or_active_changed
                    ),
                ),
            )
        if not changed_ids:
            return result
        documents = self.env["usl.document"].browse(changed_ids).exists()
        # Permission propagation is an internal fail-closed operation. It must
        # not depend on the functional Documents role currently assigned to
        # the Administrator account while Pocket profiles are being applied.
        documents.with_user(self.env.ref("base.user_root")).action_sync_permissions()
        removed_ids = set().union(
            *(before[user.id] - after[user.id] for user in tracked),
        )
        manager_revocation_ids = set().union(
            *(
                before[user.id] | after[user.id]
                for user in tracked
                if before_manager[user.id]
                and not user.has_group("usl_documents.group_documents_manager")
            ),
        )
        if documents.filtered(
            lambda document: (
                document.id in removed_ids | manager_revocation_ids
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
