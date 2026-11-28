import {
    AnalyticDistribution,
    analyticDistribution,
} from "@analytic/components/analytic_distribution/analytic_distribution";
import { Domain } from "@web/core/domain";
import { registry } from "@web/core/registry";
import { useTransferLookup } from "./bill_transfer_lookup";

function transferContext(record) {
    return { allowed_company_ids: record.data.scope_company_ids };
}

class BillTransferAnalyticDistribution extends AnalyticDistribution {
    static template = "usl_accounting.BillTransferAnalyticDistribution";

    setup() {
        super.setup();
        useTransferLookup(this.env.services);
    }

    get transferContext() {
        return transferContext(this.props.record);
    }

    fetchPlansArgs(props) {
        return { ...super.fetchPlansArgs(props), context: transferContext(props.record) };
    }

    async fetchAnalyticAccounts(domain) {
        const records = await this.batchedOrm.read(
            "account.analytic.account", domain[0][2],
            ["display_name", "root_plan_id", "color"], { context: this.transferContext }
        );
        return Object.fromEntries(records.map((record) => [record.id, record]));
    }

    recordProps(line) {
        const props = super.recordProps(line);
        props.context = this.transferContext;
        // The source label stays readable. Only destination-compatible accounts can replace it.
        const targetId = this.props.record.data.company_id?.id;
        const targetDomain = targetId
            ? ["|", ["company_id", "=", false], ["company_id", "parent_of", targetId]]
            : Domain.FALSE;
        for (const field of Object.values(props.fields)) {
            if (field.relation === "account.analytic.account") {
                field.context = this.transferContext;
                field.domain = Domain.and([field.domain, targetDomain]).toList();
            }
        }
        return props;
    }
}

registry.category("fields").add("usl_bill_transfer_analytic", {
    ...analyticDistribution,
    component: BillTransferAnalyticDistribution,
    additionalClasses: ["o_field_analytic_distribution"],
});
