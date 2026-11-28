/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";

export class ClassifierHistoryDialog extends Component {
    static template = "usl_documents.ClassifierHistoryDialog";
    static components = { Dialog };
    static props = { close: Function, documentName: String, canEdit: Boolean,
        history: Function, documents: Function, undo: Function, refresh: Function };

    setup() {
        this.state = useState({ events: [], documents: [], batch: null, busy: false, error: "", results: {} });
        onWillStart(() => this.loadHistory());
        this.env.dialogData.dismiss = () => this.pending;
    }

    get title() {
        return _t("Automatic tag history");
    }

    async loadHistory() {
        this.state.error = "";
        try {
            this.state.events = await this.props.history();
        } catch (error) {
            this.state.error = error.data?.message || _t("Tag history could not be loaded.");
        }
    }

    description(event) {
        const status = { applied: _t("Applied"), removed: _t("Removed manually"),
            undone: _t("Undone"), kept: _t("Kept (changed by hand)") };
        return status[event.status] || event.status;
    }

    scoreSummary(event) {
        const score = Math.round(100 * event.score);
        return _t("Score %s/100 · Minimum %s", score, event.minimum_score);
    }

    tagsToRemove(document) {
        return _t("Tags to remove: %s", document.tags.join(", "));
    }

    date(value) {
        return formatDateTime(luxon.DateTime.fromISO(value));
    }

    async review(batch) {
        if (this.state.busy) { return; }
        this.state.busy = true;
        this.state.error = "";
        try {
            this.state.documents = await this.props.documents(batch);
            this.state.results = {};
            this.state.batch = batch;
        } catch (error) {
            this.state.error = error.data?.message || _t("The batch could not be loaded.");
        } finally {
            this.state.busy = false;
        }
    }

    get pendingDocuments() {
        return this.state.documents.filter(doc => !this.state.results[doc.id]?.done);
    }

    async undo() {
        if (this.state.busy) { return; }
        this.state.busy = true;
        const batch = this.state.batch;
        this.pending = (async () => {
            for (const document of this.pendingDocuments) {
                try {
                    const result = await this.props.undo(document.id, batch);
                    this.state.results[document.id] = { done: true, text: result.removed
                        ? _t("Tags removed: %s", result.removed) : _t("No remaining automatic change") };
                } catch (error) {
                    this.state.results[document.id] = { done: false,
                        text: error.data?.message || _t("Could not confirm. Retry to check the archive.") };
                }
            }
            await this.props.refresh();
        })();
        try {
            await this.pending;
        } finally {
            this.state.busy = false;
            this.pending = null;
        }
    }

    async back() {
        this.state.batch = null;
        await this.loadHistory();
    }
}
