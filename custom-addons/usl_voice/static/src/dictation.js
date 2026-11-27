import { Component, markRaw, onWillUnmount } from "@odoo/owl";
import { useExternalListener, useRef, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { VoiceCapture } from "./capture";
import { captionBox, captureTarget, caretRect, dictationTarget, typeHost, wantsMicrophone } from "./dictation_target";
import { requestDictation } from "./dictation_request";
import { VoiceMeter } from "./meter";

/**
 * Odoo closes a quick-create field, a list row or a popover on any click it sees outside
 * it, and each component decides that in its own way. The controls act on the field that
 * keeps the focus, so their pointer events are handled here, first, and reach nobody else.
 * This module is loaded before any component is mounted, so these listeners run first.
 */
const ACTIONS = ["record", "stop", "cancel", "retry", "copyText"];
let mounted = null;
for (const type of ["pointerdown", "mousedown", "touchstart", "pointerup", "mouseup", "touchend", "click"]) {
    window.addEventListener(type, (ev) => {
        if (!mounted || !(ev.target instanceof Element) || !ev.target.closest(".o-usl-dictation")) {
            return;
        }
        ev.stopImmediatePropagation();
        if (type === "mousedown" && ev.target.closest(".o-usl-dictation-controls")) {
            ev.preventDefault(); // the field keeps the focus and its selection
        }
        const action = type === "click" && ev.target.closest("[data-usl-action]")?.dataset.uslAction;
        if (type === "click" && mounted.target?.isConnected &&
                document.activeElement?.closest(".o-usl-dictation-controls")) {
            mounted.target.focus(); // a touch moves the focus to the button: give it back
        }
        if (ACTIONS.includes(action)) {
            mounted[action]();
        }
    }, true);
}

export class FieldDictation extends Component {
    static template = "usl_voice.FieldDictation";
    static props = {};
    static components = { VoiceMeter };
    setup() {
        this.notify = useService("notification");
        mounted = this;
        this.root = useRef("controls");
        this.caption = useRef("caption");
        this.state = useState({ mode: "idle", visible: false, typing: false, above: true, x: 0, y: 0, preview: "", caption: "", clipped: false, recovered: "" });
        useExternalListener(document, "focusin", (ev) => {
            if (ev.target.closest?.(".o-usl-dictation")) {
                return;
            }
            const next = dictationTarget(ev.target);
            if (next !== this.target && this.state.mode !== "idle") {
                this.destination = null;
            }
            this.setTarget(next);
        });
        useExternalListener(document, "input", (ev) => {
            if (this.state.mode === "idle" && !this.target?.isConnected) {
                this.target = dictationTarget(ev.target);
            }
            if (!this.target?.contains(ev.target)) {
                return;
            }
            if (this.state.mode !== "idle") {
                this.destination = null;
                this.position();
                return;
            }
            // Step aside while the writer types, and come back at the insertion point after a pause.
            this.state.typing = true;
            clearTimeout(this.typingTimer);
            this.typingTimer = setTimeout(() => {
                this.state.typing = false;
                this.position();
            }, 900);
        });
        useExternalListener(document, "selectionchange", () => this.position());
        useExternalListener(document, "focusout", (event) => {
            if (!event.relatedTarget && this.state.mode === "idle") {
                this.setTarget(null);
            }
        });
        // Escape leaves the dictation, not the form or dialog behind it.
        useExternalListener(document, "keydown", (ev) => {
            if (ev.key === "Escape" && (this.state.mode !== "idle" || this.state.recovered)) {
                ev.preventDefault();
                ev.stopPropagation();
                this.cancel();
            } else if (ev.altKey && ev.shiftKey && ev.code === "KeyD" && !ev.repeat) {
                // Hands stay on the keyboard: the same keys start a dictation and end it.
                if (this.state.mode === "recording") {
                    ev.preventDefault();
                    this.stop();
                } else if (this.state.mode === "idle" && this.target) {
                    ev.preventDefault();
                    this.record();
                }
            }
        }, true);
        useExternalListener(window, "resize", () => this.position());
        useExternalListener(document, "scroll", () => this.position(), true);
        useExternalListener(window, "pagehide", () => this.cancel());
        onWillUnmount(() => {
            mounted = mounted === this ? null : mounted;
            clearTimeout(this.typingTimer);
            this.cancel();
        });
    }
    setTarget(element) {
        this.target = element;
        this.state.typing = false;
        this.position();
    }
    /**
     * Sit just above the insertion point, where the eyes already are, without taking any
     * room from the field: nothing in the form moves when the controls appear or grow.
     */
    position() {
        if (this.state.mode === "idle" && !this.target?.isConnected) {
            // Odoo may redraw a field while it keeps the focus: follow the element that has it now.
            const active = document.activeElement;
            this.target = active?.closest?.(".o-usl-dictation") ? null : dictationTarget(active);
        }
        const target = this.target?.isConnected ? this.target : null;
        const caret = target ? caretRect(target) : this.anchor;
        const box = target?.getBoundingClientRect();
        const shown = Boolean(caret && box && box.width && box.height && caret.top < innerHeight &&
            caret.top + caret.height > 0 && navigator.mediaDevices?.getUserMedia);
        if (shown) {
            this.anchor = caret;
            const size = this.root.el?.offsetHeight || 28;
            const width = this.root.el?.offsetWidth || size;
            // Grow upwards from the insertion point; go below it only when there is no room above.
            this.state.above = caret.top - size - 4 >= 4;
            this.state.x = Math.max(4, Math.min(innerWidth - width - 4, caret.left + 6));
            this.state.y = this.state.above ? caret.top - 4 : Math.min(innerHeight - size - 4, caret.top + caret.height + 4);
        }
        if (this.state.mode !== "idle") {
            // The words heard so far are written in the field itself, where they will be inserted:
            // not in another field the writer moved to, nor once this one has scrolled away.
            // They stay where the text will go, wherever the writer then puts the insertion point.
            // A field that changed width has wrapped its text again, so the saved place is gone.
            const pin = this.pin;
            const line = { height: pin.height, left: box?.left + pin.x - (target?.scrollLeft - pin.scrollLeft),
                top: box?.top + pin.y - (target?.scrollTop - pin.scrollTop) };
            const at = shown && this.destination && target === this.origin && Math.abs(box.width - pin.width) < 1 && line.top + line.height / 2 > box.top &&
                line.top + line.height / 2 < box.bottom && line.left >= box.left && line.left <= box.right && captionBox(target, line, pin.host);
            this.state.caption = at ? `left:${at.left}px;top:${at.top}px;max-width:${at.width}px;height:${at.height}px;` +
                `font:${at.font};color:${at.color};background-color:${at.background}` : "";
        }
        const words = this.caption.el;
        this.state.clipped = Boolean(words && words.firstElementChild.offsetWidth > words.clientWidth);
        this.state.visible = shown;
        this.state.wanted = Boolean(target && wantsMicrophone(target));
    }
    setMode(mode) {
        this.state.mode = mode;
        this.settle();
    }
    /** Place everything now, and again once redrawn: the controls and the words change width. */
    settle() {
        this.position();
        requestAnimationFrame(() => requestAnimationFrame(() => this.position()));
    }
    cancel() {
        this.pendingAudio = null;
        this.capture?.discard();
        this.capture = null;
        this.request?.abort();
        this.request = null;
        this.destination = null;
        this.listening?.abort();
        this.state.preview = "";
        this.setMode("idle");
        this.state.recovered = "";
    }
    async record() {
        if (this.state.mode !== "idle" || !this.target?.isConnected) {
            return;
        }
        this.origin = this.target;
        const caret = caretRect(this.target);
        const box = this.target.getBoundingClientRect();
        this.pin = { x: caret.left - box.left, y: caret.top - box.top, width: box.width, height: caret.height, host: typeHost(this.target),
            scrollLeft: this.target.scrollLeft, scrollTop: this.target.scrollTop };
        this.destination = captureTarget(this.target);
        const capture = markRaw(new VoiceCapture());
        this.capture = capture;
        this.setMode("starting");
        try {
            await capture.start(() => this.stop());
            if (this.capture === capture) {
                this.setMode("recording");
                this.listen(capture);
            }
        } catch {
            if (this.capture === capture) {
                this.cancel();
                this.notify.add(_t("Check microphone access and try again."), { type: "warning" });
            }
        }
    }
    async transcribe(file, signal, preview = false) {
        return requestDictation(file, signal, preview);
    }
    /**
     * Write the last words heard in the field while the writer is still speaking: without it
     * they talk into silence until the end. One request at a time, on the last seconds only.
     */
    async listen(capture) {
        const listening = new AbortController();
        this.listening = listening;
        const live = () => this.capture === capture && this.state.mode === "recording" && !listening.signal.aborted;
        while (live()) {
            await new Promise((resolve) => setTimeout(resolve, 2000));
            const file = live() && capture.tail();
            if (!file) {
                continue;
            }
            try {
                const text = await this.transcribe(file, listening.signal, true);
                if (live() && text) {
                    this.state.preview = text;
                    this.settle();
                }
            } catch {
                // The preview is a comfort: the dictation itself is unaffected.
            }
        }
    }
    async stop() {
        if (this.state.mode !== "recording") {
            return;
        }
        this.listening?.abort();
        const file = this.capture.finish();
        this.capture = null;
        if (!file) {
            this.cancel();
            return;
        }
        this.pendingAudio = file;
        await this.retry();
    }
    async retry() {
        if (!this.pendingAudio || this.request) {
            return;
        }
        if (this.state.mode === "retry" && this.destination && this.target?.isConnected) {
            // The retry button disappears while processing. Return focus so
            // the result can be inserted at the selection captured earlier.
            this.target.focus({ preventScroll: true });
        }
        this.setMode("processing");
        const request = new AbortController();
        this.request = request;
        try {
            const text = await this.transcribe(this.pendingAudio, request.signal);
            if (this.request !== request) {
                return;
            }
            this.pendingAudio = null;
            if (text && !this.destination?.insert(text)) {
                this.state.recovered = text;
            } else if (!text) {
                this.notify.add(_t("No speech detected. Try recording again."), { type: "info" });
            }
        } catch {
            // Keep the file in memory for Retry. Cancel, pagehide and unmount discard it.
        } finally {
            if (this.request === request) {
                this.request = null;
                this.state.preview = "";
                this.setMode(this.pendingAudio ? "retry" : "idle");
            }
        }
    }
    async copyText() {
        try {
            await navigator.clipboard.writeText(this.state.recovered);
            this.cancel();
            this.notify.add(_t("Transcript copied."), { type: "success" });
        } catch {
            this.notify.add(_t("Select the transcript and copy it."), { type: "info" });
        }
    }
}
registry.category("main_components").add("usl_voice.FieldDictation", { Component: FieldDictation });
