/** @odoo-module **/

import {Component, onWillStart, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";

const LANDING_SECTIONS = [
    ["sign_now", _t("Sign now"), _t("Documents waiting for your signature"), "fa-pencil"],
    ["prepare", _t("Prepare and send"), _t("Documents you are getting ready"), "fa-paper-plane"],
    ["issues", _t("Needs attention"), _t("Documents waiting for a fix or setup step"), "fa-exclamation-triangle"],
    ["waiting", _t("Waiting on others"), _t("Documents currently with signers"), "fa-clock-o"],
    ["completed", _t("Recently completed"), _t("Final documents ready to retrieve"), "fa-folder-open"],
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

    inspectSignatures() {
        return this.action.doAction("usl_sign.signature_inspector_action");
    }

    viewMore(actionXmlId) {
        return this.action.doAction(actionXmlId);
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

registry.category("actions").add("usl_sign.landing", SignLanding);
