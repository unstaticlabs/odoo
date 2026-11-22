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
