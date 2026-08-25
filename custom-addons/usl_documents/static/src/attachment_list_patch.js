import { AttachmentList } from "@mail/core/common/attachment_list";

import { useEffect, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(AttachmentList.prototype, {
    setup() {
        super.setup(...arguments);
        this.uslDocumentsOrm = useService("orm");
        this.uslDocumentsNotification = useService("notification");
        this.uslDocumentsKeepState = useState({ byAttachment: {} });
        useEffect(
            () => {
                this.loadKeepInDocumentsStates();
            },
            () => [
                this.props.attachments
                    .filter((attachment) => attachment.id > 0)
                    .map((attachment) => attachment.id)
                    .sort((left, right) => left - right)
                    .join(","),
            ]
        );
    },

    async loadKeepInDocumentsStates() {
        const attachmentIds = this.props.attachments
            .map((attachment) => attachment.id)
            .filter((attachmentId) => attachmentId > 0);
        if (!attachmentIds.length || this.env.inComposer) {
            this.uslDocumentsKeepState.byAttachment = {};
            return;
        }
        try {
            this.uslDocumentsKeepState.byAttachment = await this.uslDocumentsOrm.call(
                "ir.attachment",
                "get_keep_in_documents_states",
                [attachmentIds]
            );
        } catch {
            // The attachment remains completely usable in Odoo if Documents is
            // unavailable or the current user has no Documents access.
            this.uslDocumentsKeepState.byAttachment = {};
        }
    },

    canKeepInDocuments(attachment) {
        return (
            !this.env.inComposer &&
            this.uslDocumentsKeepState.byAttachment[String(attachment.id)] ===
                "available"
        );
    },

    async keepInDocuments(attachment) {
        if (!this.canKeepInDocuments(attachment)) {
            return;
        }
        this.uslDocumentsKeepState.byAttachment[String(attachment.id)] = "busy";
        try {
            const result = await this.uslDocumentsOrm.call(
                "ir.attachment",
                "action_keep_in_documents_from_ui",
                [[attachment.id]]
            );
            this.uslDocumentsKeepState.byAttachment[String(attachment.id)] = "kept";
            this.uslDocumentsNotification.add(
                result.message || _t("This file will be kept in Documents."),
                { type: "success" }
            );
        } catch (error) {
            this.uslDocumentsKeepState.byAttachment[String(attachment.id)] =
                "available";
            this.uslDocumentsNotification.add(
                error.data?.message ||
                    error.message ||
                    _t("This file could not be kept in Documents."),
                { type: "danger", sticky: true }
            );
        }
    },

    getActions(attachment) {
        const actions = super.getActions(...arguments);
        if (this.canKeepInDocuments(attachment)) {
            actions.unshift({
                label: _t("Keep in Documents"),
                icon: "fa fa-bookmark-o",
                onSelect: () => this.keepInDocuments(attachment),
            });
        }
        return actions;
    },
});
