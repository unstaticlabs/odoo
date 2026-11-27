import { EventBus } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

/**
 * A finished recording is the one thing the writer cannot get back: it is kept in this browser
 * (IndexedDB) from the moment recording stops until its text is inserted or copied, or the
 * writer discards it. A reload, a crash, a logout or a phone that kills the tab loses nothing:
 * the next page offers it again.
 *
 * An entry is {id, kind: "dictation" | "voice_note", blob, createdAt, userId, destination?}.
 * A voice note also names the record it was recorded for: {model, id, name}.
 */
const DATABASE = "usl_voice";
const STORE = "recordings";

/** Tells the screen that shows unused recordings to read them again. */
export const keptBus = new EventBus();
/** Recordings a live screen is already handling: they are not "unused". */
const owned = new Set();

function open() {
    return new Promise((resolve) => {
        try {
            const request = indexedDB.open(DATABASE, 1);
            request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "id" });
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => resolve(null);
            request.onblocked = () => resolve(null);
        } catch {
            resolve(null); // no IndexedDB (private mode, old browser): the page then warns before it unloads
        }
    });
}

async function transact(mode, work) {
    const database = await open();
    if (!database) {
        return { ok: false };
    }
    return new Promise((resolve) => {
        try {
            const transaction = database.transaction(STORE, mode);
            const request = work(transaction.objectStore(STORE));
            transaction.oncomplete = () => {
                database.close();
                resolve({ ok: true, result: request?.result });
            };
            transaction.onerror = transaction.onabort = () => {
                database.close();
                resolve({ ok: false });
            };
        } catch {
            database.close();
            resolve({ ok: false });
        }
    });
}

export function newRecordingId() {
    return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** @returns {Promise<boolean>} whether the recording is now safe in this browser */
export async function keepRecording(entry) {
    const { ok } = await transact("readwrite", (store) => store.put(entry));
    keptBus.trigger("change");
    return ok;
}

export async function forgetRecording(id) {
    owned.delete(id);
    const { ok } = await transact("readwrite", (store) => store.delete(id));
    keptBus.trigger("change");
    return ok;
}

/** The writer's own recordings no screen is handling, the latest first. */
export async function keptRecordings(userId) {
    const { ok, result } = await transact("readonly", (store) => store.getAll());
    return (ok ? result : [])
        .filter((entry) => entry.userId === userId && !owned.has(entry.id))
        .map((entry) => ({ ...entry, blob: new File([entry.blob], "Voice.mp3", { type: "audio/mpeg" }) }))
        .sort((a, b) => b.createdAt - a.createdAt);
}

/** A screen takes charge of a recording: it is not offered as unused while it handles it. */
export function ownRecording(id) {
    owned.add(id);
}

/** The screen lets go without deleting: the recording is offered as unused again. */
export function disownRecording(id) {
    if (owned.delete(id)) {
        keptBus.trigger("change");
    }
}

export function recordingFileName(at) {
    const pad = (value) => String(value).padStart(2, "0");
    const date = new Date(at);
    return `voice-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}.mp3`;
}

/** The guaranteed way out: the audio itself, whatever state the server or the speech worker is in. */
export function downloadRecording(file, at) {
    const url = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = url;
    link.download = recordingFileName(at);
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** "10:42", or the date as well when it is not today. */
export function recordingTime(at, format = luxon.DateTime.TIME_SIMPLE) {
    const { DateTime } = luxon;
    const date = DateTime.fromMillis(at);
    return date.hasSame(DateTime.now(), "day")
        ? date.toLocaleString(format)
        : date.toLocaleString(DateTime.DATETIME_SHORT);
}

/**
 * Discarding is never final at once: the recording stays kept for ten seconds with an Undo button
 * (and if the page closes first it is still there, which is why it is only deleted when the time
 * is up). The message carries the seconds: Odoo closes a notification that repeats another one,
 * and that would end the Undo of the earlier discard.
 */
export const UNDO_DELAY = 10_000;
export function offerUndo(notification, at, { undo, expire }) {
    let settled = false;
    const settle = (action) => {
        if (!settled) {
            settled = true;
            action();
        }
    };
    const close = notification.add(_t("Recording from %s discarded.", recordingTime(at, luxon.DateTime.TIME_WITH_SECONDS)), {
        type: "info",
        autocloseDelay: UNDO_DELAY,
        buttons: [{
            name: _t("Undo"),
            primary: true,
            onClick: () => {
                settle(undo);
                close();
            },
        }],
        onClose: () => settle(expire),
    });
}
