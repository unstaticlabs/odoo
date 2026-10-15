/** @odoo-module **/

import { Component, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class DocumentBatchDialog extends Component {
    static template = "usl_documents.DocumentBatchDialog";
    static components = { Dialog };
    static props = { close: Function, documents: Array, tags: Array,
        correspondents: Array, documentTypes: Array, apply: Function, refresh: Function };

    setup() {
        this.state = useState({ operation: "add_tags", metadataIds: [],
            phase: "choose", busy: false, results: {}, query: "", stopRequested: false });
        // Use Dialog's supported asynchronous dismissal hook, rather than
        // replacing its reactive close function. Keep a pending write visible.
        this.env.dialogData.dismiss = () => this.busyDone;
        onWillUnmount(() => {
            this.disposed = true;
        });

    }

    get selectionSummary() {
        return this.props.documents.length === 1
            ? _t("1 selected document. Only the chosen metadata changes.")
            : _t("%s selected documents. Only the chosen metadata changes.", this.props.documents.length);
    }

    get catalog() {
        return this.state.operation === "add_tags" ? this.props.tags :
            this.state.operation === "set_correspondent" ? this.props.correspondents :
            this.props.documentTypes;
    }

    get matchingCatalog() {
        const query = this.state.query.trim().toLocaleLowerCase();
        return this.catalog.filter((item) => item.name.toLocaleLowerCase().includes(query));
    }

    get visibleCatalog() {
        return this.matchingCatalog.slice(0, 100);
    }

    get changeLabel() {
        const names = this.catalog.filter((item) => this.state.metadataIds.includes(item.id))
            .map((item) => item.name).join(", ");
        const label = this.state.operation === "add_tags" ? _t("Add tags") :
            this.state.operation === "set_correspondent" ? _t("Set correspondent") :
            _t("Set document type");
        return `${label}: ${names}`;
    }

    changeOperation(event) {
        this.state.operation = event.target.value;
        this.state.metadataIds = [];
        this.state.query = "";
    }

    choose(item) {
        const ids = new Set(this.state.metadataIds);
        if (this.state.operation !== "add_tags") { ids.clear(); }
        if (ids.has(item.id)) { ids.delete(item.id); } else { ids.add(item.id); }
        this.state.metadataIds = [...ids];
    }

    review() {
        if (!this.state.metadataIds.length) { return; }
        this.fixedOperation = this.state.operation;
        this.fixedIds = [...this.state.metadataIds];
        this.state.phase = "review";
    }

    get unresolved() {
        return this.props.documents.filter((doc) =>
            this.state.results[doc.id]?.status !== "completed");
    }

    async apply() {
        if (this.state.busy || !this.unresolved.length) { return; }
        this.state.busy = true;
        this.state.stopRequested = false;
        let finished;
        this.busyDone = new Promise((resolve) => { finished = resolve; });
        this.state.phase = "results";
        try {
            for (const document of this.unresolved) {
                if (this.disposed || this.state.stopRequested) { break; }
                this.state.results[document.id] = { status: "pending", message: _t("Checking archive…") };
                try {
                    this.state.results[document.id] = await this.props.apply(
                        document.id, this.fixedOperation, this.fixedIds);
                } catch (error) {
                    if (error.data?.name === "odoo.exceptions.AccessError") {
                        this.state.results[document.id] = { status: "failed", message: _t(
                            "Your current access does not allow this change. No change was sent.") };
                        continue;
                    }
                    if (error.data?.name === "odoo.exceptions.ValidationError") {
                        this.state.results[document.id] = { status: "failed", message: _t(
                            "This document or chosen metadata is unavailable. No change was sent. Close and refresh Documents.") };
                        continue;
                    }
                    // A transport error may arrive after a committed remote
                    // change. Never assert that this document stayed unchanged.
                    this.state.results[document.id] = { status: "uncertain", message: _t(
                        "The result could not be confirmed. Retry to check current access and the archive.") };
                }
            }
            try { await this.props.refresh(); } catch { /* Per-document results remain visible. */ }
        } finally { this.state.busy = false; this.busyDone = null; finished(); }
    }

    resultMessage(document) {
        return this.state.results[document.id]?.message ||
            (this.state.busy ? "" : _t("Not sent. You can retry this document."));
    }

    resultLabel(document) {
        const status = this.state.results[document.id]?.status;
        return status === "completed" ? _t("Completed") : status === "failed" ? _t("Not applied") :
            status === "uncertain" ? _t("Needs confirmation") :
            this.state.busy ? _t("Waiting") : _t("Not attempted");
    }
}
