import { AttachmentList } from "@mail/core/common/attachment_list";
import { Attachment } from "@mail/core/common/attachment_model";
import { fields } from "@mail/model/export";

import { Component, onWillUnmount, useEffect, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Dialog } from "@web/core/dialog/dialog";
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

export class AttachmentRemovalDialog extends Component {
    static template = "usl_documents.AttachmentRemovalDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        attachmentName: String,
        canMoveToTrash: Boolean,
        remove: Function,
    };

    setup() {
        this.state = useState({ busy: false });
    }

    get title() {
        return _t("Remove document from this record?");
    }

    get intro() {
        return _t("Choose what happens to %s.", this.props.attachmentName);
    }

    get explanation() {
        return _t(
            "Unlinking removes it from this record while keeping the archived document available in Documents. Moving it to Trash also removes the archived document from normal Documents views."
        );
    }

    get trashUnavailableMessage() {
        return _t(
            "This archived document is shared with another record or cannot be moved to Trash from here. You can still unlink it from this record."
        );
    }

    get unlinkLabel() {
        return _t("Unlink Document from Record");
    }

    get trashLabel() {
        return _t("Unlink and Move to Trash");
    }

    get cancelLabel() {
        return _t("Cancel");
    }

    async choose(removal) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.remove(removal);
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
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

    onClickUnlink(attachment) {
        if (this.env.inComposer) {
            return super.onClickUnlink(...arguments);
        }
        const detail = this.keepInDocumentsDetail(attachment);
        if (["archived", "duplicate"].includes(detail?.state)) {
            if (!detail.can_remove_from_record) {
                this.uslDocumentsNotification.add(
                    _t("You do not have permission to unlink this archived document."),
                    { type: "warning" }
                );
                return;
            }
            this.dialog.add(AttachmentRemovalDialog, {
                attachmentName: attachment.name,
                canMoveToTrash: detail.can_move_to_trash,
                remove: (removal) =>
                    this.removeArchivedAttachment(attachment, removal),
            });
            return;
        }
        if (["pending", "uploading", "processing"].includes(detail?.state)) {
            this.uslDocumentsNotification.add(
                _t("Wait for Documents archiving to finish before unlinking this file."),
                { type: "warning" }
            );
            return;
        }
        if (detail?.state === "failed") {
            this.uslDocumentsNotification.add(
                _t("Resolve the Documents archiving issue before unlinking this file."),
                { type: "warning" }
            );
            return;
        }
        if (detail?.state === "available") {
            this.uslDocumentsNotification.add(
                _t("Keep this file in Documents before unlinking it from the record."),
                { type: "warning" }
            );
            return;
        }
        return super.onClickUnlink(...arguments);
    },

    async removeArchivedAttachment(attachment, removal) {
        const result = await this.uslDocumentsOrm.call(
            "ir.attachment",
            "action_remove_archived_from_record",
            [[attachment.id], removal]
        );
        attachment.delete();
        this.uslDocumentsNotification.add(result.message, { type: "success" });
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
