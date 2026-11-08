/** @odoo-module */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { session } from "@web/session";

const DOCS_ROUTE = "/usl/user-docs";

/**
 * Open the guide on the page about the screen the person is looking at.
 *
 * Odoo's own Help entry points at ``session.support_url``, which this
 * distribution sets to the guide (``usl_locale``). This item replaces it so
 * the link also carries the current action and model; the guide lands on the
 * page that declares them in its front matter, or on its home page with a
 * note when no page covers the screen yet. A ``support_url`` outside the
 * guide is kept as is, so the entry never points away from what the
 * distribution configured.
 */
function helpUrl(env) {
    const base = session.support_url || DOCS_ROUTE;
    if (!base.startsWith(DOCS_ROUTE)) {
        return base;
    }
    const controller = env.services.action?.currentController;
    const params = new URLSearchParams();
    const action = controller?.action;
    if (action) {
        const key = action.path || action.xml_id || (action.id ? String(action.id) : "");
        if (key) {
            params.set("action", key);
        }
        if (action.xml_id && action.xml_id !== key) {
            params.set("xmlid", action.xml_id);
        }
        const model = controller.props?.resModel || action.res_model;
        if (model) {
            params.set("model", model);
        }
    }
    const query = params.toString();
    return query ? `${DOCS_ROUTE}?${query}` : DOCS_ROUTE;
}

export { helpUrl };

function guideItem(env) {
    return {
        type: "item",
        id: "support",
        description: _t("Help"),
        href: helpUrl(env),
        callback: () => {
            browser.open(helpUrl(env), "_blank");
        },
        sequence: 20,
    };
}

registry.category("user_menuitems").add("support", guideItem, { force: true });
