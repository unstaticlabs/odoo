import { EventBus } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { session } from "@web/session";

/**
 * A finished recording is the one thing the writer cannot get back: it is kept in this browser
 * (IndexedDB) from the moment recording stops until its text is inserted or copied, or the
 * writer discards it. A reload, a crash, a logout or a phone that kills the tab loses nothing:
 * the next page offers it again.
 *
 * An entry is {id, kind: "dictation" | "voice_note", blob, createdAt, userId, db, destination?, attachmentId?}.
 * A voice note also names the record it was recorded for: {model, id, name}.
 */
const DATABASE = "usl_voice";
const STORE = "recordings";
const unstored = new Map();

/** Tells the screen that shows unused recordings to read them again. */
export const keptBus = new EventBus();
/** Recordings a live screen of this tab is already handling: they are not "unused". */
const owned = new Set();
/**
 * Another tab handles a recording too: it holds a Web Lock for as long as it does (a closed or
 * crashed tab releases it by itself), and tells the others when something changed.
 */
const lockName = (id) => `usl-voice-${id}`;
const releases = new Map();
const CHANNEL = "usl_voice";
function changed() {
    keptBus.trigger("change");
    try {
        const channel = new BroadcastChannel(CHANNEL);
        channel.postMessage("change");
        channel.close();
    } catch {
        // No BroadcastChannel: the other tabs read the storage when they next start.
    }
}

/** Call back when another tab of this browser changed the kept recordings. Returns a function to stop. */
export function listenOtherTabs(callback) {
    try {
        const channel = new BroadcastChannel(CHANNEL);
        channel.onmessage = callback;
        return () => channel.close();
    } catch {
        return () => {};
    }
}

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
    // The same browser serves every database of the host: a recording belongs to the one it was made in.
    const { ok } = await transact("readwrite", (store) => store.put({ db: session.db, ...entry }));
    if (ok) {
        unstored.delete(entry.id);
    }
    changed();
    return ok;
}

/** Protect a note while the composer is uploading it or IndexedDB is still writing. */
export function trackUnstoredRecording(entry) {
    unstored.set(entry.id, { ...entry, db: session.db, volatile: true });
}

export function hasUnstoredRecordings(userId) {
    return [...unstored.values()].some((entry) => entry.userId === userId && entry.db === session.db);
}

export async function forgetRecording(id) {
    unstored.delete(id);
    letGo(id);
    const { ok } = await transact("readwrite", (store) => store.delete(id));
    changed();
    return ok;
}

function letGo(id) {
    owned.delete(id);
    releases.get(id)?.();
    releases.delete(id);
}

/** The ids another tab (or this one) holds a lock on. */
async function lockedIds() {
    try {
        const { held } = await navigator.locks.query();
        return new Set(held.map(({ name }) => name));
    } catch {
        return new Set();
    }
}

/** The writer's own recordings no screen is handling, the latest first. */
export async function keptRecordings(userId) {
    const { ok, result } = await transact("readonly", (store) => store.getAll());
    const locked = await lockedIds();
    const entries = new Map((ok ? result : []).map((entry) => [entry.id, entry]));
    for (const [id, entry] of unstored) {
        entries.set(id, entry);
    }
    return [...entries.values()]
        .filter((entry) => entry.userId === userId && entry.db === session.db && !owned.has(entry.id) && !locked.has(lockName(entry.id)))
        .map((entry) => ({ ...entry, blob: new File([entry.blob], "Voice.mp3", { type: "audio/mpeg" }) }))
        .sort((a, b) => b.createdAt - a.createdAt);
}

/** A screen takes charge of a recording: it is not offered as unused while it handles it. */
export function ownRecording(id) {
    owned.add(id);
    if (navigator.locks && !releases.has(id)) {
        const held = new Promise((release) => releases.set(id, release));
        navigator.locks.request(lockName(id), () => held).catch(() => {});
    }
}

/**
 * Take charge of a recording that was offered as unused. False when another tab got there first:
 * the writer then does not start a second transcription of the same audio.
 */
export async function claimRecording(id) {
    if (owned.has(id)) {
        return false;
    }
    if (navigator.locks) {
        let release;
        const held = new Promise((resolve) => (release = resolve));
        const granted = await new Promise((resolve) =>
            navigator.locks.request(lockName(id), { ifAvailable: true }, (lock) => {
                resolve(Boolean(lock));
                return lock ? held : undefined;
            }).catch(() => resolve(false))
        );
        if (!granted) {
            return false;
        }
        releases.set(id, release);
    }
    owned.add(id);
    return true;
}

/** The screen lets go without deleting: the recording is offered as unused again. */
export function disownRecording(id) {
    if (owned.has(id)) {
        letGo(id);
        changed();
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
 * is up). Odoo closes a notification that repeats the message of another one, which would end the
 * Undo of the earlier discard (and, through onClose, delete its recording early): every message
 * on screen is therefore unique, whatever the clock says.
 */
export const UNDO_DELAY = 10_000;
const undoMessages = new Set();
function uniqueUndoMessage(at) {
    const base = _t("Recording from %s discarded.", recordingTime(at, luxon.DateTime.TIME_WITH_SECONDS));
    let message = base;
    for (let n = 2; undoMessages.has(message); n++) {
        message = `${base} (${n})`;
    }
    undoMessages.add(message);
    return message;
}
export function offerUndo(notification, at, { undo, expire }) {
    const message = uniqueUndoMessage(at);
    let settled = false;
    const settle = (action) => {
        if (!settled) {
            settled = true;
            action();
        }
    };
    const close = notification.add(message, {
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
        onClose: () => {
            undoMessages.delete(message);
            settle(expire);
        },
    });
}
