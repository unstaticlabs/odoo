import "reflect-metadata";
import * as x509 from "@peculiar/x509";

x509.cryptoProvider.set(globalThis.crypto);

let keyPair = null;

function bytesToBase64(bytes) {
    let binary = "";
    for (const byte of new Uint8Array(bytes)) {
        binary += String.fromCharCode(byte);
    }
    return btoa(binary);
}

function base64ToBytes(value) {
    const binary = atob(value);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function derInteger(raw) {
    let bytes = Array.from(raw);
    while (bytes.length > 1 && bytes[0] === 0 && !(bytes[1] & 0x80)) {
        bytes.shift();
    }
    if (bytes[0] & 0x80) {
        bytes.unshift(0);
    }
    return Uint8Array.from([0x02, bytes.length, ...bytes]);
}

function ecdsaRawToDer(raw) {
    if (raw.length !== 64) {
        return raw;
    }
    const r = derInteger(raw.slice(0, 32));
    const s = derInteger(raw.slice(32));
    return Uint8Array.from([0x30, r.length + s.length, ...r, ...s]);
}

self.addEventListener("message", async (event) => {
    const {id, command, payload = {}} = event.data;
    try {
        if (command === "generate") {
            keyPair = await crypto.subtle.generateKey(
                {name: "ECDSA", namedCurve: "P-256"},
                false,
                ["sign", "verify"]
            );
            const request = await x509.Pkcs10CertificateRequestGenerator.create({
                name: `CN=${String(payload.commonName || "USL Signer").replaceAll(",", " ")}`,
                keys: keyPair,
                signingAlgorithm: {name: "ECDSA", hash: "SHA-256"},
            });
            self.postMessage({id, ok: true, csrPem: request.toString("pem")});
            return;
        }
        if (command === "sign") {
            if (!keyPair?.privateKey) {
                throw new Error("The document-specific key is no longer available.");
            }
            const rawSignature = new Uint8Array(
                await crypto.subtle.sign(
                    {name: "ECDSA", hash: "SHA-256"},
                    keyPair.privateKey,
                    base64ToBytes(payload.dataToSign)
                )
            );
            const signature = ecdsaRawToDer(rawSignature);
            keyPair = null;
            self.postMessage({id, ok: true, signature: bytesToBase64(signature)});
            return;
        }
        if (command === "destroy") {
            keyPair = null;
            self.postMessage({id, ok: true});
            close();
            return;
        }
        throw new Error("Unsupported worker command.");
    } catch (error) {
        keyPair = null;
        self.postMessage({id, ok: false, error: error.message || "Ceremony failed."});
    }
});
