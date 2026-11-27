import { expect, test } from "@odoo/hoot";
import { advanceTime } from "@odoo/hoot-mock";
import { requestDictation } from "@usl_voice/dictation_request";

/** Let the request answer and the next wait start: a few microtask turns, no timer involved. */
async function tick() {
    for (let turn = 0; turn < 10; turn++) {
        await Promise.resolve();
    }
}

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

async function posts(headers, seconds = 120) {
    let calls = 0;
    const post = async () => {
        calls++;
        return new Response(null, { status: 503, headers });
    };
    const pending = requestDictation(new File(["voice"], "clip.mp3"), new AbortController().signal, false, post);
    const outcome = pending.then(() => "done", () => "gave up");
    for (let second = 0; second < seconds; second++) {
        await tick(); // let the request answer and the next wait start
        await advanceTime(1000, { animationFrame: false });
    }
    expect(await outcome).toBe("gave up");
    return calls;
}

test("a busy worker is not sent the whole recording every few seconds", async () => {
    // Retry-After 2 used to mean about 45 uploads in 90 seconds: 5 MB each for a long dictation.
    // The wait doubles (2, 2, 4, 8, 16, 32 s): 7 uploads, the last at 64 s.
    expect(await posts({ "Retry-After": "2" })).toBe(7);
});

test("a longer Retry-After from the worker is respected", async () => {
    // The worker asks for 20 s: uploads at 0, 20, 40, 60 and 80 s.
    expect(await posts({ "Retry-After": "20" })).toBe(5);
});

test("a 503 without a numeric Retry-After is not retried", async () => {
    expect(await posts({}, 5)).toBe(1);
    expect(await posts({ "Retry-After": "soon" }, 5)).toBe(1);
});

test("a timer that fires after the deadline does not start another upload", async () => {
    let calls = 0;
    const post = async () => {
        calls++;
        return new Response(null, { status: 503, headers: { "Retry-After": "2" } });
    };
    const pending = requestDictation(new File(["voice"], "clip.mp3"), new AbortController().signal, false, post);
    const outcome = pending.then(() => "done", () => "gave up");
    await tick();
    // A background tab wakes up long after the deadline.
    await advanceTime(200_000, { animationFrame: false });
    expect(await outcome).toBe("gave up");
    expect(calls).toBeLessThan(3);
});
