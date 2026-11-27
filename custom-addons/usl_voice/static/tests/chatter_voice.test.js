import { after, describe, expect, test } from "@odoo/hoot";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";
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
            return answers.shift() ? { id: 1 } : undefined;
        },
    });
    return uploads;
}

async function openNote() {
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Jane Roe" });
    await start();
    await openFormView("res.partner", partnerId);
    await click("button:text('Log note')");
    after(async () => {
        for (const entry of await keptRecordings(user.userId)) {
            await forgetRecording(entry.id);
        }
    });
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
    // The server holds it now: nothing is left to offer again.
    await animationFrame();
    expect(await keptRecordings(user.userId)).toEqual([]);
});

test("a voice note recorded for a record is uploaded to it after the writer moved on", async () => {
    fakeMicrophone();
    const uploads = fakeUploads([true]);
    let composer;
    patchWithCleanup(Composer.prototype, {
        setup() {
            super.setup();
            composer = this;
        },
    });
    const partnerId = await openNote();
    await record();
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
