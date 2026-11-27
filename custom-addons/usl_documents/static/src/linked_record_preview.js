/** @odoo-module **/

import { Component, onMounted, onWillDestroy, useExternalListener, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Dialog } from "@web/core/dialog/dialog";
import { ConnectionLostError } from "@web/core/network/rpc";

// Only the open dialog polls. A stalled request must not leave the last form on screen.
const CHECK_INTERVAL = 4000;
const MAX_CHECK_AGE = 8000;

export class LinkedRecordPreview extends Component {
    static template = "usl_documents.LinkedRecordPreview";
    static components = { Dialog };
    static props = {
        close: Function, title: String, resModel: String, resId: Number,
        checkLink: Function, onUnavailable: Function,
    };

    setup() {
        this.preview = useState({ phase: "checking", record: null });
        this.generation = 0;
        this.pending = false;
        onMounted(() => this.check());
        onWillDestroy(() => this.invalidate());
        useExternalListener(document, "visibilitychange", () => {
            this.invalidate();
            this.preview.phase = "checking";
            if (!document.hidden) {
                this.check();
            }
        });
        useExternalListener(window, "focus", () => {
            if (!document.hidden && this.preview.phase !== "unavailable") {
                this.invalidate();
                this.preview.phase = "checking";
                this.check();
            }
        });
    }

    invalidate() {
        this.preview.record = null;
        this.generation++;
        this.pending = false;
        browser.clearTimeout(this.nextCheck);
        browser.clearTimeout(this.expiry);
    }

    unavailable() {
        this.invalidate();
        this.preview.phase = "unavailable";
        this.props.onUnavailable();
    }

    disconnect() {
        this.invalidate();
        this.preview.phase = "disconnected";
    }

    async check() {
        if (document.hidden || this.pending) {
            return;
        }
        this.pending = true;
        const generation = this.generation;
        if (this.preview.phase !== "ready") {
            this.preview.phase = "checking";
            this.expiry = browser.setTimeout(() => this.disconnect(), MAX_CHECK_AGE);
        }
        try {
            const action = await this.props.checkLink();
            if (generation !== this.generation) {
                return;
            }
            if (action.res_model !== this.props.resModel || action.res_id !== this.props.resId) {
                this.unavailable();
                return;
            }
            this.pending = false;
            this.preview.record = action;
            this.preview.phase = "ready";
            browser.clearTimeout(this.expiry);
            this.expiry = browser.setTimeout(() => this.disconnect(), MAX_CHECK_AGE);
            this.nextCheck = browser.setTimeout(() => this.check(), CHECK_INTERVAL);
        } catch (error) {
            if (generation !== this.generation) {
                return;
            }
            const name = error.data?.name;
            if (["odoo.exceptions.AccessError", "odoo.exceptions.MissingError",
                "odoo.exceptions.ValidationError", "odoo.exceptions.UserError"].includes(name)) {
                this.unavailable();
            } else if (error instanceof ConnectionLostError) {
                this.disconnect();
            } else {
                this.invalidate();
                this.preview.phase = "error";
            }
        }
    }
}
