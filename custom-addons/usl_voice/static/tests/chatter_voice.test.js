import { after, describe, expect, test } from "@odoo/hoot";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";
import { waitUntil } from "@odoo/hoot-dom";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";
import { user } from "@web/core/user";
import {
    click, contains, defineMailModels, openFormView, start, startServer,
} from "@mail/../tests/mail_test_helpers";
import { Composer } from "@mail/core/common/composer";
import { AttachmentUploadService } from "@mail/core/common/attachment_upload_service";
import { VoiceCapture } from "@usl_voice/capture";
import { forgetRecording, keptRecordings } from "@usl_voice/recording_store";
import "@usl_voice/chatter";

describe.current.tags("desktop");
defineMailModels();

const clip = () => new File(["synthetic-voice-note"], "Voice.mp3", { type: "audio/mpeg" });

/** No microphone in the tests: the recorder starts at once and stops with a file. */
const composers = [];
function fakeMicrophone() {
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
        finish() {
            return clip();
        },
    });
    if (!navigator.mediaDevices?.getUserMedia) {
        patchWithCleanup(navigator, { mediaDevices: { getUserMedia: async () => {} } });
    }
}

/** The upload goes through the native service: what it is given, and what it answers, is the contract. */
function fakeUploads(answers) {
    const uploads = [];
    patchWithCleanup(AttachmentUploadService.prototype, {
        async upload(thread, composer, file, options) {
            uploads.push({ model: thread.model, id: thread.id, composer: Boolean(composer), file, voice: options?.voice });
            if (!answers.shift()) {
                return undefined;
            }
            // As the native upload does: the pending attachment joins the composer.
            const attachment = (composer || thread).store["ir.attachment"].insert({ id: 9001, name: "Voice.mp3", mimetype: "audio/mpeg" });
            composer?.attachments.push(attachment);
            return attachment;
        },
    });
    return uploads;
}

/** Start from, and leave, an empty browser storage: the tests share one. */
function wipeStorage() {
    return new Promise((resolve) => {
        const request = indexedDB.deleteDatabase("usl_voice");
        request.onsuccess = request.onerror = request.onblocked = () => resolve();
    });
}

/** Hoot's navigator.locks grants and holds nothing: take the browser's own (an iframe reaches it). */
function realLocks() {
    const frame = document.createElement("iframe");
    document.body.append(frame);
    after(() => frame.remove());
    patchWithCleanup(navigator, { locks: frame.contentWindow.navigator.locks });
}

async function openNote() {
    realLocks();
    await wipeStorage();
    after(wipeStorage);
    patchWithCleanup(Composer.prototype, {
        setup() {
            super.setup();
            composers.push(this);
        },
    });
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Jane Roe" });
    await start();
    await openFormView("res.partner", partnerId);
    await click("button:text('Log note')");
    return partnerId;
}

async function record() {
    await click("button[aria-label='Record a voice note']");
    await contains(".o-usl-voice-bar button[aria-label='Stop and preview']");
}

async function stop() {
    await click(".o-usl-voice-bar button[aria-label='Stop and preview']");
}

test("a failed upload keeps the voice note, with Retry and Download", async () => {
    fakeMicrophone();
    const uploads = fakeUploads([false, true]);
    const partnerId = await openNote();
    await record();
    await stop();
    await contains(".o-usl-voice-bar:contains('Voice note not uploaded.')");
    await contains(".o-usl-voice-bar:contains('Your recording is kept.')");
    await contains(".o-usl-voice-bar button:contains('Download audio')");
    // The recording is also in this browser: a reload now loses nothing.
    expect(uploads.length).toBe(1);
    expect(uploads[0]).toMatchObject({ model: "res.partner", id: partnerId, composer: true, voice: true });

    await click(".o-usl-voice-bar button:contains('Retry upload')");
    await contains(".o-usl-voice-bar", { count: 0 });
    expect(uploads.length).toBe(2);
    expect(uploads[1].file).toBe(uploads[0].file);
    // The server only holds a pending attachment of the composer: the browser keeps its copy
    // (handled by the composer) until the message is posted.
    await animationFrame();
    expect(Boolean(composers.at(-1).uslPending.kept.id)).toBe(true);
});

test("an uploaded voice note stays kept when the composer closes before Send", async () => {
    // The pending attachment is deleted by Odoo a day later if no message carries it, and a reload
    // loses the composer: the recording must still be offered at the next page.
    fakeMicrophone();
    const uploads = fakeUploads([true]);
    await openNote();
    await record();
    await stop();
    await contains(".o-usl-voice-bar", { count: 0 });
    expect(uploads.length).toBe(1);
    expect(await keptRecordings(user.userId)).toEqual([]); // the composer handles it
    await click("button:text('Log note')"); // closed without sending
    await contains(".o-mail-Composer", { count: 0 });
    await animationFrame();
    const entries = await keptRecordings(user.userId);
    expect(entries.length).toBe(1);
    expect(entries[0].kind).toBe("voice_note");
});

test("a voice note that left the composer with its message, or was removed, is forgotten", async () => {
    fakeMicrophone();
    fakeUploads([true]);
    await openNote();
    await record();
    await stop();
    await contains(".o-usl-voice-bar", { count: 0 });
    const composer = composers.at(-1);
    expect(composer.uslPending).not.toBe(null);
    // Posting a message (or removing the attachment) empties the composer's attachments.
    composer.props.composer.attachments.length = 0;
    await animationFrame();
    await animationFrame();
    expect(composer.uslPending).toBe(null);
    await click("button:text('Log note')");
    await contains(".o-mail-Composer", { count: 0 });
    expect(await keptRecordings(user.userId)).toEqual([]);
});

test("a voice note recorded for a record is uploaded to it after the writer moved on", async () => {
    fakeMicrophone();
    const uploads = fakeUploads([true]);
    const partnerId = await openNote();
    await record();
    const composer = composers.at(-1);
    // The recording was made for another record than the one the composer shows when it stops:
    // what the composer saved when it started is where the voice note goes.
    expect(composer.uslVoiceDestination).toMatchObject({ model: "res.partner", id: partnerId });
    composer.uslVoiceDestination = { model: "res.partner", id: partnerId + 1000, name: "Elsewhere" };
    await stop();
    await animationFrame();
    expect(uploads.length).toBe(1);
    // Not dropped, and not attached to the record now on screen: to the record it was recorded for.
    expect(uploads[0]).toMatchObject({ model: "res.partner", id: partnerId + 1000, composer: false, voice: true });
    expect(await keptRecordings(user.userId)).toEqual([]);
});

test("closing the composer while recording stops and keeps the voice note", async () => {
    fakeMicrophone();
    const uploads = fakeUploads([true]);
    const partnerId = await openNote();
    await record();
    // The same button closes the composer.
    await click("button:text('Log note')");
    await contains(".o-mail-Composer", { count: 0 });
    await animationFrame();
    expect(uploads.length).toBe(1);
    expect(uploads[0]).toMatchObject({ model: "res.partner", id: partnerId, composer: false, voice: true });
});

test("a voice note that cannot be uploaded after the composer closed is offered again", async () => {
    fakeMicrophone();
    const uploads = fakeUploads([false]);
    await openNote();
    await record();
    await click("button:text('Log note')");
    await contains(".o-mail-Composer", { count: 0 });
    await animationFrame();
    expect(uploads.length).toBe(1);
    const entries = await keptRecordings(user.userId);
    expect(entries.length).toBe(1);
    expect(entries[0].kind).toBe("voice_note");
    expect(entries[0].destination).toMatchObject({ model: "res.partner" });
});

test("cancelling a voice note while recording can be undone", async () => {
    fakeMicrophone();
    const uploads = fakeUploads([true]);
    await openNote();
    await record();
    await click(".o-usl-voice-bar button[aria-label='Discard recording']");
    await contains(".o_notification:contains('discarded')");
    expect(uploads.length).toBe(0);
    await click(".o_notification button:contains('Undo')");
    await animationFrame();
    expect(uploads.length).toBe(1);
});

test("a discarded voice note is freed when the time is up", async () => {
    fakeMicrophone();
    fakeUploads([true]);
    await openNote();
    await record();
    await click(".o-usl-voice-bar button[aria-label='Discard recording']");
    await contains(".o_notification:contains('discarded')");
    await advanceTime(11_000);
    await contains(".o_notification", { count: 0 });
    await animationFrame();
    expect(await keptRecordings(user.userId)).toEqual([]);
});

test("Undo does not take over a recording that was started since", async () => {
    fakeMicrophone();
    const uploads = fakeUploads([true]);
    await openNote();
    await record();
    await click(".o-usl-voice-bar button[aria-label='Discard recording']");
    await contains(".o_notification:contains('discarded')");
    // The writer starts another voice note, then remembers the first and presses Undo.
    await record();
    await click(".o_notification button:contains('Undo')");
    await animationFrame();
    expect(composers.at(-1).uslVoice.mode).toBe("recording");
    expect(uploads.length).toBe(0);
    await contains(".o-usl-voice-bar button[aria-label='Stop and preview']");
    // The new recording still stops and uploads.
    await stop();
    await contains(".o-usl-voice-bar", { count: 0 });
    expect(uploads.length).toBe(1);
    // The undone one is not lost: it is offered as an unused voice note.
    const entries = await keptRecordings(user.userId);
    expect(entries.length).toBe(1);
    expect(entries[0].kind).toBe("voice_note");
});

test("leaving the page asks first while a voice note is failed or pending and not stored", async () => {
    fakeMicrophone();
    fakeUploads([false]);
    await openNote();
    const leave = () => {
        const event = new Event("beforeunload", { cancelable: true });
        window.dispatchEvent(event);
        return event.defaultPrevented;
    };
    expect(leave()).toBe(false);
    await record();
    expect(leave()).toBe(true); // being recorded
    await stop();
    await contains(".o-usl-voice-bar:contains('Voice note not uploaded.')");
    const composer = composers.at(-1);
    await waitUntil(() => composer.uslKept.persisted);
    expect(leave()).toBe(false); // failed, but safe in the browser's storage
    composer.uslKept.persisted = false; // the browser could not store it
    expect(leave()).toBe(true);
    composer.uslKept.persisted = true;
    expect(leave()).toBe(false);
});

test("a composer opened again for the record takes its pending voice note back", async () => {
    fakeMicrophone();
    fakeUploads([true]);
    await openNote();
    await record();
    await stop();
    await contains(".o-usl-voice-bar", { count: 0 });
    const first = composers.at(-1);
    await first.uslPending.kept.saving; // the entry knows its pending attachment now
    await click("button:text('Log note')"); // closed without sending
    await contains(".o-mail-Composer", { count: 0 });
    // The attachment is still the composer's in this page: no "not attached" offer, no duplicate.
    await animationFrame();
    expect(".o-usl-stored").toHaveCount(0);
    // Opened again, the composer holds the attachment and the note is its own again.
    await click("button:text('Log note')");
    await contains(".o-mail-Composer");
    const second = composers.at(-1);
    await waitUntil(() => second.uslPending);
    expect(second.uslPending.attachmentId).toBe(9001);
    // Posting (which empties the composer's attachments) is the end of the browser copy.
    second.props.composer.attachments.length = 0;
    await animationFrame();
    await animationFrame();
    expect(second.uslPending).toBe(null);
    await click("button:text('Log note')");
    await contains(".o-mail-Composer", { count: 0 });
    expect(await keptRecordings(user.userId)).toEqual([]);
});
