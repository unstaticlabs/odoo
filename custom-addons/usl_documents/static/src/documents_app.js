/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class DocumentsWorkspace extends Component {
    static template = "usl_documents.DocumentsWorkspace";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
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
            dragged: false,
            degraded: false,
            error: "",
            query: typeof restored.query === "string" ? restored.query : "",
            workspace: [
                "attention",
                "recent",
                "ingested",
                "accounting",
                "contracts",
                "banking",
                "tax",
                "hr",
                "all",
            ].includes(restored.workspace)
                ? restored.workspace
                : "recent",
            view: ["cards", "list"].includes(restored.view) ? restored.view : "cards",
            sort: ["recent", "ingested", "date", "title"].includes(restored.sort)
                ? restored.sort
                : "recent",
            companyId:
                ["string", "number"].includes(typeof restored.companyId)
                    ? String(restored.companyId)
                    : "",
            documentType:
                typeof restored.documentType === "string" ? restored.documentType : "",
            confidentiality: ["", "internal", "accounting", "hr", "private"].includes(
                restored.confidentiality
            )
                ? restored.confidentiality
                : "",
            reviewState: ["", "needs_attention", "classified", "reviewed"].includes(
                restored.reviewState
            )
                ? restored.reviewState
                : "",
            linkedRecord:
                typeof restored.linkedRecord === "string" ? restored.linkedRecord : "",
            companies: [],
            documentTypes: [],
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
            operation: null,
            operations: [],
            truncated: false,
        });
        onWillStart(() => this.load());
    }

    persistState() {
        const serialized = JSON.stringify({
                query: this.state.query,
                workspace: this.state.workspace,
                view: this.state.view,
                sort: this.state.sort,
                companyId: this.state.companyId,
                documentType: this.state.documentType,
                confidentiality: this.state.confidentiality,
                reviewState: this.state.reviewState,
                linkedRecord: this.state.linkedRecord,
                page: this.state.page,
            });
        browser.sessionStorage.setItem(this.storageKey, serialized);
        // Odoo's breadcrumb returns a record-scoped client action to the
        // global Documents action. Carry the same search state across that
        // native route transition.
        browser.sessionStorage.setItem(this.globalStorageKey, serialized);
    }

    async load() {
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await this.orm.call("usl.document", "workspace_data", [], {
                query: this.state.query,
                workspace: this.state.workspace,
                page: this.state.page,
                page_size: this.state.pageSize,
                sort: this.state.sort,
                company_id: this.state.companyId || null,
                document_type: this.state.documentType || null,
                confidentiality: this.state.confidentiality || null,
                review_state: this.state.reviewState || null,
                linked_model: this.state.linkedRecord
                    ? this.state.linkedRecord.split(":", 1)[0]
                    : null,
                linked_id: this.state.linkedRecord
                    ? Number(this.state.linkedRecord.split(":", 2)[1])
                    : null,
            });
            this.state.documents = result.documents;
            this.state.count = result.count;
            this.state.degraded = result.degraded;
            this.state.companies = result.companies || this.state.companies;
            this.state.documentTypes =
                result.document_types || this.state.documentTypes;
            this.state.linkFacets = result.link_facets || this.state.linkFacets;
            this.state.operations = result.operations || [];
            this.state.truncated = Boolean(result.truncated);
            this.state.error = result.error || "";
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
                error.data?.message || error.message || "The archive could not be loaded.";
        } finally {
            this.state.loading = false;
            this.persistState();
        }
    }

    selectWorkspace(workspace) {
        this.state.workspace = workspace;
        this.state.page = 1;
        this.state.selected = null;
        return this.load();
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

    updateFilter(field, event) {
        // Read the DOM value explicitly. OWL's generated t-model change
        // handler and a second change handler do not have a contractual
        // execution order, so load() must not depend on t-model winning it.
        this.state[field] = event.target.value;
        this.state.page = 1;
        return this.load();
    }

    async select(document) {
        this.state.selected = { ...document, preview_url: `/usl_documents/${document.id}/preview` };
        this.state.selectedLoading = true;
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
                error.data?.message || error.message || "Document details could not be loaded.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.selectedLoading = false;
        }
    }

    closeDetail() {
        this.state.selected = null;
    }

    selectVersion(version) {
        this.state.selected.preview_url = version.preview_url;
        this.state.selected.selected_version_id = version.paperless_version_id;
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
                error.data?.message || error.message || "Replacement upload failed.";
            this.state.operation = { name: file.name, state: "failed", error: message };
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
            const result = await this.orm.call("usl.document", "upload_from_odoo", [
                file.name,
                content,
                file.type || "application/octet-stream",
            ], this.recordContext
                ? {
                      res_model: this.recordContext.resModel,
                      res_id: this.recordContext.resId,
                  }
                : {});
            this.state.operation = { name: file.name, ...result };
            if (result.state === "duplicate") {
                this.notification.add(result.message, { type: "warning" });
                await this.load();
            } else {
                this.notification.add(result.message, { type: "info" });
                await this.pollOperation(result.operation_id);
            }
        } catch (error) {
            const message = error.data?.message || error.message || "Upload failed.";
            this.state.operation = { name: file.name, state: "failed", error: message };
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
            this.notification.add("Archived document linked to this record.", {
                type: "success",
            });
            await this.load();
            await this.select(this.state.selected);
        } catch (error) {
            const message =
                error.data?.message || error.message || "The document could not be linked.";
            this.notification.add(message, { type: "danger", sticky: true });
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
                "Relationship removed. The Paperless original was not deleted.",
                { type: "success" }
            );
            await this.load();
            await this.select(this.state.selected);
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || "The relationship could not be removed.",
                { type: "danger", sticky: true }
            );
        }
    }

    async openLink(link) {
        this.persistState();
        const action = await this.orm.call("usl.document.link", "action_open_record", [
            [link.id],
        ]);
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
            this.state.operation = { ...this.state.operation, ...status };
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
                this.notification.add(status.error || "Paperless processing failed.", {
                    type: "danger",
                    sticky: true,
                });
                return;
            }
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        this.notification.add(
            "Processing continues in Paperless. The visible operation will update automatically.",
            { type: "info" }
        );
    }
}

registry.category("actions").add("usl_documents.workspace", DocumentsWorkspace);
