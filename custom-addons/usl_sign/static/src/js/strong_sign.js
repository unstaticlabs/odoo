async function rpc(route, params) {
    const response = await fetch(route, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({jsonrpc: "2.0", method: "call", params}),
    });
    const payload = await response.json();
    if (!response.ok || payload.error) {
        throw new Error(
            payload.error?.data?.message ||
                payload.error?.message ||
                "The secure operation failed."
        );
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

function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function openPocketID(url) {
    const popup = window.open(
        url,
        "usl_sign_pocketid",
        "popup=yes,width=520,height=760,resizable=yes,scrollbars=yes"
    );
    if (!popup) {
        throw new Error(
            "Your browser blocked the Pocket ID window. Allow pop-ups for this page and try again."
        );
    }
    return popup;
}

async function poll(route, params, acceptedStates, timeoutSeconds) {
    const deadline = Date.now() + timeoutSeconds * 1000;
    while (Date.now() < deadline) {
        const result = await rpc(route, params);
        if (acceptedStates.includes(result.state)) {
            return result;
        }
        if (["failed", "expired", "revoked"].includes(result.state)) {
            throw new Error(
                result.failure_code
                    ? `Authorization stopped (${result.failure_code}). Start again.`
                    : "Authorization stopped. Start again."
            );
        }
        await delay(1000);
    }
    throw new Error("Pocket ID authorization timed out. Start again.");
}

async function enroll(container) {
    const button = document.getElementById("usl_enroll_button");
    const status = document.getElementById("usl_enroll_status");
    button.addEventListener("click", async () => {
        button.disabled = true;
        status.className = "mt-3 alert alert-info";
        status.textContent = "Opening Pocket ID…";
        let popup;
        try {
            const base = `/sign/enroll/${container.dataset.enrollmentId}/${container.dataset.enrollmentToken}`;
            const started = await rpc(`${base}/begin`, {});
            popup = openPocketID(started.authorization_url);
            status.textContent = "Use your Pocket ID passkey in the new window.";
            const result = await poll(`${base}/status`, {}, ["pending_review", "active"], 300);
            popup?.close();
            status.className = "mt-3 alert alert-success";
            status.textContent = result.display_name
                ? `Pocket ID connected as ${result.display_name}. An identity reviewer must now confirm the enrolment.`
                : "Pocket ID connected. An identity reviewer must now confirm the enrolment.";
        } catch (error) {
            popup?.close();
            status.className = "mt-3 alert alert-danger";
            status.textContent = error.message || "Pocket ID connection failed.";
            button.disabled = false;
        }
    });
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
        let popup;
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
            popup = openPocketID(begin.authorization_url);
            status.textContent = "Use your Pocket ID passkey in the new window.";
            const authorization = await poll(
                `${base}/status`,
                {ceremony_id: begin.ceremony_id},
                ["authorized"],
                begin.expires_in
            );
            popup?.close();
            status.textContent =
                "Applying and independently validating your personal PAdES signature…";
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
            popup?.close();
            ceremonyWorker.worker.terminate();
            status.className = "mt-3 alert alert-danger";
            status.textContent =
                error.message || "The strong signature could not be completed.";
            button.disabled = false;
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const enrollment = document.getElementById("usl_strong_enrollment");
    const signing = document.getElementById("usl_strong_sign");
    if (!window.isSecureContext || !window.crypto?.subtle || (signing && !window.Worker)) {
        const target =
            document.getElementById("usl_enroll_status") ||
            document.getElementById("usl_strong_status");
        const action =
            document.getElementById("usl_enroll_button") ||
            document.getElementById("usl_strong_sign_button");
        if (action) {
            action.disabled = true;
        }
        if (target) {
            target.className = "mt-3 alert alert-danger";
            target.textContent =
                "Use a current browser in a secure HTTPS session, or ask the sender for another permitted journey.";
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
