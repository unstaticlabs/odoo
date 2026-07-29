/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
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
        this.state = useState({
            loading: true,
            uploading: false,
            dragged: false,
            degraded: false,
            error: "",
            query: "",
            workspace: "recent",
            view: "cards",
            sort: "recent",
            companyId: "",
            documentType: "",
            companies: [],
            documentTypes: [],
            page: 1,
            count: 0,
            pageSize: 24,
            documents: [],
            selected: null,
            operation: null,
        });
        onWillStart(() => this.load());
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
            });
            this.state.documents = result.documents;
            this.state.count = result.count;
            this.state.degraded = result.degraded;
            this.state.companies = result.companies || this.state.companies;
            this.state.documentTypes =
                result.document_types || this.state.documentTypes;
            this.state.error = result.error || "";
            if (this.state.selected) {
                this.state.selected =
                    result.documents.find((item) => item.id === this.state.selected.id) ||
                    null;
            }
        } catch (error) {
            this.state.degraded = true;
            this.state.error =
                error.data?.message || error.message || "The archive could not be loaded.";
        } finally {
            this.state.loading = false;
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

    select(document) {
        this.state.selected = document;
    }

    closeDetail() {
        this.state.selected = null;
    }

    openRecord(document) {
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
        } catch (error) {
            const message =
                error.data?.message || error.message || "The document could not be linked.";
            this.notification.add(message, { type: "danger", sticky: true });
        }
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
                await this.load();
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
