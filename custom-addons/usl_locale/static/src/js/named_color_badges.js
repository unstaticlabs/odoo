/** @odoo-module **/

import { BadgeTag } from "@web/core/tags_list/badge_tag";
import { patch } from "@web/core/utils/patch";

const INVALID_COLOR_KEYWORDS = new Set([
    "currentcolor",
    "inherit",
    "initial",
    "revert",
    "revert-layer",
    "transparent",
    "unset",
]);
const RGB_RE =
    /^rgba?\(\s*(\d+(?:\.\d+)?)[,\s]+(\d+(?:\.\d+)?)[,\s]+(\d+(?:\.\d+)?)(?:\s*[,/]\s*(\d+(?:\.\d+)?))?\s*\)$/i;
const colorCache = new Map();

function linearChannel(channel) {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function readableForeground(red, green, blue) {
    const luminance =
        0.2126 * linearChannel(red) +
        0.7152 * linearChannel(green) +
        0.0722 * linearChannel(blue);
    const whiteContrast = 1.05 / (luminance + 0.05);
    const darkContrast = (luminance + 0.05) / 0.05;
    return whiteContrast >= darkContrast ? "#FFFFFF" : "#111827";
}

/**
 * Return a safe inline style when a badge's complete label is an opaque CSS color.
 * The browser resolves the value first, so user text is never copied into CSS.
 */
export function namedColorBadgeStyle(text, root = document.documentElement) {
    const candidate = (text || "").trim();
    const key = candidate.toLowerCase();
    if (
        !candidate ||
        INVALID_COLOR_KEYWORDS.has(key) ||
        !globalThis.CSS?.supports("color", candidate)
    ) {
        return "";
    }
    if (colorCache.has(key)) {
        return colorCache.get(key);
    }

    const probe = document.createElement("span");
    probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none";
    probe.style.color = candidate;
    root.appendChild(probe);
    const resolved = getComputedStyle(probe).color;
    probe.remove();

    const match = RGB_RE.exec(resolved);
    const alpha = match?.[4] === undefined ? 1 : Number(match[4]);
    if (!match || alpha !== 1) {
        colorCache.set(key, "");
        return "";
    }
    const [red, green, blue] = match.slice(1, 4).map(Number);
    const background = `rgb(${red}, ${green}, ${blue})`;
    const foreground = readableForeground(red, green, blue);
    const style = [
        `background-color: ${background} !important`,
        `color: ${foreground} !important`,
        "box-shadow: inset 0 0 0 1px color-mix(in srgb, currentColor 18%, transparent)",
    ].join("; ");
    colorCache.set(key, style);
    return style;
}

patch(BadgeTag.prototype, {
    get uslNamedColorStyle() {
        return namedColorBadgeStyle(this.props.text);
    },
});
