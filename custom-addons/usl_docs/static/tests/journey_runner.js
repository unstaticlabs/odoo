/** @odoo-module */

/**
 * Turn a stock tour into a documented journey.
 *
 * A journey is a tour plus a sibling entry in the ``usl_docs.journeys``
 * registry, keyed by the tour name. The entry names the page (slug, title,
 * description, persona, language) and, per step ``id``, the sentence the
 * reader follows and whether the screen at that moment belongs on the page.
 *
 * Tours stay exactly as ``web_tour`` wants them: the tour service validates
 * every step against a closed schema and reports any other key with
 * ``console.error``, which the Python test treats as a failure. So the
 * documentation never sits on the step; it sits here, beside it.
 *
 * ``odoo.uslDocsStartTour(name, options)`` wraps the tour before it starts:
 * after each documented step it inserts a synthetic step that waits for the
 * page to settle, hides what the journey asked to mask, announces itself on
 * the console and waits until the Python side has captured the screen. The
 * Python harness (``usl_docs/tests/journey.py``) listens to those console
 * lines, captures over the DevTools protocol, then resolves the wait.
 */

import { registry } from "@web/core/registry";
import { rpcBus } from "@web/core/network/rpc";

const tours = registry.category("web_tour.tours");
export const journeys = registry.category("usl_docs.journeys");

export const PERSONAS = [
    "everyone",
    "ceo",
    "accountant",
    "finance_operator",
    "employee",
    "manager",
    "administrator",
];
export const TYPES = ["tutorial", "how-to"];
export const LANGUAGES = ["en", "fr"];

/** CSS that keeps a screen still while it is captured. */
const FREEZE_STYLE = `
body.usl-docs-journey *, body.usl-docs-journey *::before, body.usl-docs-journey *::after {
    animation: none !important;
    transition: none !important;
    caret-color: transparent !important;
    scroll-behavior: auto !important;
}
body.usl-docs-journey ::-webkit-scrollbar { display: none !important; }
body.usl-docs-journey .o_notification_manager,
body.usl-docs-journey .o_loading_indicator,
body.usl-docs-journey .o-tooltip,
body.usl-docs-journey .o_tooltip,
body.usl-docs-journey .o-mail-Message-date,
body.usl-docs-journey .o-mail-Message-header time,
body.usl-docs-journey .o-mail-Chatter time { visibility: hidden !important; }
body.usl-docs-journey .o_navbar .o_user_menu img { visibility: hidden !important; }
`;

let inFlight = 0;
rpcBus.addEventListener("RPC:REQUEST", () => {
    inFlight += 1;
});
rpcBus.addEventListener("RPC:RESPONSE", () => {
    inFlight = Math.max(0, inFlight - 1);
});

function nextFrame() {
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Resolve once fonts are loaded, no RPC is pending and nothing is loading. */
export async function settle(timeoutMs = 15000) {
    const started = Date.now();
    if (document.fonts && document.fonts.ready) {
        await document.fonts.ready;
    }
    let quietFrames = 0;
    while (Date.now() - started < timeoutMs) {
        await nextFrame();
        const busy =
            inFlight > 0 ||
            document.querySelector(".o_loading_indicator, .o_form_view.o_form_saving, .o_blockUI");
        quietFrames = busy ? 0 : quietFrames + 1;
        if (quietFrames >= 3) {
            await sleep(50);
            return;
        }
    }
    throw new Error("usl_docs: the page did not settle before the screenshot");
}

function validateJourney(name, journey, steps) {
    const problems = [];
    for (const key of ["id", "title", "description"]) {
        if (typeof journey[key] !== "string" || !journey[key]) {
            problems.push(`${key} is required`);
        }
    }
    if (!TYPES.includes(journey.type)) {
        problems.push(`type must be one of ${TYPES.join(", ")}`);
    }
    if (!PERSONAS.includes(journey.persona)) {
        problems.push(`persona must be one of ${PERSONAS.join(", ")}`);
    }
    if (journey.lang !== undefined && !LANGUAGES.includes(journey.lang)) {
        problems.push(`lang must be one of ${LANGUAGES.join(", ")}`);
    }
    const ids = new Set(steps.map((step) => step.id).filter(Boolean));
    for (const [id, meta] of Object.entries(journey.steps || {})) {
        if (!ids.has(id)) {
            problems.push(`step ${id} is documented but the tour has no step with that id`);
        }
        if (typeof meta.text !== "string" || !meta.text) {
            problems.push(`step ${id} needs a text`);
        }
    }
    for (const step of steps) {
        if (step.expectUnloadPage) {
            problems.push(
                `step ${step.id || step.trigger} unloads the page; journeys navigate inside the web client`
            );
        }
    }
    if (problems.length) {
        console.error(`usl_docs: journey ${name} is invalid: ${problems.join("; ")}`);
        return false;
    }
    return true;
}

function screenshotStep(name, id, meta) {
    return {
        trigger: "body",
        content: `usl_docs screenshot ${id}`,
        timeout: 60000,
        async run() {
            await settle();
            const masked = [];
            for (const selector of meta.mask || []) {
                for (const element of document.querySelectorAll(selector)) {
                    masked.push([element, element.style.visibility]);
                    element.style.visibility = "hidden";
                }
            }
            if (document.activeElement && !meta.keepFocus) {
                document.activeElement.blur();
            }
            await nextFrame();
            const captured = new Promise((resolve) => {
                window.__uslDocsResolve = window.__uslDocsResolve || {};
                window.__uslDocsResolve[id] = resolve;
            });
            console.log(`usl_docs:screenshot:${name}:${id}`);
            await captured;
            for (const [element, visibility] of masked) {
                element.style.visibility = visibility;
            }
        },
    };
}

/**
 * Start ``name`` as a journey. Options are passed to ``odoo.startTour``.
 */
export async function startJourney(name, options = {}) {
    const journey = journeys.get(name, null);
    const tour = tours.get(name, null);
    if (!journey || !tour) {
        console.error(`usl_docs: ${name} is not both a tour and a journey`);
        return;
    }
    const steps = tour.steps();
    if (!validateJourney(name, journey, steps)) {
        return;
    }
    document.body.classList.add("usl-docs-journey");
    const style = document.createElement("style");
    style.textContent = FREEZE_STYLE;
    document.head.appendChild(style);

    const wrapped = [];
    const documented = [];
    for (const step of steps) {
        wrapped.push(step);
        const meta = step.id && journey.steps[step.id];
        if (meta) {
            documented.push({
                id: step.id,
                text: meta.text,
                assertion: typeof step.content === "string" ? step.content : "",
                trigger: typeof step.trigger === "string" ? step.trigger : "",
                run: typeof step.run === "string" ? step.run : step.run ? "function" : "",
                screenshot: Boolean(meta.screenshot),
            });
            if (meta.screenshot) {
                wrapped.push(screenshotStep(name, step.id, meta));
            }
        }
    }
    tours.add(name, { ...tour, steps: () => wrapped }, { force: true });
    console.log(
        `usl_docs:journey:${JSON.stringify({
            tour: name,
            id: journey.id,
            type: journey.type,
            title: journey.title,
            description: journey.description,
            persona: journey.persona,
            lang: journey.lang || "en",
            steps: documented,
        })}`
    );
    return odoo.startTour(name, options);
}

odoo.uslDocsStartTour = startJourney;
