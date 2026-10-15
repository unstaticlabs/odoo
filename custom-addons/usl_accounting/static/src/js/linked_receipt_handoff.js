import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

export function openLinkedReceipt(env, action) {
    const { retrieval_id: retrievalId, generation } = action.params || {};
    if (
        !Number.isInteger(retrievalId) || retrievalId <= 0 ||
        !Number.isInteger(generation) || generation <= 0 || !odoo.csrf_token
    ) {
        env.services.notification.add(_t("Reload Odoo, then open the receipt website again."), {
            type: "warning", sticky: true,
        });
        return;
    }
    const popup = browser.open("about:blank", "_blank");
    if (!popup || popup.closed || typeof popup.closed === "undefined") {
        env.services.notification.add(_t("Allow popups for Odoo, then open the receipt website again."), {
            type: "warning", sticky: true,
        });
        return;
    }
    let form;
    try {
        popup.opener = null;
        popup.document.title = _t("Opening receipt website");
        popup.document.body.textContent = _t("Opening receipt website");
        form = popup.document.createElement("form");
        form.method = "POST";
        form.action = new URL(
            `/usl/expenses/linked-receipt/${retrievalId}/continue`, browser.location.origin,
        ).href;
        form.target = "_self";
        form.hidden = true;
        for (const [name, value] of Object.entries({ csrf_token: odoo.csrf_token, generation })) {
            const input = popup.document.createElement("input");
            input.type = "hidden";
            input.name = name;
            input.value = String(value);
            form.append(input);
        }
        popup.document.body.append(form);
        form.submit();
    } catch {
        popup.close();
        env.services.notification.add(_t("The receipt website could not be opened. Try again from the expense."), {
            type: "warning", sticky: true,
        });
    } finally {
        form?.remove();
    }
}

registry.category("actions").add("usl_linked_receipt_handoff", openLinkedReceipt);
