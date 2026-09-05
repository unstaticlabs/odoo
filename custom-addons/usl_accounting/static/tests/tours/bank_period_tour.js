import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_bank_period_correction", {
    steps: () => [
        ...(window.innerWidth < 768
            ? [{ trigger: ".o_statusbar_buttons button[title='More']", run: "click" }]
            : []),
        { trigger: "button[name='action_correct_period']", run: "click" },
        { trigger: ".o_dialog [data-field='period_start']", run: "click" },
        { trigger: ".o_dialog .o_field_widget[name='period_start'] input", run: "edit 2026-08-01" },
        { trigger: ".o_dialog [data-field='period_end']", run: "click" },
        { trigger: ".o_dialog .o_field_widget[name='period_end'] input", run: "edit 2026-08-31" },
        { trigger: ".o_dialog .o_field_widget[name='reason'] textarea", run: "edit Verified official bank statement" },
        { trigger: ".o_dialog button[name='action_apply']", run: "click" },
        { trigger: "body:not(:has(.o_dialog)) .o_form_view .o_field_widget[name='period_start']" },
    ],
});
