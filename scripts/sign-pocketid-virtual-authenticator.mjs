#!/usr/bin/env node

import {createHash, createPublicKey, randomBytes, verify} from "node:crypto";
import {execFileSync, spawn} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";


const ROOT = resolve(import.meta.dirname, "..");
const ENV_FILE = process.env.USL_SIGN_POCKETID_ENV_FILE || join(ROOT, ".sign-pocketid-qa.env");
const CHROME = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const CLIENT_ID = "usl-sign-authorization";
let REDIRECT_URI;


function readEnv(raw) {
    return Object.fromEntries(
        raw.split("\n")
            .map((line) => line.trim())
            .filter((line) => line && !line.startsWith("#") && line.includes("="))
            .map((line) => {
                const separator = line.indexOf("=");
                return [line.slice(0, separator), line.slice(separator + 1)];
            })
    );
}


function base64url(raw) {
    return Buffer.from(raw).toString("base64url");
}


function decodeJwtSegment(value) {
    return JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
}


function sleep(milliseconds) {
    return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}


function qaFetch(url, options = {}) {
    const target = new URL(url);
    if (!target.hostname.endsWith(".localhost")) {
        return fetch(target, options);
    }
    // Some Node resolvers do not apply the reserved .localhost loopback rule
    // that browsers and curl implement. Keep the public Host header and OIDC
    // issuer while connecting the isolated QA request to loopback.
    const publicHost = target.host;
    target.hostname = "127.0.0.1";
    return fetch(target, {
        ...options,
        headers: {...options.headers, Host: publicHost},
    });
}


function stackAcceptance(command, ...args) {
    const output = execFileSync(
        join(ROOT, "scripts/sign-pocketid-stack"),
        [command, ...args.map(String)],
        {cwd: ROOT, encoding: "utf8"}
    );
    const marker = output.match(/^USL_SIGN_STRONG_ACCEPTANCE=(.+)$/m)?.[1];
    if (!marker) {
        throw new Error(`The ${command} fixture did not return its acceptance marker`);
    }
    return JSON.parse(marker);
}


async function waitFor(read, description, timeout = 15000) {
    const deadline = Date.now() + timeout;
    let lastError;
    while (Date.now() < deadline) {
        try {
            const value = await read();
            if (value) {
                return value;
            }
        } catch (error) {
            lastError = error;
        }
        await sleep(100);
    }
    throw new Error(`${description} timed out${lastError ? `: ${lastError.message}` : ""}`);
}


class CDPClient {
    constructor(url) {
        this.socket = new WebSocket(url);
        this.sequence = 0;
        this.pending = new Map();
        this.handlers = new Map();
        this.ready = new Promise((resolvePromise, rejectPromise) => {
            this.socket.addEventListener("open", resolvePromise, {once: true});
            this.socket.addEventListener("error", rejectPromise, {once: true});
        });
        this.socket.addEventListener("message", (event) => this.receive(event.data));
    }

    receive(raw) {
        const message = JSON.parse(String(raw));
        if (message.id && this.pending.has(message.id)) {
            const {resolve: resolvePromise, reject: rejectPromise} = this.pending.get(message.id);
            this.pending.delete(message.id);
            if (message.error) {
                rejectPromise(new Error(`${message.error.message} (${message.error.code})`));
            } else {
                resolvePromise(message.result || {});
            }
            return;
        }
        for (const handler of this.handlers.get(message.method) || []) {
            handler(message.params || {}, message.sessionId);
        }
    }

    async send(method, params = {}, sessionId = undefined) {
        await this.ready;
        const id = ++this.sequence;
        const payload = {id, method, params};
        if (sessionId) {
            payload.sessionId = sessionId;
        }
        const response = new Promise((resolvePromise, rejectPromise) => {
            this.pending.set(id, {resolve: resolvePromise, reject: rejectPromise});
        });
        this.socket.send(JSON.stringify(payload));
        return response;
    }

    on(method, handler) {
        const handlers = this.handlers.get(method) || [];
        handlers.push(handler);
        this.handlers.set(method, handlers);
    }
}


async function evaluate(client, sessionId, expression, {userGesture = false} = {}) {
    const result = await client.send(
        "Runtime.evaluate",
        {expression, awaitPromise: true, returnByValue: true, userGesture},
        sessionId
    );
    if (result.exceptionDetails) {
        throw new Error(result.exceptionDetails.text || "Browser evaluation failed");
    }
    return result.result?.value;
}


async function clickVisibleButton(client, sessionId, labels) {
    return evaluate(
        client,
        sessionId,
        `(() => {
            const labels = ${JSON.stringify(labels.map((label) => label.toLowerCase()))};
            const buttons = Array.from(document.querySelectorAll("button"));
            const button = buttons.find((candidate) => {
                const text = (candidate.innerText || candidate.textContent || "").trim().toLowerCase();
                return candidate.offsetParent !== null && labels.includes(text) && !candidate.disabled;
            });
            if (!button) return false;
            button.click();
            return true;
        })()`
    );
}


async function clickPocketAuthorization(client, sessionId, assertedCount, description) {
    const before = assertedCount.value;
    const clicked = await waitFor(
        () => clickVisibleButton(
            client,
            sessionId,
            ["Authenticate", "Continue", "Log in", "Sign in", "Use passkey", "Use Passkey"]
        ),
        `${description} action`,
        10000
    );
    if (!clicked) {
        throw new Error(`${description} did not expose a Pocket ID action`);
    }
    await waitFor(
        async () => {
            const location = await evaluate(client, sessionId, "location.href");
            return location.startsWith(REDIRECT_URI);
        },
        `${description} callback`,
        30000
    ).catch(async (error) => {
        const diagnostic = await evaluate(
            client,
            sessionId,
            `({url: location.href, text: (document.body?.innerText || "").slice(0, 900)})`
        ).catch(() => ({url: "unavailable", text: ""}));
        throw new Error(
            `${error.message}; stopped at ${diagnostic.url}: ${diagnostic.text.replaceAll("\n", " | ")}`
        );
    });
    await waitFor(
        () => evaluate(
            client,
            sessionId,
            `document.getElementById("usl_pocketid_callback")?.dataset.successful === "true"`
        ),
        `${description} verification result`,
        10000
    );
    if (assertedCount.value <= before) {
        throw new Error(`${description} did not invoke the virtual passkey`);
    }
}


async function jsonRpc(client, sessionId, route, params = {}) {
    return evaluate(
        client,
        sessionId,
        `(async () => {
            const response = await fetch(${JSON.stringify(route)}, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({jsonrpc: "2.0", method: "call", params: ${JSON.stringify(params)}}),
            });
            const payload = await response.json();
            if (!response.ok || payload.error) {
                throw new Error(payload.error?.data?.message || payload.error?.message || "RPC failed");
            }
            return payload.result;
        })()`
    );
}


async function registerPasskey(client, sessionId, authenticatorId) {
    const clicked = await clickVisibleButton(client, sessionId, ["Add Passkey"]);
    if (!clicked) {
        throw new Error("Pocket ID did not expose Add Passkey");
    }
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
        const credentials = await client.send(
            "WebAuthn.getCredentials",
            {authenticatorId},
            sessionId
        );
        if (credentials.credentials?.length) {
            return credentials.credentials;
        }
        await evaluate(
            client,
            sessionId,
            `(() => {
                const dialog = document.querySelector('[role="dialog"]');
                if (!dialog) return false;
                const input = Array.from(dialog.querySelectorAll("input"))
                    .find((candidate) => candidate.offsetParent !== null && !candidate.disabled);
                if (input && !input.value) {
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
                    setter.call(input, "USL Sign QA Virtual Passkey");
                    input.dispatchEvent(new Event("input", {bubbles: true}));
                    input.dispatchEvent(new Event("change", {bubbles: true}));
                }
                const action = Array.from(dialog.querySelectorAll("button")).find((candidate) => {
                    const text = (candidate.innerText || "").trim().toLowerCase();
                    return candidate.offsetParent !== null
                        && ["add", "add passkey", "create", "continue", "save"].includes(text)
                        && !candidate.disabled;
                });
                if (action) action.click();
                return Boolean(action);
            })()`
        );
        await sleep(200);
    }
    throw new Error("Pocket ID did not register the virtual passkey");
}


async function verifyIdToken(token, discovery, expected) {
    const parts = token.split(".");
    if (parts.length !== 3) {
        throw new Error("Pocket ID returned a malformed ID token");
    }
    const header = decodeJwtSegment(parts[0]);
    const claims = decodeJwtSegment(parts[1]);
    const jwks = await (await qaFetch(discovery.jwks_uri)).json();
    const jwk = jwks.keys.find((candidate) => candidate.kid === header.kid);
    if (header.alg !== "RS256" || !jwk) {
        throw new Error("Pocket ID returned an unsupported signing key");
    }
    const signatureValid = verify(
        "RSA-SHA256",
        Buffer.from(`${parts[0]}.${parts[1]}`),
        createPublicKey({key: jwk, format: "jwk"}),
        Buffer.from(parts[2], "base64url")
    );
    const audience = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    const failures = [
        [!signatureValid, "signature"],
        [claims.iss !== discovery.issuer, "issuer"],
        [!audience.includes(CLIENT_ID), "audience"],
        [claims.nonce !== expected.nonce, "nonce"],
        [!Array.isArray(claims.amr), "amr-shape"],
        [!claims.amr?.includes("phr"), "passkey-method"],
        [claims.amr?.includes("otp"), "otp-method"],
        [Number(claims.auth_time) < expected.createdAt, "authentication-time"],
        [Number(claims.exp) <= Math.floor(Date.now() / 1000), "expiry"],
    ].filter(([failed]) => failed).map(([, name]) => name);
    if (failures.length) {
        throw new Error(
            `Pocket ID ID-token claims failed: ${failures.join(", ")}`
        );
    }
    return claims;
}


async function authorizeFreshPasskey(client, sessionId, discovery, env, assertedCount) {
    const verifier = base64url(randomBytes(32));
    const challenge = base64url(createHash("sha256").update(verifier).digest());
    const state = base64url(randomBytes(32));
    const nonce = base64url(randomBytes(32));
    const createdAt = Math.floor(Date.now() / 1000);
    const authorize = new URL(discovery.authorization_endpoint);
    for (const [key, value] of Object.entries({
        response_type: "code",
        client_id: CLIENT_ID,
        redirect_uri: REDIRECT_URI,
        scope: "openid profile email groups",
        state,
        nonce,
        code_challenge: challenge,
        code_challenge_method: "S256",
        prompt: "login",
        max_age: "0",
    })) {
        authorize.searchParams.set(key, value);
    }

    let callbackUrl;
    const pausedHandler = async (params, eventSessionId) => {
        if (
            callbackUrl
            || eventSessionId !== sessionId
            || !params.request?.url?.startsWith(REDIRECT_URI)
        ) {
            return;
        }
        callbackUrl = params.request.url;
        await client.send(
            "Fetch.failRequest",
            {requestId: params.requestId, errorReason: "Aborted"},
            sessionId
        );
    };
    client.on("Fetch.requestPaused", pausedHandler);
    await client.send("Page.navigate", {url: authorize.toString()}, sessionId);

    await waitFor(
        async () => callbackUrl || clickVisibleButton(
            client,
            sessionId,
            [
                "Authenticate",
                "Continue",
                "Log in",
                "Sign in",
                "Use passkey",
                "Use Passkey",
            ]
        ).catch(() => false),
        "Pocket ID authentication action",
        10000
    );
    await waitFor(() => callbackUrl, "Pocket ID authorization callback", 20000)
        .catch(() => undefined);
    if (!callbackUrl) {
        const diagnostic = await evaluate(
            client,
            sessionId,
            `({url: location.origin + location.pathname, text: (document.body?.innerText || "").slice(0, 700)})`
        );
        throw new Error(
            `Pocket ID authorization stalled at ${diagnostic.url}: ${diagnostic.text.replaceAll("\n", " | ")}`
        );
    }
    const callback = new URL(callbackUrl);
    if (callback.searchParams.get("state") !== state || !callback.searchParams.get("code")) {
        throw new Error("Pocket ID returned an invalid authorization callback");
    }
    if (assertedCount.value <= assertedCount.before) {
        throw new Error("A recent Pocket session bypassed the fresh virtual-passkey assertion");
    }

    const body = new URLSearchParams({
        grant_type: "authorization_code",
        code: callback.searchParams.get("code"),
        redirect_uri: REDIRECT_URI,
        code_verifier: verifier,
    });
    const credentials = Buffer.from(
        `${CLIENT_ID}:${env.POCKET_ID_SIGN_CLIENT_SECRET}`
    ).toString("base64");
    const response = await qaFetch(discovery.token_endpoint, {
        method: "POST",
        headers: {
            Accept: "application/json",
            Authorization: `Basic ${credentials}`,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body,
    });
    const tokens = await response.json();
    if (!response.ok || !tokens.id_token || tokens.refresh_token) {
        throw new Error(`Pocket ID token exchange failed with HTTP ${response.status}`);
    }
    const claims = await verifyIdToken(tokens.id_token, discovery, {nonce, createdAt});
    return {
        amr: claims.amr,
        authTime: claims.auth_time,
        subjectFingerprint: createHash("sha256").update(claims.sub).digest("hex"),
    };
}


async function fullStrongAcceptance({
    client,
    sessionId,
    authenticatorId,
    credentials,
    assertedCount,
    networkRequests,
    trackedSessions,
}) {
    const runId = randomBytes(5).toString("hex");
    const prepared = stackAcceptance("strong-acceptance-prepare", runId);
    await client.send("Fetch.disable", {}, sessionId);
    await client.send("Page.navigate", {url: prepared.invitation_url}, sessionId);
    await waitFor(
        () => evaluate(
            client,
            sessionId,
            `document.readyState === "complete" && Boolean(document.getElementById("usl_enroll_button"))`
        ),
        "Odoo Strong enrolment page",
        15000
    );
    const enrollmentUrl = new URL(prepared.invitation_url);
    const enrollmentBase = enrollmentUrl.pathname;
    const enrollmentBegin = await jsonRpc(
        client,
        sessionId,
        `${enrollmentBase}/begin`
    );
    await client.send("Page.navigate", {url: enrollmentBegin.authorization_url}, sessionId);
    await clickPocketAuthorization(
        client,
        sessionId,
        assertedCount,
        "Pocket ID enrolment"
    );

    const reviewed = stackAcceptance(
        "strong-acceptance-review",
        prepared.enrollment_id,
        runId
    );
    await client.send("Page.navigate", {url: reviewed.signing_url}, sessionId);
    await waitFor(
        () => evaluate(
            client,
            sessionId,
            `document.readyState === "complete"
                && Boolean(document.getElementById("usl_strong_sign_button"))`
        ),
        "Odoo Strong signing page",
        20000
    );

    const targetsBefore = new Set(
        (await client.send("Target.getTargets")).targetInfos.map((target) => target.targetId)
    );
    await evaluate(
        client,
        sessionId,
        `(() => {
            document.getElementById("usl_strong_consent").click();
            document.getElementById("usl_strong_sign_button").click();
            return true;
        })()`,
        {userGesture: true}
    );
    const popupTarget = await waitFor(
        async () => {
            const targets = (await client.send("Target.getTargets")).targetInfos;
            return targets.find(
                (target) => target.type === "page" && !targetsBefore.has(target.targetId)
            );
        },
        "Strong Pocket ID popup",
        20000
    ).catch(async (error) => {
        const targets = (await client.send("Target.getTargets")).targetInfos.map(
            ({targetId, type, url}) => ({
                isNew: !targetsBefore.has(targetId),
                type,
                url,
            })
        );
        const diagnostic = await evaluate(
            client,
            sessionId,
            `({
                buttonDisabled: document.getElementById("usl_strong_sign_button")?.disabled,
                status: document.getElementById("usl_strong_status")?.innerText,
                url: location.href,
            })`
        );
        throw new Error(
            `${error.message}; signer page=${JSON.stringify(diagnostic)}; targets=${JSON.stringify(targets)}`
        );
    });
    const popupAttachment = await client.send(
        "Target.attachToTarget",
        {targetId: popupTarget.targetId, flatten: true}
    );
    const popupSession = popupAttachment.sessionId;
    trackedSessions.add(popupSession);
    await Promise.all([
        client.send("Page.enable", {}, popupSession),
        client.send("Runtime.enable", {}, popupSession),
        client.send("Network.enable", {}, popupSession),
        client.send("WebAuthn.enable", {}, popupSession),
    ]);
    const popupAuthenticator = await client.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            options: {
                protocol: "ctap2",
                transport: "internal",
                hasResidentKey: true,
                hasUserVerification: true,
                isUserVerified: true,
                automaticPresenceSimulation: true,
            },
        },
        popupSession
    );
    for (const credential of credentials) {
        await client.send(
            "WebAuthn.addCredential",
            {authenticatorId: popupAuthenticator.authenticatorId, credential},
            popupSession
        );
    }
    const popupAssertions = {value: 0};
    client.on("WebAuthn.credentialAsserted", (_params, eventSessionId) => {
        if (eventSessionId === popupSession) {
            popupAssertions.value += 1;
        }
    });
    await waitFor(
        () => evaluate(
            client,
            popupSession,
            `document.readyState === "complete" && location.origin.includes("pocket-id-sign-qa")`
        ),
        "Pocket ID Strong authorization popup",
        20000
    );
    const popupCount = {value: 0};
    Object.defineProperty(popupCount, "value", {
        get: () => popupAssertions.value,
    });
    await clickPocketAuthorization(
        client,
        popupSession,
        popupCount,
        "Pocket ID Strong authorization"
    );
    await waitFor(
        () => evaluate(
            client,
            sessionId,
            `location.pathname === "/sign/result/success"`
        ),
        "completed Strong signing redirect",
        120000
    );
    await client.send(
        "WebAuthn.removeVirtualAuthenticator",
        {authenticatorId: popupAuthenticator.authenticatorId},
        popupSession
    ).catch(() => undefined);

    const forbidden = [
        /BEGIN (?:EC |RSA )?PRIVATE KEY/i,
        /private[_-]?jwk/i,
        /pkcs\s*#?8/i,
        /privateKey/i,
        /["']seed["']\s*:/i,
        /["']d["']\s*:/i,
    ];
    const requestPayloads = networkRequests
        .filter((entry) => entry.url.includes("/sign/") || entry.url.includes("pocket-id-sign"))
        .map((entry) => entry.postData || "")
        .join("\n");
    if (forbidden.some((pattern) => pattern.test(requestPayloads))) {
        throw new Error("Private key material appeared in Odoo or Pocket ID browser traffic");
    }
    const verified = stackAcceptance("strong-acceptance-verify", reviewed.request_id);
    return {
        archive_status: verified.archive_status,
        browser_private_material_detected: false,
        ceremony_completed: verified.checks.ceremony_completed,
        document_key_transport: "CSR and signature value only",
        evidence_complete: verified.checks.evidence_complete,
        oidc_validated: verified.checks.oidc_validated,
        passkey_assertions: popupAssertions.value,
        request_id: verified.request_id,
        state: verified.state,
        validation_engine: verified.validation_engine,
        validation_status: verified.validation_status,
    };
}


async function main() {
    const env = readEnv(await readFile(ENV_FILE, "utf8"));
    if (
        !env.POCKET_ID_SIGN_CLIENT_SECRET
        || !env.POCKET_ID_APP_URL
        || !env.ODOO_PUBLIC_BASE_URL
    ) {
        throw new Error("The isolated Pocket ID Sign client is not configured");
    }
    REDIRECT_URI = `${env.ODOO_PUBLIC_BASE_URL.replace(/\/$/, "")}/sign/pocketid/callback`;
    const discovery = await (
        await qaFetch(`${env.POCKET_ID_APP_URL}/.well-known/openid-configuration`)
    ).json();
    if (!discovery.fresh_passkey_reauthentication_supported) {
        throw new Error("Pocket ID does not advertise strict fresh-passkey support");
    }
    const onboardingOutput = execFileSync(
        "python3",
        [
            join(ROOT, "scripts/pocket_id_dev.py"),
            "--env-file",
            ENV_FILE,
            "one-time-link",
            "roger",
        ],
        {cwd: ROOT, encoding: "utf8"}
    );
    const onboardingUrl = onboardingOutput.match(/https?:\/\/\S+/)?.[0];
    if (!onboardingUrl) {
        throw new Error("Pocket ID did not return a one-time onboarding URL");
    }

    const profile = await mkdtemp(join(tmpdir(), "usl-sign-passkey-"));
    const chrome = spawn(
        CHROME,
        [
            "--headless=new",
            "--remote-debugging-port=0",
            `--user-data-dir=${profile}`,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-features=WebAuthenticationEnclaveAuthenticator",
            "--remote-allow-origins=*",
            `--unsafely-treat-insecure-origin-as-secure=${env.POCKET_ID_APP_URL},${env.ODOO_PUBLIC_BASE_URL}`,
            "about:blank",
        ],
        {stdio: ["ignore", "ignore", "pipe"]}
    );
    let chromeStderr = "";
    chrome.stderr.on("data", (chunk) => {
        chromeStderr = `${chromeStderr}${chunk}`.slice(-4000);
    });
    let client;
    let authenticatorId;
    try {
        const activePort = await waitFor(
            async () => {
                if (chrome.exitCode !== null) {
                    throw new Error(
                        `Chrome exited with ${chrome.exitCode}: ${chromeStderr.trim()}`
                    );
                }
                const raw = await readFile(join(profile, "DevToolsActivePort"), "utf8");
                return raw.trim();
            },
            "Chrome DevTools port"
        );
        const [port, browserPath] = activePort.split("\n");
        client = new CDPClient(`ws://127.0.0.1:${port}${browserPath}`);
        await client.ready;
        const {targetId} = await client.send("Target.createTarget", {url: onboardingUrl});
        const attached = await client.send(
            "Target.attachToTarget",
            {targetId, flatten: true}
        );
        const sessionId = attached.sessionId;
        const networkRequests = [];
        const trackedSessions = new Set([sessionId]);
        client.on("Network.requestWillBeSent", (params, eventSessionId) => {
            if (trackedSessions.has(eventSessionId)) {
                networkRequests.push({
                    url: params.request?.url || "",
                    postData: params.request?.postData || "",
                });
            }
        });
        await Promise.all([
            client.send("Page.enable", {}, sessionId),
            client.send("Runtime.enable", {}, sessionId),
            client.send("Network.enable", {}, sessionId),
            client.send(
                "Fetch.enable",
                {patterns: [{urlPattern: `${REDIRECT_URI}*`, requestStage: "Request"}]},
                sessionId
            ),
            client.send("WebAuthn.enable", {}, sessionId),
        ]);
        ({authenticatorId} = await client.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                options: {
                    protocol: "ctap2",
                    transport: "internal",
                    hasResidentKey: true,
                    hasUserVerification: true,
                    isUserVerified: true,
                    automaticPresenceSimulation: true,
                },
            },
            sessionId
        ));
        const assertedCount = {value: 0, before: 0};
        client.on("WebAuthn.credentialAsserted", (_params, eventSessionId) => {
            if (eventSessionId === sessionId) {
                assertedCount.value += 1;
            }
        });
        await waitFor(
            () => evaluate(
                client,
                sessionId,
                `document.readyState === "complete" && location.pathname === "/settings/account"`
            ),
            "Pocket ID account onboarding"
        );
        const credentials = await registerPasskey(client, sessionId, authenticatorId);
        const fullAcceptance = process.env.USL_SIGN_FULL_ACCEPTANCE === "1";
        if (fullAcceptance) {
            const strongAcceptance = await fullStrongAcceptance({
                client,
                sessionId,
                authenticatorId,
                credentials,
                assertedCount,
                networkRequests,
                trackedSessions,
            });
            console.log(JSON.stringify({
                    credential_count: credentials.length,
                    strict_capability: true,
                    strong_acceptance: strongAcceptance,
                }, null, 2));
        } else {
            assertedCount.before = assertedCount.value;
            const first = await authorizeFreshPasskey(
                client, sessionId, discovery, env, assertedCount
            );
            const firstAssertionCount = assertedCount.value;
            assertedCount.before = assertedCount.value;
            const second = await authorizeFreshPasskey(
                client, sessionId, discovery, env, assertedCount
            );
            const secondAssertionCount = assertedCount.value - firstAssertionCount;
            if (firstAssertionCount < 1 || secondAssertionCount < 1) {
                throw new Error("Every strict authorization must invoke the passkey");
            }
            console.log(JSON.stringify({
                credential_count: credentials.length,
                first_authorization: first,
                first_passkey_assertions: firstAssertionCount,
                recent_session_bypass_rejected: true,
                second_authorization: second,
                second_passkey_assertions: secondAssertionCount,
                strict_capability: true,
            }, null, 2));
        }
    } finally {
        if (client && authenticatorId) {
            await client.send(
                "WebAuthn.removeVirtualAuthenticator",
                {authenticatorId}
            ).catch(() => undefined);
        }
        if (client) {
            await client.send("Browser.close").catch(() => undefined);
        } else {
            chrome.kill("SIGTERM");
        }
        await sleep(200);
        await rm(profile, {recursive: true, force: true});
    }
}


main().catch((error) => {
    console.error(`Pocket ID virtual-authenticator acceptance failed: ${error.message}`);
    process.exitCode = 1;
});
