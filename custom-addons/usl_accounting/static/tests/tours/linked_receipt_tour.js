import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("usl_linked_receipt_teach_and_recover", {
    steps: () => [
        {
            content: "The employee is asked to teach the first receipt link",
            trigger:
                ".o_form_view .alert-info button[name='action_review_linked_receipt']:contains('Choose receipt link')",
            run: "click",
        },
        {
            content: "The dialog explains instance-wide learning without exposing the signed URL",
            trigger:
                ".o_dialog:contains('Odoo will remember the sender, host, and link pattern across this instance') tr:has(td[name='hostname']:contains('receipts.example.com')) button[name='action_choose']",
        },
        {
            content: "Teach the selected candidate and start the safe download",
            trigger:
                ".o_dialog tr:has(td[name='hostname']:contains('receipts.example.com')) button[name='action_choose']:contains('Use this link')",
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
