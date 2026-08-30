import { AttachmentList } from "@mail/core/common/attachment_list";
import { Attachment } from "@mail/core/common/attachment_model";
import { fields } from "@mail/model/export";

import { onWillUnmount, useEffect, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

/**
 * Newly uploaded attachments can use their scoped ownership token. Persisted
 * attachments must expose actual write access instead of inheriting the parent
 * record's broader write capability.
 *
 * @param {import("models").Attachment} attachment
 */
export function canPersistPdfThumbnail(attachment) {
    return Boolean(
        attachment.ownership_token || attachment.uslCanUpdateThumbnail
    );
}

patch(Attachment.prototype, {
    setup() {
        super.setup(...arguments);
        this.uslCanUpdateThumbnail = fields.Attr();
    },

    async setPdfThumbnail() {
        if (!canPersistPdfThumbnail(this)) {
            return;
        }
        return super.setPdfThumbnail(...arguments);
    },
});

patch(AttachmentList.prototype, {
    setup() {
        super.setup(...arguments);
        this.uslDocumentsAction = useService("action");
        this.uslDocumentsOrm = useService("orm");
        this.uslDocumentsNotification = useService("notification");
        this.uslDocumentsKeepState = useState({ byAttachment: {} });
        this.uslDocumentsRefreshTimer = null;
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
        onWillUnmount(() => {
            if (this.uslDocumentsRefreshTimer) {
                browser.clearTimeout(this.uslDocumentsRefreshTimer);
            }
        });
    },

    async loadKeepInDocumentsStates() {
        const attachmentIds = this.props.attachments
            .map((attachment) => attachment.id)
            .filter((attachmentId) => attachmentId > 0);
        if (!attachmentIds.length || this.env.inComposer) {
            this.uslDocumentsKeepState.byAttachment = {};
            this.scheduleKeepInDocumentsRefresh();
            return;
        }
        try {
            this.uslDocumentsKeepState.byAttachment =
                await this.uslDocumentsOrm.call(
                    "ir.attachment",
                    "get_keep_in_documents_details",
                    [attachmentIds]
                );
            this.scheduleKeepInDocumentsRefresh();
        } catch {
            // The attachment remains completely usable in Odoo if Documents is
            // unavailable or the current user has no Documents access.
            this.uslDocumentsKeepState.byAttachment = {};
            this.scheduleKeepInDocumentsRefresh();
        }
    },

    canKeepInDocuments(attachment) {
        return (
            !this.env.inComposer &&
            this.keepInDocumentsDetail(attachment)?.state === "available"
        );
    },

    keepInDocumentsDetail(attachment) {
        return this.uslDocumentsKeepState.byAttachment[String(attachment.id)];
    },

    isArchivingInDocuments(attachment) {
        return ["pending", "uploading", "processing"].includes(
            this.keepInDocumentsDetail(attachment)?.state
        );
    },

    canOpenInDocuments(attachment) {
        const detail = this.keepInDocumentsDetail(attachment);
        return Boolean(
            detail?.document_id && ["archived", "duplicate"].includes(detail.state)
        );
    },

    hasDocumentsArchiveFailure(attachment) {
        return this.keepInDocumentsDetail(attachment)?.state === "failed";
    },

    scheduleKeepInDocumentsRefresh() {
        if (this.uslDocumentsRefreshTimer) {
            browser.clearTimeout(this.uslDocumentsRefreshTimer);
            this.uslDocumentsRefreshTimer = null;
        }
        if (
            Object.values(this.uslDocumentsKeepState.byAttachment).some((detail) =>
                ["pending", "uploading", "processing"].includes(detail.state)
            )
        ) {
            this.uslDocumentsRefreshTimer = browser.setTimeout(() => {
                this.uslDocumentsRefreshTimer = null;
                this.loadKeepInDocumentsStates();
            }, 3000);
        }
    },

    async keepInDocuments(attachment) {
        if (!this.canKeepInDocuments(attachment)) {
            return;
        }
        this.uslDocumentsKeepState.byAttachment[String(attachment.id)] = {
            state: "uploading",
            status_label: _t("Starting Documents archiving"),
        };
        let result;
        try {
            result = await this.uslDocumentsOrm.call(
                "ir.attachment",
                "action_keep_in_documents_from_ui",
                [[attachment.id]]
            );
        } catch (error) {
            this.uslDocumentsKeepState.byAttachment[String(attachment.id)] =
                {
                    state: "available",
                    status_label: _t("Keep in Documents"),
                };
            this.uslDocumentsNotification.add(
                error.data?.message ||
                    error.message ||
                    _t("This file could not be kept in Documents."),
                { type: "danger", sticky: true }
            );
            return;
        }
        this.uslDocumentsNotification.add(
            result.message || _t("This file will be kept in Documents."),
            { type: "success" }
        );
        this.uslDocumentsKeepState.byAttachment[String(attachment.id)] =
            result.detail;
        this.scheduleKeepInDocumentsRefresh();
    },

    async openInDocuments(attachment) {
        const action = await this.uslDocumentsOrm.call(
            "ir.attachment",
            "action_open_in_documents",
            [[attachment.id]]
        );
        return this.uslDocumentsAction.doAction(action);
    },

    getActions(attachment) {
        const actions = super.getActions(...arguments);
        if (this.canKeepInDocuments(attachment)) {
            actions.unshift({
                label: _t("Keep in Documents"),
                icon: "fa fa-bookmark-o",
                onSelect: () => this.keepInDocuments(attachment),
            });
        } else if (this.canOpenInDocuments(attachment)) {
            actions.unshift({
                label: _t("Open in Documents"),
                icon: "fa fa-folder-open-o",
                onSelect: () => this.openInDocuments(attachment),
            });
        }
        return actions;
    },
});
