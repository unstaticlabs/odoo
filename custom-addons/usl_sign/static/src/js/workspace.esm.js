/** @odoo-module **/

import {Component, onWillStart, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";

const LANDING_SECTIONS = [
    ["sign_now", _t("Sign now"), _t("Documents waiting for your signature"), "fa-pencil"],
    ["decide", _t("Decide"), _t("Business decisions waiting for you"), "fa-check-circle"],
    ["prepare", _t("Prepare and send"), _t("Drafts and ready requests you manage"), "fa-paper-plane"],
    ["issues", _t("Resolve issues"), _t("Requests that cannot move forward"), "fa-exclamation-triangle"],
    ["waiting", _t("Waiting on others"), _t("Requests progressing without your action"), "fa-clock-o"],
    ["completed", _t("Recently completed"), _t("Validated and archived results"), "fa-folder-open"],
];

export class SignLanding extends Component {
    static template = "usl_sign.Landing";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.sections = LANDING_SECTIONS;
        this.state = useState({loading: true, error: false, data: {sections: {}}});
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.error = false;
        try {
            this.state.data = await this.orm.call("usl.sign.workspace", "get_landing", []);
        } catch {
            this.state.error = true;
        } finally {
            this.state.loading = false;
        }
    }

    getSection(key) {
        return this.state.data.sections[key] || {count: 0, items: []};
    }

    start() {
        return this.action.doAction("usl_sign.sign_start_action");
    }

    async openItem(item) {
        if (item.action.type === "call") {
            try {
                const result = await this.orm.call(
                    item.action.model,
                    item.action.method,
                    [[item.action.id]],
                );
                if (result) {
                    return this.action.doAction(result);
                }
            } catch (error) {
                this.notification.add(error.message || _t("This item could not be opened."), {
                    type: "danger",
                });
            }
            return;
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: item.action.model,
            res_id: item.action.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

export class SignLibrary extends Component {
    static template = "usl_sign.Library";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            section: "templates",
            search: "",
            loading: true,
            error: false,
            items: [],
            total: 0,
            offset: 0,
            limit: 24,
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.error = false;
        try {
            const result = await this.orm.call("usl.sign.workspace", "get_library", [], {
                section: this.state.section,
                search: this.state.search,
                offset: this.state.offset,
                limit: this.state.limit,
            });
            this.state.items = result.items;
            this.state.total = result.total;
        } catch {
            this.state.error = true;
        } finally {
            this.state.loading = false;
        }
    }

    chooseSection(section) {
        if (this.state.section === section) {
            return;
        }
        this.state.section = section;
        this.state.offset = 0;
        this.load();
    }

    updateSearch(event) {
        this.state.search = event.target.value;
    }

    submitSearch(event) {
        event.preventDefault();
        this.state.offset = 0;
        this.load();
    }

    previous() {
        this.state.offset = Math.max(0, this.state.offset - this.state.limit);
        this.load();
    }

    next() {
        this.state.offset += this.state.limit;
        this.load();
    }

    openTemplate(item) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sign.oca.template",
            res_id: item.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    useTemplate(item) {
        if (!item.ready) {
            return this.openTemplate(item);
        }
        return this.action.doAction("sign_oca.sign_oca_template_generate_act_window", {
            additionalContext: {
                active_id: item.id,
                active_model: "sign.oca.template",
                default_template_id: item.id,
            },
        });
    }

    templateActionLabel(item) {
        return item.ready ? _t("Use template") : _t("Finish template");
    }

    openCompleted(item) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sign.oca.request",
            res_id: item.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openTimestamp(item) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "usl.sign.daily.manifest",
            res_id: item.timestamp_manifest_id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("usl_sign.landing", SignLanding);
registry.category("actions").add("usl_sign.library", SignLibrary);
