(function () {
    "use strict";

    const TERMINAL_FAILURES = new Set(["failed", "expired", "revoked"]);
    const PHASE_STEPS = {
        review: "review",
        preparing: "identity",
        identity: "identity",
        signing: "complete",
        validating: "complete",
        success: "complete",
        error: null,
    };

    class JourneyError extends Error {
        constructor(code) {
            super(code);
            this.code = code;
        }
    }

    async function rpc(route, params) {
        let response;
        try {
            response = await fetch(route, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({jsonrpc: "2.0", method: "call", params}),
            });
        } catch (_error) {
            throw new JourneyError("network");
        }
        let payload;
        try {
            payload = await response.json();
        } catch (_error) {
            throw new JourneyError("service");
        }
        if (!response.ok || payload.error) {
            throw new JourneyError("service");
        }
        return payload.result;
    }

    function workerClient() {
        const worker = new Worker("/usl_sign/static/lib/strong_sign_worker.js");
        let sequence = 0;
        const pending = new Map();
        worker.addEventListener("message", (event) => {
            const promise = pending.get(event.data.id);
            if (!promise) {
                return;
            }
            pending.delete(event.data.id);
            if (event.data.ok) {
                promise.resolve(event.data);
            } else {
                promise.reject(new JourneyError("browser_key"));
            }
        });
        worker.addEventListener("error", () => {
            for (const promise of pending.values()) {
                promise.reject(new JourneyError("browser_key"));
            }
            pending.clear();
        });
        return {
            worker,
            call(command, payload = {}) {
                const id = ++sequence;
                return new Promise((resolve, reject) => {
                    pending.set(id, {resolve, reject});
                    worker.postMessage({id, command, payload});
                });
            },
        };
    }

    function delay(milliseconds) {
        return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    }

    function openPocketID() {
        const popup = window.open(
            "about:blank",
            "usl_sign_pocketid",
            "popup=yes,width=520,height=760,resizable=yes,scrollbars=yes"
        );
        if (!popup) {
            throw new JourneyError("popup_blocked");
        }
        return popup;
    }

    function navigatePocketID(popup, url) {
        if (!popup || popup.closed) {
            throw new JourneyError("popup_closed");
        }
        popup.location.replace(url);
    }

    function updateProgress(container, phase) {
        const activeStep = PHASE_STEPS[phase];
        const order = ["review", "identity", "complete"];
        const activeIndex = order.indexOf(activeStep);
        for (const step of container.querySelectorAll("[data-step]")) {
            const stepIndex = order.indexOf(step.dataset.step);
            step.classList.toggle("is-current", step.dataset.step === activeStep);
            step.classList.toggle(
                "is-complete",
                phase === "success" || (activeIndex >= 0 && stepIndex < activeIndex)
            );
        }
    }

    function setPhase(container, phase, {tone = "info", title = "", message = ""} = {}) {
        container.dataset.phase = phase;
        updateProgress(container, phase);
        const status = container.querySelector(".usl-sign-inline-status");
        if (!status) {
            return;
        }
        status.hidden = !title && !message;
        status.className = "usl-sign-inline-status";
        if (tone !== "info") {
            status.classList.add(`usl-sign-inline-status--${tone}`);
        }
        const titleNode = status.querySelector("[data-status-title]");
        const messageNode = status.querySelector("[data-status-message]");
        if (titleNode) {
            titleNode.textContent = title;
        }
        if (messageNode) {
            messageNode.textContent = message;
        }
        const icon = status.querySelector(".usl-sign-status-icon .fa");
        if (icon) {
            icon.className = `fa ${
                tone === "success"
                    ? "fa-check-circle"
                    : tone === "danger"
                      ? "fa-exclamation-circle"
                      : tone === "warning"
                        ? "fa-info-circle"
                        : "fa-circle-o-notch"
            }`;
        }
    }

    function setButtonBusy(button, busy, label) {
        button.disabled = busy;
        button.setAttribute("aria-busy", busy ? "true" : "false");
        const labelNode = button.querySelector(".usl-sign-button__label");
        if (labelNode && label) {
            labelNode.textContent = label;
        }
    }

    function friendlyFailure(error, journey) {
        const code = error instanceof JourneyError ? error.code : "service";
        const failures = {
            popup_blocked: {
                title: "Allow the Pocket ID window",
                message: "Your browser blocked it. Allow pop-ups for this page, then try again.",
            },
            popup_closed: {
                title: "Pocket ID was closed",
                message: "Nothing was signed. Try again when you’re ready to confirm your passkey.",
            },
            cancelled: {
                title: "Confirmation cancelled",
                message: "Nothing was signed or changed. You can start again whenever you’re ready.",
            },
            timeout: {
                title: "Time ran out",
                message: "Nothing was signed. Try again when you’re ready.",
            },
            network: {
                title: "Connection interrupted",
                message: "Check your connection, then try again.",
            },
            browser_key: {
                title: "This browser could not prepare the signature",
                message: "Reload the page in a current browser, or ask the sender for another signing option.",
            },
            service: {
                title: journey === "enrollment" ? "Account not connected" : "Signature not completed",
                message:
                    journey === "enrollment"
                        ? "Pocket ID could not be connected. Try again or contact the sender."
                        : "Your signature could not be completed. Reload the document and try again.",
            },
        };
        return failures[code] || failures.service;
    }

    async function poll(route, params, acceptedStates, timeoutSeconds, isCancelled) {
        const deadline = Date.now() + timeoutSeconds * 1000;
        while (Date.now() < deadline) {
            if (isCancelled?.()) {
                throw new JourneyError("cancelled");
            }
            const result = await rpc(route, params);
            if (acceptedStates.includes(result.state)) {
                return result;
            }
            if (TERMINAL_FAILURES.has(result.state)) {
                throw new JourneyError(result.state === "expired" ? "timeout" : "service");
            }
            await delay(900);
        }
        throw new JourneyError("timeout");
    }

    async function safelyCancel(base, ceremonyId) {
        if (!ceremonyId) {
            return;
        }
        try {
            await rpc(`${base}/cancel`, {ceremony_id: ceremonyId});
        } catch (_error) {
            // Cancellation is best-effort recovery. Server expiry still closes
            // abandoned one-use material without accepting a signature.
        }
    }

    async function safelyDestroy(client) {
        if (!client) {
            return;
        }
        try {
            await client.call("destroy");
        } catch (_error) {
            // Terminating the dedicated worker is the final fail-closed cleanup.
        }
        client.worker.terminate();
    }

    function installUnloadGuard(isActive) {
        window.addEventListener("beforeunload", (event) => {
            if (!isActive()) {
                return;
            }
            event.preventDefault();
            event.returnValue = "";
        });
    }

    function initializeEnrollment(container) {
        const button = document.getElementById("usl_enroll_button");
        const cancelButton = container.querySelector("[data-cancel-attempt]");
        let active = false;
        let cancelled = false;
        let popup;
        installUnloadGuard(() => active);
        cancelButton?.addEventListener("click", () => {
            cancelled = true;
            popup?.close();
        });
        button.addEventListener("click", async () => {
            cancelled = false;
            active = true;
            cancelButton.hidden = false;
            try {
                // Open synchronously from the user gesture so normal browser
                // popup protection recognises the signer’s explicit action.
                popup = openPocketID();
                setButtonBusy(button, true, "Connecting…");
                setPhase(container, "preparing", {
                    title: "Opening Pocket ID",
                    message: "Keep this page open while you confirm your account.",
                });
                const base = `/sign/enroll/${container.dataset.enrollmentId}/${container.dataset.enrollmentToken}`;
                const started = await rpc(`${base}/begin`, {});
                navigatePocketID(popup, started.authorization_url);
                setPhase(container, "identity", {
                    title: "Confirm in Pocket ID",
                    message: "Use your passkey in the Pocket ID window.",
                });
                const result = await poll(
                    `${base}/status`,
                    {},
                    ["pending_review", "active"],
                    started.expires_in || 300,
                    () => cancelled
                );
                popup?.close();
                const ready = result.state === "active";
                setPhase(container, ready ? "success" : "identity", {
                    tone: "success",
                    title: ready ? "Ready for strong signatures" : "Account connected",
                    message: ready
                        ? "Your reviewed identity is active."
                        : "The company will review this identity before strong signing is enabled.",
                });
                setButtonBusy(button, true, ready ? "Identity ready" : "Awaiting review");
            } catch (error) {
                popup?.close();
                const failure = friendlyFailure(error, "enrollment");
                setPhase(container, "error", {tone: "danger", ...failure});
                setButtonBusy(button, false, "Try again");
            } finally {
                active = false;
                cancelButton.hidden = true;
            }
        });
        setPhase(container, "review");
        setButtonBusy(button, false, "Connect Pocket ID");
        container.dataset.ready = "true";
    }

    function initializeStrongSigning(container) {
        const button = document.getElementById("usl_strong_sign_button");
        const consent = document.getElementById("usl_strong_consent");
        const cancelButton = container.querySelector("[data-cancel-attempt]");
        let active = false;
        let cancelled = false;
        let popup;
        installUnloadGuard(() => active);
        cancelButton?.addEventListener("click", () => {
            cancelled = true;
            popup?.close();
        });

        consent.addEventListener("change", () => {
            if (!active) {
                button.disabled = !consent.checked;
            }
        });

        button.addEventListener("click", async () => {
            if (!consent.checked || active) {
                consent.focus();
                return;
            }
            let ceremonyWorker;
            let ceremonyId;
            let finalized = false;
            const base = `/sign/strong/${container.dataset.signerId}/${container.dataset.accessToken}`;
            cancelled = false;
            active = true;
            cancelButton.hidden = false;
            try {
                popup = openPocketID();
                setButtonBusy(button, true, "Preparing…");
                setPhase(container, "preparing", {
                    title: "Preparing your signature",
                    message: "Keep this page open.",
                });
                ceremonyWorker = workerClient();
                const generated = await ceremonyWorker.call("generate", {
                    commonName: container.dataset.certificateSubject,
                });
                const begin = await rpc(`${base}/begin`, {
                    csr_pem: generated.csrPem,
                    consent: true,
                });
                ceremonyId = begin.ceremony_id;
                navigatePocketID(popup, begin.authorization_url);
                setButtonBusy(button, true, "Waiting for Pocket ID…");
                setPhase(container, "identity", {
                    title: "Confirm in Pocket ID",
                    message: "Use your passkey in the Pocket ID window.",
                });
                const authorization = await poll(
                    `${base}/status`,
                    {ceremony_id: ceremonyId},
                    ["authorized", "completed"],
                    begin.expires_in,
                    () => cancelled
                );
                popup?.close();
                if (authorization.state === "completed") {
                    window.location.assign(authorization.redirect || "/sign/result/success");
                    return;
                }
                setButtonBusy(button, true, "Applying signature…");
                setPhase(container, "signing", {
                    title: "Adding your signature",
                    message: "This usually takes only a moment.",
                });
                const signed = await ceremonyWorker.call("sign", {
                    dataToSign: authorization.data_to_sign,
                });
                setButtonBusy(button, true, "Validating…");
                setPhase(container, "validating", {
                    title: "Checking the result",
                    message: "Please keep this page open.",
                });
                let result;
                try {
                    result = await rpc(`${base}/finalize`, {
                        ceremony_id: ceremonyId,
                        signature: signed.signature,
                    });
                    finalized = true;
                } catch (finalizeError) {
                    // A response can be lost after the server safely commits.
                    // Ask for the authoritative ceremony state before offering retry.
                    const recovered = await rpc(`${base}/status`, {ceremony_id: ceremonyId});
                    if (recovered.state !== "completed") {
                        throw finalizeError;
                    }
                    finalized = true;
                    result = {redirect: recovered.redirect || "/sign/result/success"};
                }
                await safelyDestroy(ceremonyWorker);
                ceremonyWorker = null;
                setPhase(container, "success", {
                    tone: "success",
                    title: "Signed",
                    message: "Your signature has been saved.",
                });
                setButtonBusy(button, true, "Signed");
                await delay(450);
                active = false;
                window.location.assign(result.redirect);
            } catch (error) {
                popup?.close();
                if (!finalized) {
                    await safelyCancel(base, ceremonyId);
                }
                await safelyDestroy(ceremonyWorker);
                ceremonyWorker = null;
                const failure = friendlyFailure(error, "signing");
                setPhase(container, "error", {tone: "danger", ...failure});
                setButtonBusy(button, false, "Try again");
                button.disabled = !consent.checked;
            } finally {
                active = false;
                cancelButton.hidden = true;
            }
        });

        setPhase(container, "review");
        setButtonBusy(button, false, "Sign with Pocket ID");
        button.disabled = !consent.checked;
        container.dataset.ready = "true";
    }

    function initializeCallback(container) {
        const successful = container.dataset.successful === "true";
        const close = () => window.close();
        document.getElementById("usl_callback_close")?.addEventListener("click", close);
        try {
            window.opener?.postMessage(
                {type: "usl-sign-pocketid-result", successful},
                window.location.origin
            );
        } catch (_error) {
            // Polling on the signing page remains authoritative.
        }
        window.setTimeout(close, successful ? 750 : 1800);
    }

    function initialize() {
        const enrollment = document.getElementById("usl_strong_enrollment");
        const signing = document.getElementById("usl_strong_sign");
        const callback = document.getElementById("usl_pocketid_callback");
        if (callback) {
            initializeCallback(callback);
            return;
        }
        if (!enrollment && !signing) {
            return;
        }
        if (!window.isSecureContext || !window.crypto?.subtle || (signing && !window.Worker)) {
            const container = enrollment || signing;
            const action = container.querySelector(".usl-sign-button");
            action.disabled = true;
            setPhase(container, "error", {
                tone: "danger",
                title: "A secure browser is required",
                message: "Open this page over HTTPS in a current browser, or ask the sender for another signing option.",
            });
            return;
        }
        if (enrollment) {
            initializeEnrollment(enrollment);
        }
        if (signing) {
            initializeStrongSigning(signing);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, {once: true});
    } else {
        initialize();
    }
})();
