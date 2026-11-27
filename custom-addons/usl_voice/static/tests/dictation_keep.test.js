import { after, expect, test } from "@odoo/hoot";
import { click, press } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";
import { mountWithCleanup, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { MainComponentsContainer } from "@web/core/main_components_container";
import { user } from "@web/core/user";
import { VoiceCapture } from "@usl_voice/capture";
import { FieldDictation } from "@usl_voice/dictation";
import {
    disownRecording, forgetRecording, keepRecording, keptRecordings, recordingFileName, recordingTime,
} from "@usl_voice/recording_store";

defineMailModels();

/** A voice recording that never was one: the tests need a file, not a microphone. */
const clip = (name = "Voice.mp3") => new File(["synthetic-audio"], name, { type: "audio/mpeg" });

async function mountDictation() {
    let dictation;
    const ids = [];
    patchWithCleanup(FieldDictation.prototype, {
        setup() {
            super.setup();
            dictation = this;
        },
        async hold(file) {
            const held = super.hold(file);
            ids.push(this.recordingId);
            return held;
        },
    });
    await mountWithCleanup(MainComponentsContainer, { noMainContainer: true });
    after(async () => {
        for (const entry of await keptRecordings(user.userId)) {
            await forgetRecording(entry.id);
        }
        for (const id of ids) {
            await forgetRecording(id);
        }
    });
    dictation.ids = ids;
    return dictation;
}

/** Record, stop, and have the speech worker fail as it does when it is busy or asleep. */
async function dictateAndFail(dictation, input) {
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    patchWithCleanup(FieldDictation.prototype, {
        listen() {},
        async transcribe() {
            throw Error("busy");
        },
    });
    input.focus();
    await animationFrame();
    await dictation.record();
    await animationFrame();
    await dictation.stop();
    await animationFrame();
}

function field() {
    const input = document.createElement("input");
    document.body.append(input);
    after(() => input.remove());
    return input;
}

const kept = async () => {
    // Offered again means: no screen is handling it. The tests act as the next page.
    const entries = await keptRecordings(user.userId);
    return entries;
};

test("a failed transcription keeps the recording in this browser as well as in the tab", async () => {
    const dictation = await mountDictation();
    await dictateAndFail(dictation, field());
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.pendingAudio).toBeInstanceOf(File);
    expect(dictation.persisted).toBe(true);
    // The next page (a reload, a crash, a phone that closed the tab) finds it.
    const id = dictation.recordingId;
    disownRecording(id);
    expect((await kept()).map((entry) => entry.id)).toEqual([id]);
});

test("Escape and the cross never discard a finished recording", async () => {
    const dictation = await mountDictation();
    await dictateAndFail(dictation, field());
    const audio = dictation.pendingAudio;

    // Escape in the retry panel belongs to whatever else is open: it is not swallowed.
    const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    document.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.pendingAudio).toBe(audio);

    // The cross beside "Transcribing…" stops the wait and leads to the retry panel.
    patchWithCleanup(FieldDictation.prototype, {
        async transcribe(file, signal) {
            await new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason)));
        },
    });
    click('[data-usl-action="retry"]');
    await animationFrame();
    expect(dictation.state.mode).toBe("processing");
    expect('[data-usl-action="stopWaiting"]').toHaveCount(1);
    expect('[data-usl-action="cancel"]').toHaveCount(0);
    await click('[data-usl-action="stopWaiting"]');
    await animationFrame();
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.pendingAudio).toBe(audio);
});

test("Escape while recording keeps what was said, with Undo", async () => {
    const dictation = await mountDictation();
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    patchWithCleanup(FieldDictation.prototype, { listen() {} });
    const input = field();
    input.focus();
    await animationFrame();
    await dictation.record();
    await animationFrame();
    expect(dictation.state.mode).toBe("recording");
    await press("Escape");
    await animationFrame();
    expect(dictation.state.mode).toBe("idle");
    expect(".o_notification").toHaveCount(1);
    expect(".o_notification").toHaveText(/discarded/);
    await click(".o_notification button:contains(Undo)");
    await animationFrame();
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.pendingAudio).toBeInstanceOf(File);
});

test("Discard can be undone, and frees the recording only when the time is up", async () => {
    const dictation = await mountDictation();
    await dictateAndFail(dictation, field());
    const audio = dictation.pendingAudio;
    const id = dictation.recordingId;

    await click('[data-usl-action="discard"]');
    await animationFrame();
    expect(dictation.state.mode).toBe("idle");
    expect(".o_notification").toHaveText(/discarded/);
    // Still in the browser's storage: closing the page now loses nothing.
    disownRecording(id);
    expect((await kept()).map((entry) => entry.id)).toEqual([id]);

    // Undo: the retry panel is back, with the same file.
    await click(".o_notification button:contains(Undo)");
    await animationFrame();
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.pendingAudio).toBe(audio);

    // Discard again and let the ten seconds pass: now it is gone.
    await click('[data-usl-action="discard"]');
    await animationFrame();
    await advanceTime(11_000);
    await animationFrame();
    expect(".o_notification").toHaveCount(0);
    expect(await kept()).toEqual([]);
    expect(dictation.state.mode).toBe("idle");
});

test("Download audio hands over the recording as voice-YYYYMMDD-HHMM.mp3", async () => {
    const dictation = await mountDictation();
    await dictateAndFail(dictation, field());
    const downloads = [];
    patchWithCleanup(URL, { createObjectURL: (file) => `blob:${file.size}` });
    patchWithCleanup(HTMLAnchorElement.prototype, {
        click() {
            downloads.push({ name: this.download, href: this.href });
        },
    });
    await click('[data-usl-action="download"]');
    expect(downloads).toEqual([{ name: recordingFileName(dictation.recordedAt), href: "blob:15" }]);
    expect(downloads[0].name).toMatch(/^voice-\d{8}-\d{4}\.mp3$/);
    // Downloading is not discarding.
    expect(dictation.state.mode).toBe("retry");
    expect(recordingFileName(new Date(2026, 0, 5, 7, 3).getTime())).toBe("voice-20260105-0703.mp3");
});

test("a recording that heard nothing stays downloadable", async () => {
    const dictation = await mountDictation();
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    patchWithCleanup(FieldDictation.prototype, {
        listen() {},
        async transcribe() {
            return "";
        },
    });
    const input = field();
    input.focus();
    await animationFrame();
    await dictation.record();
    await animationFrame();
    await dictation.stop();
    await animationFrame();
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.state.reason).toBe("silent");
    expect(".o-usl-recovered").toHaveText(/No speech detected/);
    expect('[data-usl-action="download"]').toHaveCount(1);
    expect(dictation.pendingAudio).toBeInstanceOf(File);
});

test("the text is inserted: the recording is no longer kept", async () => {
    const dictation = await mountDictation();
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    patchWithCleanup(FieldDictation.prototype, {
        listen() {},
        async transcribe() {
            return "Heard";
        },
    });
    const input = field();
    input.focus();
    await animationFrame();
    await dictation.record();
    await animationFrame();
    await dictation.stop();
    await animationFrame();
    const id = dictation.ids.at(-1);
    expect(input.value).toBe("Heard");
    expect(dictation.pendingAudio).toBe(null);
    disownRecording(id);
    expect(await kept()).toEqual([]);
});

test("a transcript that cannot be inserted keeps the audio until it is copied", async () => {
    const dictation = await mountDictation();
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    patchWithCleanup(FieldDictation.prototype, {
        listen() {},
        async transcribe() {
            return "Heard";
        },
    });
    patchWithCleanup(navigator.clipboard, { writeText: async () => {} });
    const input = field();
    input.focus();
    await animationFrame();
    await dictation.record();
    await animationFrame();
    dictation.destination = { insert: () => false }; // the field changed meanwhile
    await dictation.stop();
    await animationFrame();
    const id = dictation.ids.at(-1);
    expect(dictation.state.recovered).toBe("Heard");
    disownRecording(id);
    expect((await kept()).map((entry) => entry.id)).toEqual([id]);
    await click('[data-usl-action="copyText"]');
    await animationFrame();
    await animationFrame();
    expect(await kept()).toEqual([]);
});

test("leaving the page asks first only when audio could be lost", async () => {
    const dictation = await mountDictation();
    const leave = () => {
        const event = new Event("beforeunload", { cancelable: true });
        window.dispatchEvent(event);
        return event.defaultPrevented;
    };
    expect(leave()).toBe(false);
    // A recording under way is only in memory.
    dictation.capture = {};
    expect(leave()).toBe(true);
    dictation.capture = null;
    // A finished one that could not be written to the browser's storage too.
    dictation.pendingAudio = clip();
    dictation.persisted = false;
    expect(leave()).toBe(true);
    // Once it is in storage, closing the page loses nothing and nothing is asked.
    dictation.persisted = true;
    expect(leave()).toBe(false);
    dictation.pendingAudio = null;
});

test("closing the page while recording stops and keeps the recording", async () => {
    const dictation = await mountDictation();
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    patchWithCleanup(FieldDictation.prototype, { listen() {} });
    const input = field();
    input.focus();
    await animationFrame();
    await dictation.record();
    await animationFrame();
    expect(dictation.state.mode).toBe("recording");
    window.dispatchEvent(new Event("pagehide"));
    await advanceTime(50);
    await animationFrame();
    expect(dictation.state.mode).toBe("idle");
    expect((await kept()).length).toBe(1);
});

test("an unused recording is offered when the page starts, and can be retried", async () => {
    await keepRecording({ id: "earlier", kind: "dictation", blob: clip(), createdAt: Date.now(), userId: user.userId });
    // Somebody else's recording on the same browser is not offered.
    await keepRecording({ id: "other", kind: "dictation", blob: clip(), createdAt: Date.now(), userId: user.userId + 1000 });
    after(() => forgetRecording("other"));
    patchWithCleanup(FieldDictation.prototype, {
        async transcribe() {
            return "Said earlier";
        },
    });
    const dictation = await mountDictation();
    await animationFrame();
    await animationFrame();
    expect(".o-usl-stored").toHaveCount(1);
    expect(".o-usl-stored .fw-bold").toHaveText(`Unused dictation from ${recordingTime(Date.now())}`);
    expect('.o-usl-stored [data-usl-action="retry"]').toHaveCount(1);
    expect('.o-usl-stored [data-usl-action="download"]').toHaveCount(1);
    expect('.o-usl-stored [data-usl-action="discard"]').toHaveCount(1);

    await click('.o-usl-stored [data-usl-action="retry"]');
    await animationFrame();
    await animationFrame();
    // There is no field to insert in any more: the text is offered for copying.
    expect(dictation.state.recovered).toBe("Said earlier");
    expect(".o-usl-stored").toHaveCount(0);
    expect(".o-usl-recovered-text").toHaveText("Said earlier");
});

test("an unused recording can be downloaded and discarded with Undo from the recovery panel", async () => {
    await keepRecording({ id: "earlier", kind: "dictation", blob: clip(), createdAt: Date.now(), userId: user.userId });
    const dictation = await mountDictation();
    await animationFrame();
    await animationFrame();
    const downloads = [];
    patchWithCleanup(URL, { createObjectURL: () => "blob:x" });
    patchWithCleanup(HTMLAnchorElement.prototype, {
        click() {
            downloads.push(this.download);
        },
    });
    await click('.o-usl-stored [data-usl-action="download"]');
    expect(downloads).toEqual([recordingFileName(Date.now())]);

    await click('.o-usl-stored [data-usl-action="discard"]');
    await animationFrame();
    expect(".o-usl-stored").toHaveCount(0);
    // Undo: it is offered again, still kept.
    await click(".o_notification button:contains(Undo)");
    await animationFrame();
    await animationFrame();
    expect(".o-usl-stored").toHaveCount(1);
    expect(dictation.state.stored.map((entry) => entry.id)).toEqual(["earlier"]);

    // Discard and wait: gone for good.
    await click('.o-usl-stored [data-usl-action="discard"]');
    await animationFrame();
    await advanceTime(11_000);
    await animationFrame();
    expect(await kept()).toEqual([]);
});
