import { Component, markRaw, onWillUnmount } from "@odoo/owl";
import { useExternalListener, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { VoiceCapture } from "./capture";
import { captureTarget, dictationTarget } from "./dictation_target";
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
        this.state = useState({ mode: "idle", visible: false, x: 0, y: 0, recovered: "" });
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
            if (this.state.mode !== "idle" && this.target?.contains(ev.target)) {
                this.destination = null;
            }
        });
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
            }
        }, true);
        useExternalListener(window, "resize", () => this.position());
        useExternalListener(document, "scroll", () => this.position(), true);
        useExternalListener(window, "pagehide", () => this.cancel());
        onWillUnmount(() => {
            mounted = mounted === this ? null : mounted;
            this.cancel();
            this.target?.classList.remove("o-usl-dictation-target", "o-usl-dictation-busy");
        });
    }
    setTarget(element) {
        this.target?.classList.remove("o-usl-dictation-target", "o-usl-dictation-busy");
        this.target = element;
        this.target?.classList.add("o-usl-dictation-target");
        this.position();
    }
    position() {
        const box = this.target?.isConnected && this.target.getBoundingClientRect();
        if (box && box.width && box.height) {
            this.state.x = Math.max(2, Math.min(innerWidth - 4, box.right - 3));
            this.state.y = Math.max(2, Math.min(innerHeight - 36, box.top + (Math.min(box.height, 40) - 32) / 2));
        }
        this.state.visible = Boolean(box && box.width && box.height &&
            box.top >= 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth &&
            navigator.mediaDevices?.getUserMedia);
    }
    /** Keep the field's text clear of the wider controls while a dictation runs. */
    setMode(mode) {
        this.state.mode = mode;
        this.target?.classList.toggle("o-usl-dictation-busy", mode !== "idle");
        this.position();
        // Reserve exactly the room the controls take once they are drawn, whatever the language.
        const target = this.target;
        // Two frames: the controls are redrawn on the first one.
        requestAnimationFrame(() => requestAnimationFrame(() => {
            const width = document.querySelector(".o-usl-dictation-pill")?.offsetWidth;
            if (width && this.state.mode !== "idle") {
                target?.style.setProperty("--usl-dictation-room", `${width + 12}px`);
            } else {
                target?.style.removeProperty("--usl-dictation-room");
            }
        }));
    }
    cancel() {
        this.capture?.discard();
        this.capture = null;
        this.request?.abort();
        this.request = null;
        this.destination = null;
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
            }
        } catch {
            if (this.capture === capture) {
                this.cancel();
                this.notify.add(_t("Check microphone access and try again."), { type: "warning" });
            }
        }
    }
    async stop() {
        if (this.state.mode !== "recording") {
            return;
        }
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
            const form = new FormData();
            form.append("file", file);
            form.append("csrf_token", odoo.csrf_token);
            const response = await fetch("/usl/voice/dictate", { method: "POST", body: form, signal: request.signal });
            if (!response.ok) {
                throw Error();
            }
            const { text } = await response.json();
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
