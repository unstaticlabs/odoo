import { after, expect, test } from "@odoo/hoot";
import { click, press, waitFor, waitUntil } from "@odoo/hoot-dom";
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

/** The tests share one browser storage: each starts from, and leaves, an empty one. */
function wipeStorage() {
    return new Promise((resolve) => {
        const request = indexedDB.deleteDatabase("usl_voice");
        request.onsuccess = request.onerror = request.onblocked = () => resolve();
    });
}

/** A recording kept by an earlier page, at a fixed time so that its own time can be checked. */
const EARLIER = 1_700_000_000_000;
const earlier = (extra = {}) => ({ id: "earlier", kind: "dictation", blob: clip(), createdAt: EARLIER, userId: user.userId, ...extra });

/**
 * Hoot replaces navigator.locks with a stub that grants and holds nothing. The ownership between
 * tabs is the point of these tests, so they use the browser's own lock manager (an iframe's
 * navigator reaches it unmocked; locks are shared by every page of the origin).
 */
function realLocks() {
    const frame = document.createElement("iframe");
    document.body.append(frame);
    after(() => frame.remove());
    patchWithCleanup(navigator, { locks: frame.contentWindow.navigator.locks });
}

async function mountDictation(seed = []) {
    realLocks();
    // What an earlier test left handled in this page (a real reload would start clean).
    for (const id of ["earlier", "free", "other"]) {
        disownRecording(id);
    }
    await wipeStorage();
    for (const entry of seed) {
        await keepRecording(entry);
    }
    after(wipeStorage);
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
    await dictation.loadStored(); // what onMounted started: wait for it, not for frames
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
    patchWithCleanup(FieldDictation.prototype, {
        async transcribe() {
            return "Said earlier";
        },
    });
    // Somebody else's recording on the same browser is not offered.
    const dictation = await mountDictation([earlier(), earlier({ id: "other", userId: user.userId + 1000 })]);
    await waitFor(".o-usl-stored");
    expect(".o-usl-stored").toHaveCount(1);
    expect(".o-usl-stored .fw-bold").toHaveText(`Unused dictation (${recordingTime(EARLIER)})`);
    expect('.o-usl-stored [data-usl-action="retry"]').toHaveCount(1);
    expect('.o-usl-stored [data-usl-action="download"]').toHaveCount(1);
    expect('.o-usl-stored [data-usl-action="discard"]').toHaveCount(1);

    await click('.o-usl-stored [data-usl-action="retry"]');
    await waitFor(".o-usl-recovered-text");
    // There is no field to insert in: the text is offered for copying.
    expect(dictation.state.recovered).toBe("Said earlier");
    expect(".o-usl-stored").toHaveCount(0);
    expect(".o-usl-recovered-text").toHaveText("Said earlier");
});

test("an unused recording is retried into the field the writer is in", async () => {
    patchWithCleanup(FieldDictation.prototype, {
        async transcribe() {
            return "Said earlier";
        },
    });
    const dictation = await mountDictation([earlier()]);
    const input = field();
    input.value = "Before ";
    input.focus();
    input.setSelectionRange(7, 7);
    await waitFor(".o-usl-stored");
    await click('.o-usl-stored [data-usl-action="retry"]');
    await waitUntil(() => input.value !== "Before ");
    expect(input.value).toBe("Before Said earlier");
    expect(dictation.state.recovered).toBe("");
    // The text is where it belongs: the audio is no longer kept.
    disownRecording("earlier");
    expect(await kept()).toEqual([]);
});

test("a recording another tab is handling is not offered", async () => {
    // A tab that handles a recording holds a Web Lock on it for as long as it lives.
    realLocks();
    let release;
    const held = new Promise((resolve) => (release = resolve));
    const locked = new Promise((resolve) => navigator.locks.request("usl-voice-earlier", () => (resolve(), held)));
    await locked;
    after(() => release());
    const dictation = await mountDictation([earlier(), earlier({ id: "free", createdAt: EARLIER + 1000 })]);
    expect(dictation.state.stored.map((entry) => entry.id)).toEqual(["free"]);
    // A stale list cannot take it either.
    dictation.state.stored = [earlier()];
    await dictation.retry("earlier");
    expect(Boolean(dictation.pendingAudio)).toBe(false);
    expect(dictation.state.mode).toBe("idle");
    // Once that tab is gone the recording is offered again.
    release();
    let ids = [];
    for (let turn = 0; turn < 20 && ids.length < 2; turn++) {
        await advanceTime(50);
        ids = (await kept()).map((entry) => entry.id).sort();
    }
    expect(ids).toEqual(["earlier", "free"]);
});

test("another tab's change refreshes the list", async () => {
    const dictation = await mountDictation();
    const refreshed = new Promise((resolve) => {
        dictation.loadStored = () => resolve("refreshed");
    });
    // What another tab does after it keeps, forgets or lets go of a recording.
    const other = new BroadcastChannel("usl_voice");
    other.postMessage("change");
    other.close();
    expect(await refreshed).toBe("refreshed");
});

test("an unused recording can be downloaded and discarded with Undo from the recovery panel", async () => {
    const dictation = await mountDictation([earlier()]);
    await waitFor(".o-usl-stored");
    const downloads = [];
    patchWithCleanup(URL, { createObjectURL: () => "blob:x" });
    patchWithCleanup(HTMLAnchorElement.prototype, {
        click() {
            downloads.push(this.download);
        },
    });
    await click('.o-usl-stored [data-usl-action="download"]');
    expect(downloads).toEqual([recordingFileName(EARLIER)]);

    await click('.o-usl-stored [data-usl-action="discard"]');
    await waitFor(".o_notification");
    expect(".o-usl-stored").toHaveCount(0);
    // Undo: it is offered again, still kept.
    await click(".o_notification button:contains(Undo)");
    await waitFor(".o-usl-stored");
    expect(dictation.state.stored.map((entry) => entry.id)).toEqual(["earlier"]);

    // Discard and wait: gone for good.
    await click('.o-usl-stored [data-usl-action="discard"]');
    await waitFor(".o_notification");
    await advanceTime(11_000);
    await waitUntil(() => !document.querySelector(".o_notification"));
    expect(await kept()).toEqual([]);
});

test("Undo keeps the recording as safe, or as unsafe, as it was", async () => {
    const dictation = await mountDictation();
    await dictateAndFail(dictation, field());
    expect(dictation.unsafe()).toBe(false);
    dictation.persisted = false; // the browser could not store it
    dictation.persistence = Promise.resolve(false);
    await click('[data-usl-action="discard"]');
    await waitFor(".o_notification");
    await click(".o_notification button:contains(Undo)");
    await waitFor('[data-usl-action="retry"]');
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.unsafe()).toBe(true);
});

test("a second Stop or an Escape while the recording is being kept does nothing", async () => {
    const dictation = await mountDictation();
    let open;
    const gate = new Promise((resolve) => (open = resolve));
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    patchWithCleanup(FieldDictation.prototype, {
        listen() {},
        async hold(file) {
            await gate;
            return super.hold(file);
        },
        async transcribe() {
            throw Error("busy");
        },
    });
    const input = field();
    input.focus();
    await dictation.record();
    await waitUntil(() => dictation.state.mode === "recording");
    const first = dictation.stop();
    expect(dictation.state.mode).toBe("processing"); // not "recording": nothing left to stop or cancel
    await dictation.stop(); // used to throw: the capture was already taken
    const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    document.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    open();
    await first;
    expect(dictation.state.mode).toBe("retry");
    expect(dictation.pendingAudio).toBeInstanceOf(File);
});

test("an unused recording the page cannot retry because it got busy is offered again", async () => {
    const dictation = await mountDictation([earlier()]);
    const retrying = dictation.retry("earlier");
    dictation.state.mode = "starting"; // a dictation started while the lock was being taken
    await retrying;
    dictation.state.mode = "idle";
    expect(Boolean(dictation.pendingAudio)).toBe(false);
    await waitUntil(() => dictation.state.stored.length === 1);
    expect((await kept()).map((entry) => entry.id)).toEqual(["earlier"]);
});

test("a recording kept in another database of the same browser is not offered", async () => {
    const dictation = await mountDictation([earlier(), earlier({ id: "elsewhere", db: "another_database" })]);
    expect(dictation.state.stored.map((entry) => entry.id)).toEqual(["earlier"]);
});

test("a tab cannot delete a recording another tab is handling", async () => {
    realLocks();
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await new Promise((resolve) => navigator.locks.request("usl-voice-earlier", () => (resolve(), held)));
    after(() => release());
    const dictation = await mountDictation([earlier()]);
    dictation.state.stored = [earlier()]; // a list drawn before the other tab took it
    dictation.discard("earlier");
    expect(dictation.state.stored.length).toBe(0);
    for (let turn = 0; turn < 5; turn++) {
        await advanceTime(50);
    }
    expect(".o_notification").toHaveCount(0); // no Undo offered: nothing was discarded
    release();
    let ids = [];
    for (let turn = 0; turn < 20 && !ids.length; turn++) {
        await advanceTime(50);
        ids = (await kept()).map((entry) => entry.id);
    }
    expect(ids).toEqual(["earlier"]); // still kept, and offered again
});

test("the recovery panel folds into a small count and opens again on a tap", async () => {
    const dictation = await mountDictation([earlier()]);
    await waitFor(".o-usl-stored");
    expect(".o-usl-stored-pill").toHaveCount(0);
    await click('.o-usl-stored [data-usl-action="collapseStored"]');
    await waitFor(".o-usl-stored-pill");
    expect(".o-usl-stored").toHaveCount(0);
    expect(".o-usl-stored-pill").toHaveText("1");
    // Folded away is not gone: the recording is still offered.
    expect(dictation.state.stored.length).toBe(1);
    await click(".o-usl-stored-pill");
    await waitFor(".o-usl-stored");
    expect(".o-usl-stored-pill").toHaveCount(0);
});
