/** @odoo-module **/

export function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
}

export function pointToPlacement(pageRectangle, clientX, clientY, width, height) {
    const positionX = ((clientX - pageRectangle.left) * 100) / pageRectangle.width;
    const positionY = ((clientY - pageRectangle.top) * 100) / pageRectangle.height;
    return {
        position_x: Math.round(clamp(positionX, 0, 100 - width) * 10) / 10,
        position_y: Math.round(clamp(positionY, 0, 100 - height) * 10) / 10,
    };
}

export function roleTint(hexColor, alpha = 0.16) {
    const normalized = hexColor.replace("#", "");
    const red = Number.parseInt(normalized.slice(0, 2), 16);
    const green = Number.parseInt(normalized.slice(2, 4), 16);
    const blue = Number.parseInt(normalized.slice(4, 6), 16);
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

export function contrastForeground(hexColor) {
    const normalized = hexColor.replace("#", "");
    const channels = [0, 2, 4].map((offset) => {
        const value = Number.parseInt(normalized.slice(offset, offset + 2), 16) / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    const luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    return luminance > 0.179 ? "#17202A" : "#FFFFFF";
}

export function editableItemValues(item) {
    return {
        field_id: Number(item.field_id),
        role_id: Number(item.role_id),
        required: Boolean(item.required),
        placeholder: item.placeholder || "",
        page: Number(item.page),
        position_x: Number(item.position_x),
        position_y: Number(item.position_y),
        width: Number(item.width),
        height: Number(item.height),
    };
}

export function operationUuid(cryptoProvider = globalThis.crypto) {
    if (typeof cryptoProvider?.randomUUID === "function") {
        return cryptoProvider.randomUUID();
    }
    if (typeof cryptoProvider?.getRandomValues !== "function") {
        throw new Error("This browser cannot create a secure editor operation identifier.");
    }
    const bytes = cryptoProvider.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
    return [
        hex.slice(0, 4).join(""),
        hex.slice(4, 6).join(""),
        hex.slice(6, 8).join(""),
        hex.slice(8, 10).join(""),
        hex.slice(10).join(""),
    ].join("-");
}
