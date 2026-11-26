// Keep the audio in this tab while the private worker is busy. Waiting here
// releases the Odoo HTTP worker, and cancelling the dictation stops uploads.
export async function requestDictation(file, signal, preview = false, post = fetch) {
    const form = new FormData();
    form.append("file", file);
    form.append("csrf_token", odoo.csrf_token);
    form.append(preview ? "preview" : "client_retry", "1");
    const deadline = performance.now() + 90_000;
    while (true) {
        signal.throwIfAborted();
        const response = await post("/usl/voice/dictate", { method: "POST", body: form, signal });
        if (response.ok) {
            return (await response.json()).text;
        }
        const seconds = Number(response.headers.get("Retry-After"));
        if (preview || response.status !== 503 || !Number.isInteger(seconds) || seconds < 1 ||
            seconds > 5 || performance.now() + seconds * 1000 > deadline) {
            throw Error("Dictation unavailable");
        }
        signal.throwIfAborted();
        await new Promise((resolve, reject) => {
            const cancel = () => {
                clearTimeout(timer);
                reject(signal.reason);
            };
            const timer = setTimeout(() => {
                signal.removeEventListener("abort", cancel);
                resolve();
            }, seconds * 1000);
            signal.addEventListener("abort", cancel, { once: true });
        });
    }
}
