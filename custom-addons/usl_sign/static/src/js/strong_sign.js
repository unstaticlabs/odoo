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
        constructor(code, detail = null) {
            super(code);
            this.code = code;
            this.detail = detail;
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
        const finishLabel = container.querySelector("[data-finish-label]");
        if (finishLabel) {
            finishLabel.textContent = phase === "success"
                ? finishLabel.dataset.finishComplete
                : finishLabel.dataset.finishPending;
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
                message: "Nothing was signed. Try again when you’re ready.",
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
            certificate_service: {
                title: "The signature could not be prepared",
                message: "Nothing was signed. Try again, or contact the sender if this keeps happening.",
            },
            signature_service: {
                title: "The signature could not be checked",
                message: "Nothing was accepted. Try again, or contact the sender if this keeps happening.",
            },
            identity_check: {
                title: "This identity could not be confirmed",
                message: "Nothing was signed. Return to the document and start again.",
            },
            pocket_id_rejected: {
                title: "Pocket ID did not confirm this attempt",
                message: "Nothing was signed or changed. You can try again when you’re ready.",
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
                throw new JourneyError(
                    result.state === "expired" ? "timeout" : result.failure_code || "service",
                    result.failure_code || null
                );
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
        let callbackFailed = false;
        installUnloadGuard(() => active);
        window.addEventListener("message", (event) => {
            if (
                event.origin === window.location.origin &&
                event.data?.type === "usl-sign-pocketid-result"
            ) {
                callbackFailed = event.data.successful === false;
            }
        });
        cancelButton?.addEventListener("click", () => {
            cancelled = true;
            popup?.close();
        });
        button.addEventListener("click", async () => {
            cancelled = false;
            callbackFailed = false;
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
                    message: "Follow the prompt in the Pocket ID window.",
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
                    title: ready ? "Ready for strong signatures" : "Pocket ID connected",
                    message: ready
                        ? "Your reviewed identity is active. You can now complete Strong personal signature requests."
                        : `${container.dataset.companyName} will now confirm that this account belongs to you. You can close this page; after approval, you can complete Strong personal signature requests.`,
                });
                setButtonBusy(button, false, ready ? "Identity ready" : "Setup complete");
                button.hidden = true;
                container.querySelector(".usl-sign-action-note")?.setAttribute("hidden", "");
            } catch (error) {
                if (!callbackFailed) {
                    popup?.close();
                }
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

    function setPortalStatus(title, message, tone = "info") {
        const status = document.getElementById("usl_sign_submission_status");
        if (!status) {
            return;
        }
        status.classList.remove("d-none");
        const icon = status.querySelector(".fa");
        if (icon) {
            icon.className = `fa ${
                tone === "success"
                    ? "fa-check-circle"
                    : tone === "danger"
                      ? "fa-exclamation-circle"
                      : "fa-circle-o-notch fa-spin"
            }`;
        }
        const text = status.querySelector("span");
        if (text) {
            text.textContent = `${title} ${message}`.trim();
        }
    }

    async function submitPortalStrongSignature({
        button,
        items,
        documentSha256,
        location,
        browserContext,
    }) {
        const context = document.getElementById("usl_strong_sign_context");
        if (!context || context.dataset.active === "true") {
            throw new JourneyError("service");
        }
        if (!window.isSecureContext || !window.crypto?.subtle || !window.Worker) {
            throw new JourneyError("browser_key");
        }
        let ceremonyWorker;
        let ceremonyId;
        let popup;
        let finalized = false;
        let callbackFailed = false;
        const base = `/sign/strong/${context.dataset.signerId}/${context.dataset.accessToken}`;
        const onCallback = (event) => {
            if (
                event.origin === window.location.origin &&
                event.data?.type === "usl-sign-pocketid-result"
            ) {
                callbackFailed = event.data.successful === false;
            }
        };
        context.dataset.active = "true";
        window.addEventListener("message", onCallback);
        try {
            popup = openPocketID();
            setButtonBusy(button, true, "Preparing…");
            setPortalStatus("Preparing your personal signature.", "Keep this tab open.");
            ceremonyWorker = workerClient();
            const generated = await ceremonyWorker.call("generate", {
                commonName: context.dataset.certificateSubject,
            });
            const begin = await rpc(`${base}/begin`, {
                csr_pem: generated.csrPem,
                consent: true,
                items,
                document_sha256: documentSha256,
                location,
                browser_context: browserContext,
            });
            ceremonyId = begin.ceremony_id;
            navigatePocketID(popup, begin.authorization_url);
            setButtonBusy(button, true, "Waiting for Pocket ID…");
            setPortalStatus(
                "Confirm in Pocket ID.",
                "The document stays here; Pocket ID confirms your account."
            );
            const authorization = await poll(
                `${base}/status`,
                {ceremony_id: ceremonyId},
                ["authorized", "completed"],
                begin.expires_in,
                () => false
            );
            popup?.close();
            if (authorization.state === "completed") {
                context.dataset.active = "false";
                window.location.assign(authorization.redirect || "/sign/result/success");
                return;
            }
            setButtonBusy(button, true, "Applying signature…");
            setPortalStatus(
                "Applying your signature.",
                "The result will be validated before it is accepted."
            );
            const signed = await ceremonyWorker.call("sign", {
                dataToSign: authorization.data_to_sign,
            });
            setButtonBusy(button, true, "Validating…");
            setPortalStatus("Validating the signed revision.", "Please keep this tab open.");
            let result;
            try {
                result = await rpc(`${base}/finalize`, {
                    ceremony_id: ceremonyId,
                    signature: signed.signature,
                });
                finalized = true;
            } catch (finalizeError) {
                const recovered = await rpc(`${base}/status`, {ceremony_id: ceremonyId});
                if (recovered.state !== "completed") {
                    throw finalizeError;
                }
                finalized = true;
                result = {redirect: recovered.redirect || "/sign/result/success"};
            }
            await safelyDestroy(ceremonyWorker);
            ceremonyWorker = null;
            setPortalStatus("Signed.", "Opening your result…", "success");
            context.dataset.active = "false";
            window.location.assign(result.redirect);
        } catch (error) {
            if (!callbackFailed) {
                popup?.close();
            }
            if (!finalized) {
                await safelyCancel(base, ceremonyId);
            }
            await safelyDestroy(ceremonyWorker);
            const failure = friendlyFailure(error, "signing");
            setPortalStatus(failure.title, failure.message, "danger");
            const publicError = new JourneyError(error.code || "service");
            publicError.message = `${failure.title}. ${failure.message}`;
            throw publicError;
        } finally {
            context.dataset.active = "false";
            window.removeEventListener("message", onCallback);
        }
    }

    window.uslStrongSign = submitPortalStrongSignature;

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
        if (successful) {
            window.setTimeout(close, 900);
        }
    }

    function initialize() {
        const enrollment = document.getElementById("usl_strong_enrollment");
        const portalSigning = document.getElementById("usl_strong_sign_context");
        const callback = document.getElementById("usl_pocketid_callback");
        if (callback) {
            initializeCallback(callback);
            return;
        }
        if (!enrollment && !portalSigning) {
            return;
        }
        if (enrollment) {
            initializeEnrollment(enrollment);
        }
        if (portalSigning) {
            installUnloadGuard(() => portalSigning.dataset.active === "true");
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, {once: true});
    } else {
        initialize();
    }
})();
