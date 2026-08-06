async function rpc(route, params) {
    const response = await fetch(route, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({jsonrpc: "2.0", method: "call", params}),
    });
    const payload = await response.json();
    if (!response.ok || payload.error) {
        throw new Error(payload.error?.data?.message || payload.error?.message || "The secure operation failed.");
    }
    return payload.result;
}

function decodeBase64Url(value) {
    const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function encodeBase64Url(value) {
    let binary = "";
    for (const byte of new Uint8Array(value)) {
        binary += String.fromCharCode(byte);
    }
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function publicKeyOptions(options) {
    const result = {...options, challenge: decodeBase64Url(options.challenge)};
    if (result.user?.id) {
        result.user = {...result.user, id: decodeBase64Url(result.user.id)};
    }
    for (const key of ["allowCredentials", "excludeCredentials"]) {
        if (result[key]) {
            result[key] = result[key].map((credential) => ({
                ...credential,
                id: decodeBase64Url(credential.id),
            }));
        }
    }
    return result;
}

function serializeCredential(credential) {
    const response = credential.response;
    const result = {
        id: credential.id,
        rawId: encodeBase64Url(credential.rawId),
        type: credential.type,
        authenticatorAttachment: credential.authenticatorAttachment,
        clientExtensionResults: credential.getClientExtensionResults(),
        response: {
            clientDataJSON: encodeBase64Url(response.clientDataJSON),
        },
    };
    for (const key of [
        "attestationObject",
        "authenticatorData",
        "signature",
        "userHandle",
    ]) {
        if (response[key]) {
            result.response[key] = encodeBase64Url(response[key]);
        }
    }
    if (response.getTransports) {
        result.response.transports = response.getTransports();
    }
    return result;
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
            promise.reject(new Error(event.data.error));
        }
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

async function enroll(container) {
    const button = document.getElementById("usl_enroll_button");
    const status = document.getElementById("usl_enroll_status");
    button.addEventListener("click", async () => {
        button.disabled = true;
        status.className = "mt-3 alert alert-info";
        status.textContent = "Waiting for your passkey…";
        try {
            const base = `/sign/enroll/${container.dataset.enrollmentId}/${container.dataset.enrollmentToken}`;
            const options = await rpc(`${base}/begin`, {});
            const credential = await navigator.credentials.create({
                publicKey: publicKeyOptions(options),
            });
            const result = await rpc(`${base}/complete`, {
                credential: serializeCredential(credential),
                name: document.getElementById("usl_passkey_name").value,
                transports: credential.response.getTransports?.() || [],
            });
            status.className = "mt-3 alert alert-success";
            status.textContent = result.recovery_ready
                ? "Passkey registered. Recovery is ready."
                : "Passkey registered. Add a second recovery passkey when possible.";
        } catch (error) {
            status.className = "mt-3 alert alert-danger";
            status.textContent = error.message || "Passkey registration failed.";
        } finally {
            button.disabled = false;
        }
    });
    status.textContent = "";
    button.disabled = false;
    container.dataset.ready = "true";
}

async function strongSign(container) {
    const button = document.getElementById("usl_strong_sign_button");
    const status = document.getElementById("usl_strong_status");
    button.addEventListener("click", async () => {
        if (!document.getElementById("usl_strong_consent").checked) {
            status.className = "mt-3 alert alert-warning";
            status.textContent = "Review the document and confirm your consent first.";
            return;
        }
        button.disabled = true;
        const ceremonyWorker = workerClient();
        try {
            status.className = "mt-3 alert alert-info";
            status.textContent = "Creating a one-use document key in this browser…";
            const generated = await ceremonyWorker.call("generate", {
                commonName: container.dataset.certificateSubject,
            });
            const base = `/sign/strong/${container.dataset.signerId}/${container.dataset.accessToken}`;
            const begin = await rpc(`${base}/begin`, {
                csr_pem: generated.csrPem,
                consent: true,
            });
            status.textContent = "Verify with your passkey…";
            const credential = await navigator.credentials.get({
                publicKey: publicKeyOptions(begin.options),
            });
            const authorization = await rpc(`${base}/authorize`, {
                ceremony_id: begin.ceremony_id,
                credential: serializeCredential(credential),
            });
            status.textContent = "Applying and independently validating your personal PAdES signature…";
            const signed = await ceremonyWorker.call("sign", {
                dataToSign: authorization.data_to_sign,
            });
            const result = await rpc(`${base}/finalize`, {
                ceremony_id: begin.ceremony_id,
                signature: signed.signature,
            });
            await ceremonyWorker.call("destroy");
            window.location.assign(result.redirect);
        } catch (error) {
            ceremonyWorker.worker.terminate();
            status.className = "mt-3 alert alert-danger";
            status.textContent = error.message || "The strong signature could not be completed.";
            button.disabled = false;
        }
    });
    status.textContent = "";
    button.disabled = false;
    container.dataset.ready = "true";
}

document.addEventListener("DOMContentLoaded", () => {
    const enrollment = document.getElementById("usl_strong_enrollment");
    const signing = document.getElementById("usl_strong_sign");
    if (!window.isSecureContext || !window.PublicKeyCredential || !window.crypto?.subtle) {
        const target = document.getElementById("usl_enroll_status") || document.getElementById("usl_strong_status");
        const action = document.getElementById("usl_enroll_button") || document.getElementById("usl_strong_sign_button");
        if (action) {
            action.disabled = true;
        }
        if (target) {
            target.className = "mt-3 alert alert-danger";
            target.textContent = "This browser cannot perform the secure passkey ceremony. Use a current passkey-capable browser on a trusted device, or contact the sender to choose another permitted journey.";
        }
        return;
    }
    if (enrollment) {
        enroll(enrollment);
    }
    if (signing) {
        strongSign(signing);
    }
});
