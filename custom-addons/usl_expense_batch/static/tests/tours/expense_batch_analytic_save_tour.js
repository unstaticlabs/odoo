import { click, edit } from "@odoo/hoot-dom";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

const tours = registry.category("web_tour.tours");
const field = ".o_field_widget[name='analytic_distribution']";
const save = ".o_form_view .o_form_button_save";
const revisedAccount = "QA revised allocation";

tours.add("usl_expense_batch_save_analytics", {
    steps: () => [
        {
            content: "Make another edit so Save is already available",
            trigger: ".o_usl_expense_batch_title [name='name'] input",
            async run() {
                await click(this.anchor);
                await edit("QA sample batch", { confirm: "tab" });
            },
        },
        {
            content: "Open the saved batch allocation",
            trigger: `.o_form_view ${field} .o_input_dropdown`,
            run: "click",
        },
        {
            content: "Find another account in the same plan",
            trigger: ".analytic_distribution_popup tr[name='line_0'] .o_field_many2one input",
            async run() {
                await click(this.anchor);
                await edit(revisedAccount, { confirm: false });
            },
        },
        {
            content: "Select the new account and save without waiting for the account lookup",
            trigger: `.analytic_distribution_popup .o_input_dropdown a:contains('${revisedAccount}')`,
            async run() {
                await click(this.anchor);
                if (!user.lang.startsWith("fr")) {
                    await click(".o_usl_expense_batch_title label");
                }
                await click(save);
            },
        },
        {
            content: "The batch form finishes saving",
            trigger: `.o_form_view:not(:has(.o_form_status_indicator_buttons:not(.invisible))) ${field} .o_tag_badge_text`,
        },
    ],
});

tours.add("usl_expense_batch_reopen_and_apply_analytics", {
    steps: () => [
        {
            content: "The new account persists after reopening the batch form",
            trigger: `.o_form_view ${field} .o_tag_badge_text:contains('${revisedAccount}')`,
        },
        {
            content: "Review the impact before changing expenses",
            trigger: "button[name='action_open_context_wizard']",
            run: "click",
        },
        {
            content: "Only the eligible draft is selected for application",
            trigger: ".o_dialog .o_form_view:has([name='changed_count']:contains('1')):has([name='exception_count']:contains('1')):has([name='skipped_count']:contains('1'))",
        },
        {
            content: "Confirm the update to the eligible draft",
            trigger: ".o_dialog footer button[name='action_apply']",
            run: "click",
        },
        {
            content: "The confirmation closes and the batch remains open",
            trigger: ".o_web_client:not(:has(.modal.show)) .o_usl_batch_summary",
        },
    ],
});
