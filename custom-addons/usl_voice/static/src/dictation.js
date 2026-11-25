import { Component, markRaw, onWillUnmount } from "@odoo/owl";
import { useExternalListener, useRef, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { VoiceCapture } from "./capture";
import { captureTarget, caretRect, dictationTarget, wantsMicrophone } from "./dictation_target";
import { VoiceMeter } from "./meter";

/**
 * Odoo closes a quick-create field, a list row or a popover on any click it sees outside
 * it, and each component decides that in its own way. The controls act on the field that
 * keeps the focus, so their pointer events are handled here, first, and reach nobody else.
 * This module is loaded before any component is mounted, so these listeners run first.
 */
const ACTIONS = ["record", "stop", "cancel", "copyText"];
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
        this.state = useState({ mode: "idle", visible: false, typing: false, above: true, x: 0, y: 0, preview: "", recovered: "" });
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
        this.state.visible = shown;
        this.state.wanted = Boolean(target && wantsMicrophone(target));
    }
    setMode(mode) {
        this.state.mode = mode;
        this.position();
        // The controls change width with the mode: once redrawn, keep them inside the window.
        requestAnimationFrame(() => requestAnimationFrame(() => this.position()));
    }
    cancel() {
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
    async transcribe(file, signal) {
        const form = new FormData();
        form.append("file", file);
        form.append("csrf_token", odoo.csrf_token);
        const response = await fetch("/usl/voice/dictate", { method: "POST", body: form, signal });
        if (!response.ok) {
            throw Error();
        }
        return (await response.json()).text;
    }
    /**
     * Show the last words heard while the writer is still speaking: without it they talk
     * into silence until the end. One request at a time, on the last seconds only.
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
                const text = await this.transcribe(file, listening.signal);
                if (live() && text) {
                    this.state.preview = text;
                    this.position();
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
        this.setMode("processing");
        const request = new AbortController();
        this.request = request;
        try {
            const text = await this.transcribe(file, request.signal);
            if (this.request !== request) {
                return;
            }
            if (text && !this.destination?.insert(text)) {
                this.state.recovered = text;
            } else if (!text) {
                this.notify.add(_t("No speech detected. Try recording again."), { type: "info" });
            }
        } catch {
            if (!request.signal.aborted) {
                this.notify.add(_t("Transcription is unavailable. Try dictating again."), { type: "warning" });
            }
        } finally {
            if (this.request === request) {
                this.request = null;
                this.state.preview = "";
                this.setMode("idle");
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
