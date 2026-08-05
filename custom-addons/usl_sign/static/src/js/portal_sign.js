/** @odoo-module **/

document.addEventListener("DOMContentLoaded", () => {
    const container = document.getElementById("usl_yousign_iframe");
    if (!container) {
        return;
    }
    let signatureLink = container.dataset.signatureLink;
    if (container.dataset.sandbox === "1") {
        const separator = signatureLink.includes("?") ? "&" : "?";
        signatureLink += `${separator}disable_domain_validation=true`;
    }
    const yousign = new window.Yousign(signatureLink, container.id);
    const report = async (event) => {
        const response = await fetch(container.dataset.statusUrl, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                jsonrpc: "2.0",
                method: "call",
                params: {event},
                id: Date.now(),
            }),
        });
        if (response.ok) {
            const status = event === "declined" ? "declined" : event === "error" ? "error" : "success";
            window.location.assign(`${container.dataset.resultUrl}/${status}`);
        }
    };
    yousign.onSuccess(() => report("success"));
    yousign.onSignatureDone(() => report("signature.done"));
    yousign.onDeclined(() => report("declined"));
    yousign.onError(() => report("error"));
});
