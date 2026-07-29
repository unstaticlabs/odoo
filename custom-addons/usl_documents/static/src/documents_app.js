/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const FILTER_DEFAULTS = {
    companyId: "",
    tagIds: [],
    correspondentId: "",
    documentTypeId: "",
    dateFrom: "",
    dateTo: "",
    addedFrom: "",
    addedTo: "",
    source: "",
    confidentiality: "",
    reviewState: "",
    linkedState: "",
    linkedRecord: "",
};

export class DocumentsWorkspace extends Component {
    static template = "usl_documents.DocumentsWorkspace";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        const params = this.props.action.params || {};
        this.recordContext =
            params.res_model && params.res_id
                ? { resModel: params.res_model, resId: Number(params.res_id) }
                : null;
        this.globalStorageKey = "usl_documents.workspace.global";
        this.storageKey = `usl_documents.workspace.${
            this.recordContext
                ? `${this.recordContext.resModel}.${this.recordContext.resId}`
                : "global"
        }`;
        let restored = {};
        try {
            restored = JSON.parse(
                browser.sessionStorage.getItem(this.storageKey) ||
                    browser.sessionStorage.getItem(this.globalStorageKey) ||
                    "{}"
            );
        } catch {
            restored = {};
        }
        this.state = useState({
            loading: true,
            uploading: false,
            savingMetadata: false,
            savingView: false,
            dragged: false,
            degraded: false,
            error: "",
            query: typeof restored.query === "string" ? restored.query : "",
            workspace:
                typeof restored.workspace === "string" ? restored.workspace : "recent",
            view: ["cards", "list"].includes(restored.view) ? restored.view : "cards",
            sort: ["recent", "ingested", "date", "title"].includes(restored.sort)
                ? restored.sort
                : "recent",
            ...this.restoreFilters(restored),
            moreFilters: false,
            savedViewName: "",
            smartViews: [],
            tags: [],
            correspondents: [],
            documentTypes: [],
            companies: [],
            linkFacets: [],
            page:
                Number.isInteger(restored.page) && restored.page > 0
                    ? restored.page
                    : 1,
            count: 0,
            pageSize: 24,
            documents: [],
            selected: null,
            selectedLoading: false,
            editingMetadata: false,
            metadataDraft: null,
            operation: null,
            operations: [],
            truncated: false,
        });
        onWillStart(() => this.load());
    }

    restoreFilters(restored) {
        return {
            companyId: this.stringValue(restored.companyId),
            tagIds: Array.isArray(restored.tagIds)
                ? restored.tagIds.map(Number).filter(Boolean)
                : [],
            correspondentId: this.stringValue(restored.correspondentId),
            documentTypeId: this.stringValue(restored.documentTypeId),
            dateFrom: this.stringValue(restored.dateFrom),
            dateTo: this.stringValue(restored.dateTo),
            addedFrom: this.stringValue(restored.addedFrom),
            addedTo: this.stringValue(restored.addedTo),
            source: this.stringValue(restored.source),
            confidentiality: this.stringValue(restored.confidentiality),
            reviewState: this.stringValue(restored.reviewState),
            linkedState: this.stringValue(restored.linkedState),
            linkedRecord: this.stringValue(restored.linkedRecord),
        };
    }

    stringValue(value) {
        return ["string", "number"].includes(typeof value) ? String(value) : "";
    }

    get sharedViews() {
        return this.state.smartViews.filter((view) => !view.personal);
    }

    get personalViews() {
        return this.state.smartViews.filter((view) => view.personal);
    }

    get activeFilterCount() {
        return [
            this.state.companyId,
            this.state.tagIds.length,
            this.state.correspondentId,
            this.state.documentTypeId,
            this.state.dateFrom,
            this.state.dateTo,
            this.state.addedFrom,
            this.state.addedTo,
            this.state.source,
            this.state.confidentiality,
            this.state.reviewState,
            this.state.linkedState,
            this.state.linkedRecord,
        ].filter(Boolean).length;
    }

    persistState() {
        const serialized = JSON.stringify({
            query: this.state.query,
            workspace: this.state.workspace,
            view: this.state.view,
            sort: this.state.sort,
            page: this.state.page,
            ...Object.fromEntries(
                Object.keys(FILTER_DEFAULTS).map((key) => [key, this.state[key]])
            ),
        });
        browser.sessionStorage.setItem(this.storageKey, serialized);
        browser.sessionStorage.setItem(this.globalStorageKey, serialized);
    }

    workspaceKwargs() {
        return {
            query: this.state.query,
            workspace: this.state.workspace,
            page: this.state.page,
            page_size: this.state.pageSize,
            sort: this.state.sort,
            company_id: this.state.companyId || null,
            tag_ids: this.state.tagIds,
            correspondent_id: this.state.correspondentId || null,
            document_type_id: this.state.documentTypeId || null,
            date_from: this.state.dateFrom || null,
            date_to: this.state.dateTo || null,
            added_from: this.state.addedFrom || null,
            added_to: this.state.addedTo || null,
            source: this.state.source || null,
            confidentiality: this.state.confidentiality || null,
            review_state: this.state.reviewState || null,
            linked_state: this.state.linkedState || null,
            linked_model: this.state.linkedRecord
                ? this.state.linkedRecord.split(":", 1)[0]
                : null,
            linked_id: this.state.linkedRecord
                ? Number(this.state.linkedRecord.split(":", 2)[1])
                : null,
        };
    }

    async load() {
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await this.orm.call(
                "usl.document",
                "workspace_data",
                [],
                this.workspaceKwargs()
            );
            this.state.documents = result.documents;
            this.state.count = result.count;
            this.state.degraded = result.degraded;
            this.state.smartViews = result.smart_views || this.state.smartViews;
            this.state.tags = result.tags || this.state.tags;
            this.state.correspondents =
                result.correspondents || this.state.correspondents;
            this.state.documentTypes =
                result.document_types || this.state.documentTypes;
            this.state.companies = result.companies || this.state.companies;
            this.state.linkFacets = result.link_facets || this.state.linkFacets;
            this.state.operations = result.operations || [];
            this.state.truncated = Boolean(result.truncated);
            this.state.error = result.error || "";
            this.state.workspace = result.selected_workspace || this.state.workspace;
            if (this.state.selected) {
                const refreshed = result.documents.find(
                    (item) => item.id === this.state.selected.id
                );
                if (refreshed) {
                    this.state.selected = { ...this.state.selected, ...refreshed };
                }
            }
        } catch (error) {
            this.state.degraded = true;
            this.state.error =
                error.data?.message ||
                error.message ||
                "The archive could not be loaded.";
        } finally {
            this.state.loading = false;
            this.persistState();
        }
    }

    selectWorkspace(view) {
        this.state.workspace = view.key;
        if (view.filters && Object.keys(view.filters).length) {
            this.applySavedFilters(view.filters);
        }
        this.state.page = 1;
        this.state.selected = null;
        return this.load();
    }

    applySavedFilters(filters) {
        this.state.query = filters.query || "";
        this.state.sort = filters.sort || "recent";
        for (const [key, defaultValue] of Object.entries(FILTER_DEFAULTS)) {
            const serializedKey =
                {
                    companyId: "company_id",
                    tagIds: "tag_ids",
                    correspondentId: "correspondent_id",
                    documentTypeId: "document_type_id",
                    dateFrom: "date_from",
                    dateTo: "date_to",
                    addedFrom: "added_from",
                    addedTo: "added_to",
                    reviewState: "review_state",
                    linkedState: "linked_state",
                    linkedRecord: "linked_record",
                }[key] || key;
            this.state[key] =
                filters[serializedKey] === undefined
                    ? defaultValue
                    : key === "tagIds"
                    ? filters[serializedKey].map(Number)
                    : String(filters[serializedKey] || "");
        }
    }

    search(event) {
        event.preventDefault();
        this.state.page = 1;
        return this.load();
    }

    clearSearch() {
        this.state.query = "";
        this.state.page = 1;
        return this.load();
    }

    clearFilters() {
        for (const [key, value] of Object.entries(FILTER_DEFAULTS)) {
            this.state[key] = Array.isArray(value) ? [] : value;
        }
        this.state.page = 1;
        return this.load();
    }

    clearAll() {
        this.state.query = "";
        return this.clearFilters();
    }

    updateFilter(field, event) {
        this.state[field] = event.target.value;
        this.state.page = 1;
        return this.load();
    }

    toggleLinkedFilter() {
        this.state.linkedState =
            this.state.linkedState === "linked" ? "" : "linked";
        this.state.page = 1;
        return this.load();
    }

    toggleTagFilter(tagId) {
        this.state.tagIds = this.state.tagIds.includes(tagId)
            ? this.state.tagIds.filter((id) => id !== tagId)
            : [...this.state.tagIds, tagId];
        this.state.page = 1;
        return this.load();
    }

    async savePersonalView() {
        this.state.savingView = true;
        try {
            const filters = {
                query: this.state.query,
                sort: this.state.sort,
                company_id: this.state.companyId,
                tag_ids: this.state.tagIds,
                correspondent_id: this.state.correspondentId,
                document_type_id: this.state.documentTypeId,
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
                added_from: this.state.addedFrom,
                added_to: this.state.addedTo,
                source: this.state.source,
                confidentiality: this.state.confidentiality,
                review_state: this.state.reviewState,
                linked_state: this.state.linkedState,
                linked_record: this.state.linkedRecord,
            };
            const view = await this.orm.call(
                "usl.document.smart.view",
                "save_personal_view",
                [this.state.savedViewName, filters]
            );
            this.state.smartViews.push(view);
            this.state.savedViewName = "";
            this.notification.add("View saved for you.", { type: "success" });
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || "The view could not be saved.",
                { type: "danger" }
            );
        } finally {
            this.state.savingView = false;
        }
    }

    async removePersonalView(view, event) {
        event.stopPropagation();
        await this.orm.unlink("usl.document.smart.view", [view.id]);
        this.state.smartViews = this.state.smartViews.filter(
            (item) => item.id !== view.id
        );
        if (this.state.workspace === view.key) {
            this.state.workspace = "recent";
            await this.load();
        }
    }

    async select(document) {
        this.state.selected = {
            ...document,
            preview_url: `/usl_documents/${document.id}/preview`,
        };
        this.state.selectedLoading = true;
        this.state.editingMetadata = false;
        try {
            const detail = await this.orm.call("usl.document", "document_detail", [
                document.id,
            ]);
            if (this.state.selected?.id === document.id) {
                this.state.selected = {
                    ...detail,
                    preview_url: `/usl_documents/${document.id}/preview`,
                };
            }
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "Document details could not be loaded.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.selectedLoading = false;
        }
    }

    closeDetail() {
        this.state.selected = null;
        this.state.editingMetadata = false;
    }

    beginMetadataEdit() {
        const selected = this.state.selected;
        this.state.metadataDraft = {
            name: selected.name || "",
            document_date: selected.date || "",
            correspondent_id: selected.correspondent_id || false,
            document_type_id: selected.document_type_id || false,
            tag_ids: (selected.tags || []).map((tag) => tag.id),
        };
        this.state.editingMetadata = true;
    }

    cancelMetadataEdit() {
        this.state.editingMetadata = false;
        this.state.metadataDraft = null;
    }

    updateMetadataDraft(field, event) {
        this.state.metadataDraft[field] =
            ["correspondent_id", "document_type_id"].includes(field)
                ? Number(event.target.value) || false
                : event.target.value;
    }

    toggleMetadataTag(tagId) {
        const selected = this.state.metadataDraft.tag_ids;
        this.state.metadataDraft.tag_ids = selected.includes(tagId)
            ? selected.filter((id) => id !== tagId)
            : [...selected, tagId];
    }

    async saveMetadata() {
        this.state.savingMetadata = true;
        try {
            const detail = await this.orm.call(
                "usl.document",
                "update_archive_metadata",
                [[this.state.selected.id], this.state.metadataDraft]
            );
            this.state.selected = {
                ...detail,
                preview_url: `/usl_documents/${detail.id}/preview`,
            };
            this.state.editingMetadata = false;
            this.notification.add("Document updated.", { type: "success" });
            await this.load();
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "The document could not be updated.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.savingMetadata = false;
        }
    }

    selectVersion(version) {
        this.state.selected.preview_url = version.preview_url;
        this.state.selected.selected_version_id = version.paperless_version_id;
    }

    restoreVersion(version) {
        this.dialog.add(ConfirmationDialog, {
            title: "Restore this file?",
            body:
                "This file will become current as a new version. Nothing will be deleted.",
            confirmLabel: "Restore as current",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "usl.document",
                        "restore_version",
                        [[this.state.selected.id], version.paperless_version_id]
                    );
                    this.state.operation = {
                        name: version.filename || version.label || "Document version",
                        ...result,
                    };
                    this.notification.add(result.message, { type: "info" });
                    await this.pollOperation(result.operation_id);
                } catch (error) {
                    this.notification.add(
                        error.data?.message ||
                            error.message ||
                            "The version could not be restored.",
                        { type: "danger", sticky: true }
                    );
                }
            },
        });
    }

    openRecord(document) {
        this.persistState();
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "usl.document",
            res_id: document.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    previousPage() {
        if (this.state.page > 1) {
            this.state.page -= 1;
            return this.load();
        }
    }

    nextPage() {
        if (this.state.page * this.state.pageSize < this.state.count) {
            this.state.page += 1;
            return this.load();
        }
    }

    setView(view) {
        this.state.view = view;
        this.persistState();
    }

    onDragOver(event) {
        event.preventDefault();
        this.state.dragged = true;
    }

    onDragLeave() {
        this.state.dragged = false;
    }

    onDrop(event) {
        event.preventDefault();
        this.state.dragged = false;
        const file = event.dataTransfer.files?.[0];
        if (file) {
            return this.upload(file);
        }
    }

    onFileChange(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) {
            return this.upload(file);
        }
    }

    onVersionFileChange(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) {
            return this.uploadVersion(file);
        }
    }

    async uploadVersion(file) {
        if (!this.state.selected) {
            return;
        }
        this.state.uploading = true;
        this.state.operation = { name: file.name, state: "uploading" };
        try {
            const content = await this.fileAsBase64(file);
            const result = await this.orm.call("usl.document", "upload_new_version", [
                [this.state.selected.id],
                file.name,
                content,
                file.type || "application/octet-stream",
                file.name,
            ]);
            this.state.operation = { name: file.name, ...result };
            this.notification.add(result.message, {
                type: result.state === "duplicate" ? "warning" : "info",
            });
            if (result.state === "processing") {
                await this.pollOperation(result.operation_id);
            } else {
                await this.select(this.state.selected);
            }
        } catch (error) {
            const message =
                error.data?.message || error.message || "Version upload failed.";
            this.state.operation = {
                name: file.name,
                state: "failed",
                error: message,
            };
            this.notification.add(message, { type: "danger", sticky: true });
        } finally {
            this.state.uploading = false;
        }
    }

    async upload(file) {
        this.state.uploading = true;
        this.state.operation = { name: file.name, state: "uploading" };
        try {
            const content = await this.fileAsBase64(file);
            const result = await this.orm.call(
                "usl.document",
                "upload_from_odoo",
                [
                    file.name,
                    content,
                    file.type || "application/octet-stream",
                ],
                this.recordContext
                    ? {
                          res_model: this.recordContext.resModel,
                          res_id: this.recordContext.resId,
                      }
                    : {}
            );
            this.state.operation = { name: file.name, ...result };
            if (result.state === "duplicate") {
                this.notification.add(result.message, { type: "warning" });
                await this.load();
            } else {
                this.notification.add(result.message, { type: "info" });
                await this.pollOperation(result.operation_id);
            }
        } catch (error) {
            const message =
                error.data?.message || error.message || "Upload failed.";
            this.state.operation = {
                name: file.name,
                state: "failed",
                error: message,
            };
            this.notification.add(message, { type: "danger", sticky: true });
        } finally {
            this.state.uploading = false;
        }
    }

    async linkSelected() {
        if (!this.recordContext || !this.state.selected) {
            return;
        }
        try {
            await this.orm.call("usl.document", "link_to_record", [
                [this.state.selected.id],
                this.recordContext.resModel,
                this.recordContext.resId,
            ]);
            this.notification.add("Document linked to this record.", {
                type: "success",
            });
            await this.load();
            await this.select(this.state.selected);
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "The document could not be linked.",
                { type: "danger", sticky: true }
            );
        }
    }

    async unlinkCurrent() {
        if (!this.recordContext || !this.state.selected) {
            return;
        }
        try {
            await this.orm.call("usl.document", "unlink_from_record", [
                [this.state.selected.id],
                this.recordContext.resModel,
                this.recordContext.resId,
            ]);
            this.notification.add(
                "Link removed. The archived document was not deleted.",
                { type: "success" }
            );
            await this.load();
            await this.select(this.state.selected);
        } catch (error) {
            this.notification.add(
                error.data?.message ||
                    error.message ||
                    "The link could not be removed.",
                { type: "danger", sticky: true }
            );
        }
    }

    async openLink(link) {
        this.persistState();
        const action = await this.orm.call(
            "usl.document.link",
            "action_open_record",
            [[link.id]]
        );
        return this.action.doAction(action);
    }

    isLinkedToCurrent() {
        return Boolean(
            this.recordContext &&
                this.state.selected?.links?.some(
                    (link) =>
                        link.model === this.recordContext.resModel &&
                        link.res_id === this.recordContext.resId
                )
        );
    }

    fileAsBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = reject;
            reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
            reader.readAsDataURL(file);
        });
    }

    async pollOperation(operationId) {
        for (let attempt = 0; attempt < 30; attempt++) {
            const statuses = await this.orm.call(
                "usl.document.operation",
                "poll",
                [[operationId]]
            );
            const status = statuses[operationId];
            this.state.operation = {
                ...this.state.operation,
                ...status,
                name:
                    status.name ||
                    this.state.operation?.name ||
                    "Document",
            };
            if (status.state === "archived") {
                this.notification.add("Document archived successfully.", {
                    type: "success",
                });
                const selected = this.state.selected;
                await this.load();
                if (selected) {
                    await this.select(selected);
                }
                return;
            }
            if (status.state === "failed") {
                this.notification.add(
                    status.error || "Paperless processing failed.",
                    { type: "danger", sticky: true }
                );
                return;
            }
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        this.notification.add(
            "Processing continues in Paperless. This status will update automatically.",
            { type: "info" }
        );
    }
}

registry.category("actions").add(
    "usl_documents.workspace",
    DocumentsWorkspace
);
