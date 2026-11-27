// Keep the audio in this tab while the private worker is busy. Waiting here
// releases the Odoo HTTP worker, and cancelling the dictation stops uploads.
//
// Each wait is the longer of what the worker asks for and a doubling pause (2, 2, 4, 8, 16, 32 s):
// a five-minute dictation is about 5 MB, and sending it every second or two for a minute and a
// half would cost hundreds of MB of mobile data for nothing.
export const DICTATION_DEADLINE = 90_000;

export async function requestDictation(file, signal, preview = false, post = fetch) {
    const form = new FormData();
    form.append("file", file);
    form.append("csrf_token", odoo.csrf_token);
    form.append(preview ? "preview" : "client_retry", "1");
    const deadline = Date.now() + DICTATION_DEADLINE;
    for (let busy = 0; ; busy++) {
        signal.throwIfAborted();
        const response = await post("/usl/voice/dictate", { method: "POST", body: form, signal });
        if (response.ok) {
            return (await response.json()).text;
        }
        const asked = Number(response.headers.get("Retry-After"));
        if (preview || response.status !== 503 || !Number.isInteger(asked) || asked < 1) {
            throw Error("Dictation unavailable");
        }
        const wait = Math.max(asked, 2 ** busy) * 1000;
        if (Date.now() + wait > deadline) {
            throw Error("Dictation unavailable");
        }
        await new Promise((resolve, reject) => {
            const cancel = () => {
                clearTimeout(timer);
                reject(signal.reason);
            };
            const timer = setTimeout(() => {
                signal.removeEventListener("abort", cancel);
                resolve();
            }, wait);
            signal.addEventListener("abort", cancel, { once: true });
        });
        // A timer that fired late (a background tab) must not start an upload past the deadline.
        if (Date.now() > deadline) {
            throw Error("Dictation unavailable");
        }
    }
}
