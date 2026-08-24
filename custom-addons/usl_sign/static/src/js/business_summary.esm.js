/** @odoo-module **/

import {Component, onWillStart, onWillUpdateProps, useState} from "@odoo/owl";
import {FormController} from "@web/views/form/form_controller";
import {useService} from "@web/core/utils/hooks";

export class UslSignBusinessSummary extends Component {
    static template = "usl_sign.BusinessSummary";
    static props = {resModel: String, resId: Number};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({summary: false});
        onWillStart(() => this.load(this.props));
        onWillUpdateProps((nextProps) => this.load(nextProps));
    }

    async load(props) {
        try {
            this.state.summary = await this.orm.call(
                "sign.oca.request",
                "get_business_record_summary",
                [props.resModel, props.resId]
            );
        } catch {
            this.state.summary = false;
        }
    }

    openRequest() {
        if (!this.state.summary) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: this.state.summary.record_model,
            res_id: this.state.summary.record_id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

FormController.components = {...FormController.components, UslSignBusinessSummary};
