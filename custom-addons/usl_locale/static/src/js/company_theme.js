/** @odoo-module **/

import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { SwitchCompanyItem } from "@web/webclient/switch_company_menu/switch_company_item";
import { SwitchCompanyMenu } from "@web/webclient/switch_company_menu/switch_company_menu";

const FALLBACK_COLOR = "#4E5AA8";
const MULTI_COMPANY_COLOR = "#714B67";
const HEX_COLOR_RE = /^#[0-9A-Fa-f]{6}$/;

export function normalizeCompanyColor(color) {
    return HEX_COLOR_RE.test(color || "") ? color.toUpperCase() : FALLBACK_COLOR;
}

function linearChannel(channel) {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

export function companyThemeForeground(color) {
    const normalized = normalizeCompanyColor(color);
    const red = parseInt(normalized.slice(1, 3), 16);
    const green = parseInt(normalized.slice(3, 5), 16);
    const blue = parseInt(normalized.slice(5, 7), 16);
    const luminance =
        0.2126 * linearChannel(red) +
        0.7152 * linearChannel(green) +
        0.0722 * linearChannel(blue);
    const whiteContrast = 1.05 / (luminance + 0.05);
    const darkContrast = (luminance + 0.05) / 0.05;
    return whiteContrast >= darkContrast ? "#FFFFFF" : "#111827";
}

export function companyColorIndicatorStyle(company) {
    return `--usl-company-indicator:${normalizeCompanyColor(company?.usl_ui_theme_color)}`;
}

export function clearCompanyTheme(root) {
    delete root.dataset.uslCompanyTheme;
    root.style.removeProperty("--usl-company-color");
    root.style.removeProperty("--usl-company-foreground");
}

export function applyCompanyTheme(root, company, activeCompanyCount = 1) {
    if (activeCompanyCount > 1) {
        const foreground = companyThemeForeground(MULTI_COMPANY_COLOR);
        root.dataset.uslCompanyTheme = "neutral";
        root.style.setProperty("--usl-company-color", MULTI_COMPANY_COLOR);
        root.style.setProperty("--usl-company-foreground", foreground);
        return { color: MULTI_COMPANY_COLOR, foreground };
    }
    const color = normalizeCompanyColor(company?.usl_ui_theme_color);
    const foreground = companyThemeForeground(color);
    root.dataset.uslCompanyTheme = "active";
    root.style.setProperty("--usl-company-color", color);
    root.style.setProperty("--usl-company-foreground", foreground);
    return { color, foreground };
}

export function companyScopeTitle(activeCompany, activeCompanyCount) {
    if (activeCompanyCount > 1) {
        return _t(
            "%(company)s is primary for new records. %(count)s companies are selected for viewing.",
            { company: activeCompany.name, count: activeCompanyCount }
        );
    }
    return _t("%(company)s is the active company.", { company: activeCompany.name });
}

export const companyThemeService = {
    start() {
        applyCompanyTheme(
            document.documentElement,
            user.activeCompany,
            user.activeCompanies.length
        );
    },
};

registry.category("services").add("usl_company_theme", companyThemeService);

patch(SwitchCompanyMenu.prototype, {
    get uslAdditionalCompanyCount() {
        return Math.max(user.activeCompanies.length - 1, 0);
    },

    get uslCompanyScopeTitle() {
        return companyScopeTitle(user.activeCompany, user.activeCompanies.length);
    },

    get uslActiveCompanyColorStyle() {
        return companyColorIndicatorStyle(user.activeCompany);
    },
});

patch(SwitchCompanyItem.prototype, {
    get uslCompanyColorStyle() {
        return companyColorIndicatorStyle(this.props.company);
    },
});
