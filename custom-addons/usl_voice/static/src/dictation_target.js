/** Locate the editable host itself, including descendants of rich text editors. */
export function dictationTarget(element) {
    if (!(element instanceof Element)) {
        return null;
    }
    if (element.matches("input:not(:disabled):not([readonly]), textarea:not(:disabled):not([readonly])")) {
        return element.tagName === "TEXTAREA" || ["text", "search", "url", "email", "tel"].includes(element.type)
            ? element : null;
    }
    return element.isContentEditable ? element.closest('[contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]') : null;
}

export function captureTarget(element) {
    const rich = element.isContentEditable;
    const original = rich ? element.innerHTML : element.value;
    const start = element.selectionStart;
    const end = element.selectionEnd;
    const selection = element.ownerDocument.getSelection();
    const range = rich && selection?.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
    const location = window.location.href;
    return {
        insert(text) {
            if (!text || !element.isConnected || dictationTarget(element) !== element ||
                location !== window.location.href || (rich ? element.innerHTML : element.value) !== original) {
                return false;
            }
            const active = element.ownerDocument.activeElement;
            if (!element.contains(active) && !active?.closest(".o-usl-dictation")) {
                return false;
            }
            if (rich && (!range || !element.contains(range.commonAncestorContainer))) {
                return false;
            }
            element.focus();
            if (rich) {
                selection.removeAllRanges();
                selection.addRange(range);
            } else if (start !== null && end !== null) {
                element.setSelectionRange(start, end);
            }
            // Use the browser editing command so the editor's input pipeline and Undo run normally.
            return element.ownerDocument.execCommand("insertText", false, text);
        },
    };
}

const MIRRORED = [
    "boxSizing", "width", "height", "borderTopWidth", "borderRightWidth", "borderBottomWidth",
    "borderLeftWidth", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "fontStyle",
    "fontVariant", "fontWeight", "fontStretch", "fontSize", "lineHeight", "fontFamily", "textAlign",
    "textTransform", "textIndent", "letterSpacing", "wordSpacing", "tabSize", "direction",
];

/**
 * Where the insertion point is on screen, as { left, top, height } in viewport pixels.
 * Inputs and textareas do not expose it, so their text is laid out again in a hidden copy.
 */
export function caretRect(element) {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2 || 16;
    let left = box.left + parseFloat(style.paddingLeft || 0);
    let top = box.top + parseFloat(style.paddingTop || 0);
    let height = line;
    if (element.isContentEditable) {
        const selection = element.ownerDocument.getSelection();
        const range = selection?.rangeCount && element.contains(selection.focusNode)
            ? selection.getRangeAt(0).cloneRange() : null;
        if (range) {
            range.collapse(false);
            const node = range.startContainer;
            const rect = range.getClientRects()[0] ||
                (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement)?.getBoundingClientRect();
            if (rect && (rect.width || rect.height)) {
                ({ left, top } = rect);
                height = rect.height && rect.height < line * 2 ? rect.height : line;
            }
        }
    } else {
        const multiline = element.tagName === "TEXTAREA";
        const at = element.selectionEnd ?? element.value.length;
        const mirror = element.ownerDocument.createElement("div");
        for (const property of MIRRORED) {
            mirror.style[property] = style[property];
        }
        Object.assign(mirror.style, {
            position: "fixed", left: `${box.left}px`, top: `${box.top}px`, visibility: "hidden",
            overflow: "hidden", pointerEvents: "none", borderStyle: "solid",
            whiteSpace: multiline ? "pre-wrap" : "pre", overflowWrap: multiline ? "break-word" : "normal",
        });
        mirror.textContent = element.value.slice(0, at);
        const marker = element.ownerDocument.createElement("span");
        marker.textContent = "\u200b";
        mirror.append(marker, element.value.slice(at));
        element.ownerDocument.body.append(mirror);
        mirror.scrollTop = element.scrollTop;
        mirror.scrollLeft = element.scrollLeft;
        const rect = marker.getBoundingClientRect();
        mirror.remove();
        left = rect.left;
        // A one-line input centres its text; the copy lays it out from the top.
        top = multiline ? rect.top : box.top + (box.height - line) / 2;
    }
    return {
        left: Math.min(Math.max(left, box.left), box.right),
        top: Math.min(Math.max(top, box.top), Math.max(box.top, box.bottom - height)),
        height,
    };
}

/** The element whose type the text at the insertion point takes: in rich text, maybe a heading. */
export function typeHost(element) {
    const focus = element.isContentEditable && element.ownerDocument.getSelection()?.focusNode;
    return focus && element.contains(focus) ? (focus.nodeType === Node.ELEMENT_NODE ? focus : focus.parentElement) : element;
}

/**
 * Where the words heard so far are written while the writer speaks: on the line of the
 * insertion point, from it to the end of the field, in the field's own type. With too little
 * room left on that line, the whole line is used. Viewport pixels, like caretRect.
 */
export function captionBox(element, caret, host = typeHost(element)) {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const start = box.left + parseFloat(style.borderLeftWidth || 0) + parseFloat(style.paddingLeft || 0);
    const end = box.right - parseFloat(style.borderRightWidth || 0) - parseFloat(style.paddingRight || 0);
    const left = end - caret.left >= 120 ? Math.max(caret.left, start) : start;
    // The words sit over the field, so they need its background: often an ancestor's.
    let background = "";
    for (let node = element; node && !background; node = node.parentElement) {
        const color = getComputedStyle(node).backgroundColor;
        background = /^(transparent|rgba\(.*,\s*0\))$/.test(color) ? "" : color;
    }
    const type = getComputedStyle(host.isConnected ? host : element);
    return {
        left, top: caret.top, width: Math.max(0, end - left), height: caret.height,
        font: `${type.fontStyle} ${type.fontWeight} ${type.fontSize} / ${caret.height}px ${type.fontFamily}`,
        color: type.color, background: background || "Canvas",
    };
}

/**
 * Whether the resting microphone is worth showing in this field. Record pickers and number or
 * date fields take a value, not words: there the keyboard shortcut still dictates, but no
 * button comes to sit over them. Search boxes do show it: nothing else says they can be spoken to.
 */
export function wantsMicrophone(element) {
    if (element.isContentEditable || element.tagName === "TEXTAREA") {
        return true;
    }
    return ["text", "search"].includes(element.type) &&
        !element.matches('[inputmode="numeric"], [inputmode="decimal"], .o-autocomplete--input, .o_datetime_input') &&
        !element.closest(".o-autocomplete, .o_field_integer, .o_field_float, .o_field_monetary, .o_field_percentage, .o_field_float_time, .o_field_date, .o_field_datetime, .o_datetime_picker, .dropdown-menu");
}
