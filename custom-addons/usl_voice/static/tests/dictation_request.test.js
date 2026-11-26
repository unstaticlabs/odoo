import { expect, test } from "@odoo/hoot";
import { advanceTime } from "@odoo/hoot-mock";
import { requestDictation } from "@usl_voice/dictation_request";

test("a busy response retries the same recording without repeating a preview", async () => {
    const clip = new File(["synthetic"], "qa.mp3", { type: "audio/mpeg" });
    const bodies = [];
    const post = async (_url, options) => {
        bodies.push(options.body.get("file"));
        return bodies.length === 1
            ? new Response(null, { status: 503, headers: { "Retry-After": "1" } })
            : Response.json({ text: "Ready" });
    };
    const { signal } = new AbortController();
    const pending = requestDictation(clip, signal, false, post);
    await Promise.resolve();
    await advanceTime(1000);
    expect(await pending).toBe("Ready");
    expect(bodies).toEqual([clip, clip]);

    bodies.length = 0;
    await expect(requestDictation(clip, signal, true, post)).rejects.toThrow();
    expect(bodies).toEqual([clip]);
});

test("cancel while waiting stops the next upload", async () => {
    const controller = new AbortController();
    let calls = 0;
    const post = async () => {
        calls++;
        return new Response(null, { status: 503, headers: { "Retry-After": "1" } });
    };
    const pending = requestDictation(new File(["voice"], "clip.mp3"), controller.signal, false, post);
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toThrow();
    await advanceTime(1000);
    expect(calls).toBe(1);
});
