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
        this.state = useState({loading: true, summary: false, error: false});
        onWillStart(() => this.load(this.props));
        onWillUpdateProps((nextProps) => this.load(nextProps));
    }

    async load(props) {
        this.state.loading = true;
        this.state.error = false;
        try {
            this.state.summary = await this.orm.call(
                "sign.oca.request",
                "get_business_record_summary",
                [props.resModel, props.resId]
            );
        } catch {
            this.state.summary = false;
            this.state.error = true;
        } finally {
            this.state.loading = false;
        }
    }

    openRequest() {
        if (!this.state.summary) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sign.oca.request",
            res_id: this.state.summary.request_id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

FormController.components = {...FormController.components, UslSignBusinessSummary};
