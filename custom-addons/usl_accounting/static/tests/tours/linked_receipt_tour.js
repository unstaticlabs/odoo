import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_linked_receipt_historical_scan", {
    steps: () => [
        {
            content: "Open the expense cog menu",
            trigger: ".o_form_view .o_cp_action_menus .o-dropdown",
            run: "click",
        },
        {
            content: "Scan the historical email as the expense owner",
            trigger: ".o-dropdown--menu .dropdown-item:contains('Scan existing emails for receipts')",
            run: "click",
            expectUnloadPage: true,
        },
        {
            content: "The discovered receipt is already picked and one click away",
            trigger: ".o_form_view button[name='action_accept_linked_receipt']",
        },
    ],
});

registry.category("web_tour.tours").add("usl_linked_receipt_teach_and_recover", {
    steps: () => [
        {
            content: "Odoo names the link it picked instead of asking for a choice",
            trigger:
                ".o_form_view .alert-info:contains('Odoo picked Download PDF receipt on receipts.example.com')",
        },
        {
            content: "The other links of the email stay one click away",
            trigger:
                ".o_form_view .alert-info button[name='action_review_linked_receipt']:contains('Choose another link')",
            run: "click",
        },
        {
            content: "The dialog explains instance-wide learning without exposing the signed URL",
            trigger:
                ".o_dialog:contains('Odoo will remember the sender, host, and link pattern across this instance') tr:has(td[name='hostname']:contains('receipts.example.com')) button[name='action_choose']",
        },
        {
            content: "Close the alternatives dialog without changing the choice",
            trigger: ".o_dialog footer button:contains('Cancel')",
            run: "click",
        },
        {
            content: "Accept the picked link and start the safe download",
            trigger:
                ".o_form_view .alert-info button[name='action_accept_linked_receipt']:contains('Download this receipt')",
            run: "click",
            expectUnloadPage: true,
        },
        {
            content: "A disabled environment leaves a recoverable manual-attention state",
            trigger:
                ".o_form_view .alert-warning:contains('disabled in this environment') button[name='action_retry_linked_receipt']",
            run: "click",
            expectUnloadPage: true,
        },
        {
            content: "The employee can correct the lesson after a failed retry",
            trigger:
                ".o_form_view .alert-warning button[name='action_review_linked_receipt']:contains('Teach another link')",
            run: "click",
        },
        {
            content: "The source link can still be recovered without displaying its signed value",
            trigger:
                ".o_dialog:contains('Signed URLs stay only in the source email') button[name='action_choose']",
        },
        {
            content: "Close the correction dialog",
            trigger: ".o_dialog footer button:contains('Cancel')",
            run: "click",
        },
    ],
});

registry.category("web_tour.tours").add("usl_linked_receipt_authentication_recovery", {
    steps: () => [
        {
            content: "Authentication recovery explains the safe manual handoff",
            trigger:
                ".o_form_view .alert-warning:contains('Your credentials stay with the provider')",
        },
        {
            content: "Opening the receipt website is the primary action",
            trigger:
                ".o_form_view .alert-warning button.btn-primary[name='action_open_linked_receipt_website']:contains('Open receipt website')",
        },
        {
            content: "The employee can immediately attach the downloaded PDF",
            trigger:
                ".o_form_view .alert-warning button:has(.o_attach_document:contains('Attach downloaded receipt'))",
        },
        {
            content: "Retry, correction, and ignore remain available",
            trigger:
                ".o_form_view .alert-warning button[name='action_retry_linked_receipt']:contains('Retry')",
        },
    ],
});
