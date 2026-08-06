import base64
import hashlib
import json
import secrets
from io import BytesIO
from unittest.mock import patch
from urllib.parse import urlsplit

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

from odoo import fields
from odoo.tests import HttpCase, tagged
from odoo.tests import common as test_common
from odoo.tests.common import (
    TEST_CURSOR_COOKIE_NAME,
    ChromeBrowser,
    new_test_user,
)
from odoo.tools.pdf import PdfWriter

from ..models.constants import INTERNAL_OPERATION


@tagged("post_install", "-at_install")
class TestStrongBrowserCeremony(HttpCase):
    """Exercise the production WebAuthn UI with a virtual platform passkey."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Browser Passkey Signer",
                "email": "browser-passkey@example.test",
            },
        )
        cls.partner_two = cls.env["res.partner"].create(
            {
                "name": "Second Browser Passkey Signer",
                "email": "browser-passkey-two@example.test",
            },
        )
        cls.reviewer = new_test_user(
            cls.env,
            login="usl-sign-browser-identity-reviewer",
            groups="usl_sign.group_sign_identity_reviewer",
            company_id=cls.company.id,
        )
        cls.workspace_user = new_test_user(
            cls.env,
            login="usl-sign-browser-workspace-user",
            groups="usl_sign.group_sign_admin",
            company_id=cls.company.id,
        )

    def _enable_virtual_platform_authenticator(self, browser, credential=None):
        browser._websocket_request("WebAuthn.enable")
        result = browser._websocket_request(
            "WebAuthn.addVirtualAuthenticator",
            params={
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                    "automaticPresenceSimulation": True,
                },
            },
        )
        self.assertTrue(result["authenticatorId"])
        if credential:
            browser._websocket_request(
                "WebAuthn.addCredential",
                params={
                    "authenticatorId": result["authenticatorId"],
                    "credential": credential,
                },
            )
        return result["authenticatorId"]

    @staticmethod
    def _pdf():
        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(stream)
        return stream.getvalue()

    def _localhost_origin(self):
        test_origin = urlsplit(self.base_url())
        return f"{test_origin.scheme}://localhost:{test_origin.port}"

    def _allow_localhost_requests(self, origin, original_handler):
        def handler(browser, **params):
            if params["request"]["url"].startswith(origin):
                browser._websocket_send(
                    "Fetch.continueRequest",
                    params={"requestId": params["requestId"]},
                )
                return
            original_handler(browser, **params)

        return handler

    def test_journey_workspace_and_start_dialog_in_web_client(self):
        action = self.env.ref("usl_sign.sign_landing_action")
        self.browser_js(
            f"/odoo/action-{action.id}",
            """
            (async () => {
                const waitFor = async (callback, message) => {
                    for (let attempt = 0; attempt < 200; attempt++) {
                        const result = callback();
                        if (result) {
                            return result;
                        }
                        await new Promise((resolve) => setTimeout(resolve, 50));
                    }
                    throw new Error(message);
                };
                const expectedSections = [
                    "Sign now",
                    "Decide",
                    "Prepare and send",
                    "Resolve issues",
                    "Waiting on others",
                    "Recently completed",
                ];
                const sectionTitles = Array.from(
                    document.querySelectorAll(".usl_sign_work_card h2"),
                    (element) => element.textContent.trim(),
                );
                if (JSON.stringify(sectionTitles) !== JSON.stringify(expectedSections)) {
                    throw new Error(`Unexpected journey sections: ${sectionTitles.join(", ")}`);
                }
                const startButton = document.querySelector(
                    ".usl_sign_workspace header .btn-primary",
                );
                if (!startButton || startButton.textContent.trim() !== "Start") {
                    throw new Error("The primary Start action is missing.");
                }
                startButton.click();
                const dialog = await waitFor(
                    () => document.querySelector(".o_dialog"),
                    "The Start dialog did not open.",
                );
                const dialogText = dialog.textContent.replace(/\\s+/g, " ").trim();
                for (const expected of [
                    "What do you need?",
                    "Request document signatures",
                    "Request a business decision",
                    "Use a template",
                    "Upload a PDF",
                ]) {
                    if (!dialogText.includes(expected)) {
                        throw new Error(`The Start dialog is missing: ${expected}`);
                    }
                }
                console.log("test successful");
            })();
            """,
            ready="document.querySelectorAll('.usl_sign_work_card').length === 6",
            login=self.workspace_user.login,
            timeout=60,
        )

    def test_requester_prepares_sends_and_monitors_routine_agreement(self):
        self.company.email = "requester-browser@example.test"
        template = self.env["sign.oca.template"].create(
            {
                "name": "Browser routine agreement template",
                "filename": "browser-routine-agreement.pdf",
                "data": base64.b64encode(self._pdf()),
                "company_id": self.company.id,
                "policy_id": self.env.ref("usl_sign.policy_routine_standard").id,
            },
        )
        self.env["sign.oca.template.item"].create(
            {
                "template_id": template.id,
                "field_id": self.env.ref("sign_oca.sign_field_signature").id,
                "role_id": self.env.ref("sign_oca.sign_role_customer").id,
                "required": True,
                "page": 1,
                "position_x": 12,
                "position_y": 20,
                "width": 28,
                "height": 8,
            },
        )
        template.action_mark_ready()
        request_name = "Browser requester routine agreement"
        action = self.env.ref("usl_sign.sign_landing_action")
        self.browser_js(
            f"/odoo/action-{action.id}",
            f"""
            (async () => {{
                const sleep = (milliseconds) => new Promise(
                    (resolve) => setTimeout(resolve, milliseconds),
                );
                const waitFor = async (callback, message) => {{
                    for (let attempt = 0; attempt < 300; attempt++) {{
                        const result = callback();
                        if (result) {{
                            return result;
                        }}
                        await sleep(50);
                    }}
                    throw new Error(message);
                }};
                const setInputValue = (input, value) => {{
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype,
                        "value",
                    ).set;
                    setter.call(input, value);
                    input.dispatchEvent(new InputEvent("input", {{
                        bubbles: true,
                        data: value,
                        inputType: "insertText",
                    }}));
                    input.dispatchEvent(new Event("change", {{bubbles: true}}));
                }};
                const visibleButton = (name) => Array.from(document.querySelectorAll(
                    `.o_form_view button[name="${{name}}"]`,
                )).find(
                    (button) => !button.disabled && button.checkVisibility({{
                        opacityProperty: true,
                        visibilityProperty: true,
                    }}),
                );
                const selectMany2One = async (scope, fieldName, query, expected) => {{
                    const widget = await waitFor(
                        () => scope.querySelector(`.o_field_widget[name="${{fieldName}}"]`),
                        `Missing ${{fieldName}} widget.`,
                    );
                    widget.click();
                    const input = await waitFor(
                        () => widget.querySelector("input.o-autocomplete--input"),
                        `Missing ${{fieldName}} input.`,
                    );
                    input.focus();
                    setInputValue(input, query);
                    const option = await waitFor(
                        () => Array.from(document.querySelectorAll(
                            ".o-autocomplete--dropdown-item",
                        )).find((item) => item.textContent.includes(expected)),
                        `No ${{fieldName}} option matched ${{expected}}.`,
                    );
                    (option.querySelector("a, button") || option).click();
                    await waitFor(
                        () => input.value.includes(expected),
                        `${{fieldName}} did not retain ${{expected}}.`,
                    );
                }};

                document.querySelector(".usl_sign_workspace header .btn-primary").click();
                const startDialog = await waitFor(
                    () => document.querySelector(".o_dialog"),
                    "The Start dialog did not open.",
                );
                const nameInput = startDialog.querySelector(
                    '.o_field_widget[name="name"] input',
                );
                setInputValue(nameInput, {json.dumps(request_name)});
                await selectMany2One(
                    startDialog,
                    "template_id",
                    "Browser routine",
                    "Browser routine agreement template",
                );
                startDialog.querySelector('button[name="action_continue"]').click();

                const prepareDialog = await waitFor(
                    () => Array.from(document.querySelectorAll(".o_dialog")).find(
                        (dialog) => dialog.textContent.includes("Who must sign?"),
                    ),
                    "The Prepare request dialog did not open.",
                );
                const preparedName = prepareDialog.querySelector(
                    '.o_field_widget[name="request_name"] input',
                );
                if (preparedName?.value !== {json.dumps(request_name)}) {{
                    throw new Error(`The request name was lost: ${{preparedName?.value}}`);
                }}
                const partnerCell = prepareDialog.querySelector(
                    '.o_field_widget[name="signer_ids"] .o_data_row '
                    + '.o_field_cell[name="partner_id"]',
                );
                if (!partnerCell) {{
                    throw new Error("The signer role row is missing.");
                }}
                partnerCell.click();
                await selectMany2One(
                    prepareDialog,
                    "partner_id",
                    "Browser Passkey",
                    "Browser Passkey Signer",
                );
                const prepareText = prepareDialog.textContent.replace(/\\s+/g, " ").trim();
                for (const expected of [
                    "Recommended",
                    "Standard electronic signature with reinforced evidence.",
                    "Create request",
                ]) {{
                    if (!prepareText.includes(expected)) {{
                        throw new Error(`Preparation is missing: ${{expected}}`);
                    }}
                }}
                prepareDialog.querySelector('button[name="generate"]').click();

                const reviewButton = await waitFor(
                    () => visibleButton("action_mark_ready"),
                    "The draft request did not open for review.",
                );
                const form = reviewButton.closest(".o_form_view");
                const formText = form.textContent.replace(/\\s+/g, " ").trim();
                for (const expected of [
                    {json.dumps(request_name)},
                    "Signatures",
                    "Requested trust",
                    "Proof",
                    "Archive",
                    "Overview",
                    "Signers",
                    "Documents",
                    "Proof & Validation",
                    "Timeline",
                ]) {{
                    if (!formText.includes(expected)) {{
                        throw new Error(`Request review is missing: ${{expected}}`);
                    }}
                }}
                reviewButton.click();
                const sendButton = await waitFor(
                    () => visibleButton("action_send"),
                    "The request did not become ready to send.",
                );
                sendButton.click();
                const sentForm = await waitFor(
                    () => {{
                        const candidate = document.querySelector(".o_form_view");
                        return candidate?.textContent.includes(
                            "Waiting for Browser Passkey Signer",
                        ) ? candidate : null;
                    }},
                    `The request did not enter monitoring: ${{
                        document.querySelector(".o_form_view")?.textContent
                            .replace(/\\s+/g, " ").trim()
                    }}`,
                );
                const staleSendButton = visibleButton("action_send");
                if (staleSendButton) {{
                    throw new Error(
                        `The Send action stayed visible after sending: ${{staleSendButton.outerHTML}}`,
                    );
                }}
                const sentFormText = sentForm.textContent.replace(/\\s+/g, " ").trim();
                if (!sentFormText.includes("0 of 1 signed")) {{
                    throw new Error(`The signer progress is unclear: ${{sentFormText}}`);
                }}
                console.log("test successful");
            }})();
            """,
            ready="document.querySelectorAll('.usl_sign_work_card').length === 6",
            login=self.workspace_user.login,
            timeout=90,
        )
        request = self.env["sign.oca.request"].search(
            [("name", "=", request_name)],
        )
        self.assertEqual(len(request), 1)
        self.assertEqual(request.state, "sent")
        self.assertEqual(request.user_id, self.workspace_user)
        self.assertEqual(request.signer_ids.partner_id, self.partner)
        self.assertEqual(request.signer_ids.state, "notified")
        self.assertEqual(request.signer_ids.invitation_delivery_state, "queued")
        self.assertTrue(request.original_sha256)
        self.assertEqual(
            request.event_ids.mapped("event_type"),
            [
                "request_created",
                "request_ready",
                "document_frozen",
                "request_sent",
                "invitation_queued",
            ],
        )

    def test_library_tabs_in_web_client(self):
        action = self.env.ref("usl_sign.sign_library_action")
        self.browser_js(
            f"/odoo/action-{action.id}",
            """
            (async () => {
                const waitFor = async (callback, message) => {
                    for (let attempt = 0; attempt < 200; attempt++) {
                        const result = callback();
                        if (result) {
                            return result;
                        }
                        await new Promise((resolve) => setTimeout(resolve, 50));
                    }
                    throw new Error(message);
                };
                const tabs = Array.from(document.querySelectorAll(".usl_sign_library .nav-link"));
                if (tabs.length !== 2 || tabs[0].textContent.trim() !== "Templates") {
                    throw new Error("The Library does not open on Templates.");
                }
                if (tabs[1].textContent.trim() !== "Completed Documents") {
                    throw new Error("The completed-document tab is missing.");
                }
                tabs[1].click();
                await waitFor(
                    () => tabs[1].classList.contains("active")
                        && !document.querySelector(".usl_sign_workspace_state"),
                    "The Completed Documents tab did not load.",
                );
                if (!document.querySelector(".usl_sign_library_search")) {
                    throw new Error("The Library search is missing.");
                }
                console.log("test successful");
            })();
            """,
            ready=(
                "document.querySelectorAll('.usl_sign_library .nav-link').length === 2 && "
                "!document.querySelector('.usl_sign_workspace_state')"
            ),
            login=self.workspace_user.login,
            timeout=60,
        )

    def test_passkey_enrollment_through_public_browser_page(self):
        origin = self._localhost_origin()
        self.company.write(
            {
                "sign_webauthn_rp_id": "localhost",
                "sign_webauthn_origins": origin,
            },
        )
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Browser acceptance fixture",
                "policy_version": "browser-acceptance-v1",
                "review_note": "Identity reviewed for automated browser acceptance.",
            },
        )
        action = enrollment.with_user(self.reviewer).action_confirm_identity()
        enrollment_path = urlsplit(action["url"]).path

        original_navigate = ChromeBrowser.navigate_to
        original_handle_request = ChromeBrowser._handle_request_paused

        def navigate_with_platform_passkey(browser, url, wait_stop=False):
            self._enable_virtual_platform_authenticator(browser)
            browser.set_cookie("session_id", self.session.sid, "/", "localhost")
            browser.set_cookie(
                TEST_CURSOR_COOKIE_NAME,
                self.http_request_key,
                "/",
                "localhost",
            )
            localhost_url = url.replace(self.base_url().rstrip("/"), origin, 1)
            return original_navigate(browser, localhost_url, wait_stop=wait_stop)

        with (
            patch.object(
                ChromeBrowser,
                "navigate_to",
                new=navigate_with_platform_passkey,
            ),
            patch.object(
                ChromeBrowser,
                "_handle_request_paused",
                new=self._allow_localhost_requests(origin, original_handle_request),
            ),
        ):
            self.browser_js(
                enrollment_path,
                """
                (async () => {
                    document.getElementById("usl_passkey_name").value = "Platform passkey";
                    document.getElementById("usl_enroll_button").click();
                    const status = document.getElementById("usl_enroll_status");
                    for (let attempt = 0; attempt < 300; attempt++) {
                        await new Promise((resolve) => setTimeout(resolve, 100));
                        if (status.classList.contains("alert-success")) {
                            console.log("test successful");
                            return;
                        }
                        if (status.classList.contains("alert-danger")) {
                            throw new Error(status.textContent);
                        }
                    }
                    throw new Error("Passkey registration did not finish in time.");
                })();
                """,
                ready=(
                    "document.getElementById('usl_strong_enrollment')?.dataset.ready === 'true' && "
                    "!document.getElementById('usl_enroll_button').disabled"
                ),
                login=None,
                timeout=60,
            )

        enrollment.invalidate_recordset()
        self.assertEqual(enrollment.state, "active")
        self.assertFalse(enrollment.registration_challenge)
        self.assertEqual(enrollment.active_passkey_count, 1)
        passkey = enrollment.passkey_ids
        self.assertEqual(passkey.name, "Platform passkey")
        self.assertEqual(passkey.state, "active")
        self.assertIn("internal", passkey.transports)
        self.assertTrue(passkey.credential_id)
        self.assertTrue(passkey.public_key)

    def test_standard_signature_through_public_browser_and_archive(self):
        origin = self._localhost_origin()
        self.company.email = "sign-browser@example.test"
        self.partner.phone = "+33123456789"
        role = self.env.ref("sign_oca.sign_role_customer")
        text_field = self.env.ref("sign_oca.sign_field_name")
        date_field = self.env.ref("usl_sign.field_date")
        email_field = self.env.ref("sign_oca.sign_field_email")
        phone_field = self.env.ref("sign_oca.sign_field_phone")
        checkbox_field = self.env.ref("sign_oca.sign_field_check")
        signature_field = self.env.ref("sign_oca.sign_field_signature")
        layout = {
            "1": {
                "id": 1,
                "field_id": text_field.id,
                "field_type": text_field.field_type,
                "kind": "signer_name",
                "required": True,
                "name": text_field.name,
                "role_id": role.id,
                "tabindex": 1,
                "page": 1,
                "position_x": 12,
                "position_y": 72,
                "width": 28,
                "height": 6,
                "value": False,
                "default_value": text_field.default_value,
                "placeholder": "Full name",
            },
            "2": {
                "id": 2,
                "field_id": date_field.id,
                "field_type": date_field.field_type,
                "required": True,
                "name": date_field.name,
                "role_id": role.id,
                "tabindex": 2,
                "page": 1,
                "position_x": 12,
                "position_y": 80,
                "width": 20,
                "height": 6,
                "value": False,
                "default_value": date_field.default_value,
                "placeholder": "Signing date",
            },
            "3": {
                "id": 3,
                "field_id": email_field.id,
                "field_type": email_field.field_type,
                "required": True,
                "name": email_field.name,
                "role_id": role.id,
                "tabindex": 3,
                "page": 1,
                "position_x": 45,
                "position_y": 64,
                "width": 36,
                "height": 6,
                "value": False,
                "default_value": email_field.default_value,
                "placeholder": "Email address",
            },
            "4": {
                "id": 4,
                "field_id": phone_field.id,
                "field_type": phone_field.field_type,
                "required": True,
                "name": phone_field.name,
                "role_id": role.id,
                "tabindex": 4,
                "page": 1,
                "position_x": 45,
                "position_y": 72,
                "width": 28,
                "height": 6,
                "value": False,
                "default_value": phone_field.default_value,
                "placeholder": "Phone number",
            },
            "5": {
                "id": 5,
                "field_id": checkbox_field.id,
                "field_type": checkbox_field.field_type,
                "required": True,
                "name": checkbox_field.name,
                "role_id": role.id,
                "tabindex": 5,
                "page": 1,
                "position_x": 45,
                "position_y": 80,
                "width": 6,
                "height": 6,
                "value": False,
                "default_value": checkbox_field.default_value,
                "placeholder": "",
            },
            "6": {
                "id": 6,
                "field_id": signature_field.id,
                "field_type": signature_field.field_type,
                "required": True,
                "name": signature_field.name,
                "role_id": role.id,
                "tabindex": 6,
                "page": 1,
                "position_x": 12,
                "position_y": 88,
                "width": 32,
                "height": 8,
                "value": False,
                "default_value": signature_field.default_value,
                "placeholder": "",
            },
        }
        sign_request = self.env["sign.oca.request"].create(
            {
                "name": "Browser Standard-signature acceptance",
                "data": base64.b64encode(self._pdf()),
                "filename": "browser-standard-signature.pdf",
                "company_id": self.company.id,
                "user_id": self.env.user.id,
                "policy_id": self.env.ref("usl_sign.policy_routine_standard").id,
                "document_category": "routine_agreement",
                "signer_type": "occasional",
                "risk_level": "low",
                "requested_trust": "standard",
                "signatory_data": layout,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": self.partner.id,
                            "role_id": role.id,
                            "sequence": 10,
                        },
                    ),
                ],
            },
        )
        sign_request.action_mark_ready()
        sign_request.action_send()
        signer = sign_request.signer_ids
        invitation_token = signer._issue_access_token()
        session_token = signer._exchange_access_token(invitation_token)
        signing_path = f"/sign/session/{signer.id}/{session_token}?review=1"
        archived = self.env["usl.document"].sudo().create(
            {
                "name": "Browser Standard evidence dossier",
                "paperless_id": 990045,
                "company_id": self.company.id,
                "confidentiality": "private",
                "availability_state": "available",
                "source": "odoo_generated",
            },
        )

        original_navigate = ChromeBrowser.navigate_to
        original_handle_request = ChromeBrowser._handle_request_paused
        original_external_request = type(self)._request_handler
        mobile_viewport = {"enabled": False}

        def navigate_on_localhost(browser, url, wait_stop=False):
            if mobile_viewport["enabled"]:
                browser._websocket_request(
                    "Emulation.setDeviceMetricsOverride",
                    params={
                        "width": 390,
                        "height": 844,
                        "deviceScaleFactor": 2,
                        "mobile": True,
                    },
                )
            browser.set_cookie("session_id", self.session.sid, "/", "localhost")
            browser.set_cookie(
                TEST_CURSOR_COOKIE_NAME,
                self.http_request_key,
                "/",
                "localhost",
            )
            localhost_url = url.replace(self.base_url().rstrip("/"), origin, 1)
            return original_navigate(browser, localhost_url, wait_stop=wait_stop)

        def allow_local_sign_services(test_class, session, prepared, **kwargs):
            del test_class
            if urlsplit(prepared.url).hostname in {
                "paperless-webserver",
                "usl-sign-dss",
            }:
                return test_common._super_send(session, prepared, **kwargs)
            return original_external_request(session, prepared, **kwargs)

        with (
            patch.object(ChromeBrowser, "navigate_to", new=navigate_on_localhost),
            patch.object(
                ChromeBrowser,
                "_handle_request_paused",
                new=self._allow_localhost_requests(origin, original_handle_request),
            ),
            patch.object(
                type(self),
                "_request_handler",
                new=classmethod(allow_local_sign_services),
            ),
            patch.object(
                type(self.env["usl.document"]),
                "upload_from_odoo",
                return_value={
                    "state": "duplicate",
                    "document_id": archived.id,
                    "message": "Checksum-identical dossier reused.",
                },
            ),
        ):
            self.browser_js(
                signing_path,
                """
                (async () => {
                    const iframe = document.querySelector(".o_sign_oca_iframe");
                    for (let attempt = 0; attempt < 400; attempt++) {
                        if (iframe.contentDocument?.querySelector(".o_sign_oca_field")) {
                            break;
                        }
                        await new Promise((resolve) => setTimeout(resolve, 50));
                    }
                    if (window.innerWidth < 900) {
                        throw new Error(`Desktop viewport is unexpectedly narrow: ${window.innerWidth}`);
                    }
                    if (!iframe.contentDocument?.querySelector(".o_sign_oca_field")) {
                        throw new Error("The Standard PDF fields did not render on desktop.");
                    }
                    const actions = document.querySelector(".usl_sign_portal_actions");
                    if (!actions) {
                        throw new Error("The desktop signer actions are missing.");
                    }
                    console.log("test successful");
                })();
                """,
                ready="Boolean(document.getElementById('usl_sign_consent'))",
                login=None,
                timeout=60,
            )
            mobile_viewport["enabled"] = True
            self.browser_js(
                signing_path,
                """
                (async () => {
                    if (window.innerWidth > 430) {
                        throw new Error(`Mobile viewport was not applied: ${window.innerWidth}`);
                    }
                    const iframe = document.querySelector(".o_sign_oca_iframe");
                    let input;
                    for (let attempt = 0; attempt < 400 && !input; attempt++) {
                        input = iframe.contentDocument?.querySelector(
                            '.o_sign_oca_field input[type="text"]',
                        );
                        if (!input) {
                            await new Promise((resolve) => setTimeout(resolve, 50));
                        }
                    }
                    if (!input) {
                        throw new Error("The Standard signer field did not render.");
                    }
                    input.focus();
                    await new Promise((resolve) => setTimeout(resolve, 0));
                    if (!input.isConnected) {
                        throw new Error("The signer field was replaced when it received focus.");
                    }
                    input.value = "Browser Standard Signer";
                    input.dispatchEvent(new InputEvent("input", {bubbles: true}));
                    if (!input.isConnected) {
                        throw new Error("The signer field was replaced while typing.");
                    }
                    input.dispatchEvent(new Event("change", {bubbles: true}));
                    const dateInput = iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field input[type="date"]',
                    );
                    if (!dateInput || !/^\\d{4}-\\d{2}-\\d{2}$/.test(dateInput.value)) {
                        throw new Error("The typed signing date was not prefilled.");
                    }
                    const emailInput = iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field input[type="email"]',
                    );
                    const phoneInput = iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field input[type="tel"]',
                    );
                    const checkboxInput = iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field input[type="checkbox"]',
                    );
                    if (
                        !emailInput || emailInput.value !== "browser-passkey@example.test" ||
                        emailInput.autocomplete !== "email"
                    ) {
                        throw new Error("The typed email field was not configured correctly.");
                    }
                    if (
                        !phoneInput || phoneInput.value !== "+33123456789" ||
                        phoneInput.autocomplete !== "tel"
                    ) {
                        throw new Error("The typed phone field was not configured correctly.");
                    }
                    if (!checkboxInput || checkboxInput.checked) {
                        throw new Error("The required checkbox did not render correctly.");
                    }
                    checkboxInput.click();
                    const signatureField = iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field[data-field="6"]',
                    );
                    if (!signatureField) {
                        throw new Error("The visual signature field did not render.");
                    }
                    signatureField.click();
                    let adoptButton;
                    for (let attempt = 0; attempt < 200 && !adoptButton; attempt++) {
                        adoptButton = Array.from(
                            document.querySelectorAll(".modal .btn-primary"),
                        ).find((candidate) => candidate.textContent.includes("Adopt"));
                        if (!adoptButton || adoptButton.disabled) {
                            adoptButton = null;
                            await new Promise((resolve) => setTimeout(resolve, 50));
                        }
                    }
                    if (!adoptButton) {
                        throw new Error("The visual signature dialog was not ready.");
                    }
                    adoptButton.click();
                    for (
                        let attempt = 0;
                        attempt < 200 && !iframe.contentDocument?.querySelector(
                            '.o_sign_oca_field[data-field="6"] img',
                        );
                        attempt++
                    ) {
                        await new Promise((resolve) => setTimeout(resolve, 50));
                    }
                    if (!iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field[data-field="6"] img',
                    )) {
                        throw new Error("The adopted visual signature was not placed.");
                    }
                    emailInput.value = "invalid-address";
                    emailInput.dispatchEvent(new InputEvent("input", {bubbles: true}));
                    emailInput.dispatchEvent(new Event("change", {bubbles: true}));
                    document.getElementById("usl_sign_consent").checked = true;
                    const button = document.getElementById("sign_oca_button");
                    for (let attempt = 0; attempt < 200 && button.disabled; attempt++) {
                        await new Promise((resolve) => setTimeout(resolve, 50));
                    }
                    if (button.disabled) {
                        throw new Error("The completed Standard fields did not enable signing.");
                    }
                    const actions = document.querySelector(".usl_sign_portal_actions");
                    const actionBounds = actions?.getBoundingClientRect();
                    if (
                        !actionBounds || actionBounds.width <= 0 || actionBounds.left < 0 ||
                        actionBounds.right > window.innerWidth + 1
                    ) {
                        throw new Error("The mobile signer actions overflow the viewport.");
                    }
                    button.click();
                    await new Promise((resolve) => setTimeout(resolve, 50));
                    if (!emailInput.matches(":invalid") || button.disabled) {
                        throw new Error("An invalid typed email did not block submission.");
                    }
                    emailInput.value = "signed-browser@example.test";
                    emailInput.dispatchEvent(new InputEvent("input", {bubbles: true}));
                    emailInput.dispatchEvent(new Event("change", {bubbles: true}));
                    window.addEventListener(
                        "beforeunload",
                        () => console.log("test successful"),
                        {once: true},
                    );
                    button.click();
                })();
                """,
                ready="Boolean(document.getElementById('usl_sign_consent'))",
                login=None,
                timeout=120,
            )

        sign_request.invalidate_recordset()
        signer.invalidate_recordset()
        self.assertEqual(signer.state, "signed")
        self.assertEqual(signer.authentication_method, "secure_link")
        self.assertTrue(signer.consented_at)
        self.assertTrue(signer.signed_document_sha256)
        self.assertEqual(
            sign_request.state,
            "completed",
            (
                f"archive={sign_request.archive_status}, "
                f"operation={sign_request.archive_operation_id.state}, "
                f"archive_error={sign_request.archive_last_error or ''}, "
                f"last_error={sign_request.last_error or ''}"
            ),
        )
        self.assertEqual(sign_request.achieved_trust, "standard")
        self.assertEqual(sign_request.validation_status, "valid")
        self.assertEqual(sign_request.evidence_status, "complete")
        self.assertEqual(sign_request.archive_status, "archived")
        self.assertTrue(sign_request.archive_document_id)
        self.assertTrue(sign_request.final_data)
        self.assertTrue(sign_request.completion_certificate)
        self.assertTrue(sign_request.dossier_data)
        self.assertEqual(
            sign_request.signatory_data["1"]["value"],
            "Browser Standard Signer",
        )
        self.assertRegex(sign_request.signatory_data["2"]["value"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(
            sign_request.signatory_data["3"]["value"],
            "signed-browser@example.test",
        )
        self.assertEqual(sign_request.signatory_data["4"]["value"], "+33123456789")
        self.assertTrue(sign_request.signatory_data["5"]["value"])
        self.assertTrue(
            sign_request.signatory_data["6"]["value"].startswith(
                "data:image/png;base64,",
            ),
        )
        consent_evidence = sign_request.evidence_ids.filtered(
            lambda evidence: evidence.kind == "consent",
        )
        self.assertEqual(len(consent_evidence), 1)
        consent_payload = json.loads(base64.b64decode(consent_evidence.data))
        self.assertEqual(
            consent_payload["reviewed_document_sha256"],
            sign_request.original_sha256,
        )
        self.assertEqual(consent_payload["consent"], sign_request.consent_text_snapshot)

    def test_ordered_document_specific_strong_signatures_through_browser_workers(self):
        origin = self._localhost_origin()
        self.company.write(
            {
                "email": "sign-browser@example.test",
                "sign_webauthn_rp_id": "localhost",
                "sign_webauthn_origins": origin,
            },
        )

        def create_passkey_fixture(partner, suffix):
            enrollment = self.env["usl.sign.enrollment"].create(
                {
                    "partner_id": partner.id,
                    "company_id": self.company.id,
                    "relationship_basis": "recurring_partner",
                    "relationship_reference": (
                        f"Browser signing acceptance fixture {suffix}"
                    ),
                    "policy_version": "browser-acceptance-v1",
                    "review_note": (
                        "Identity reviewed for automated browser acceptance."
                    ),
                },
            )
            enrollment.with_context(
                usl_sign_enrollment_transition=INTERNAL_OPERATION,
            ).write(
                {
                    "state": "active",
                    "reviewer_id": self.reviewer.id,
                    "reviewed_at": fields.Datetime.now(),
                },
            )
            credential_bytes = secrets.token_bytes(32)
            credential_id = bytes_to_base64url(credential_bytes)
            private_key = ec.generate_private_key(ec.SECP256R1())
            public_numbers = private_key.public_key().public_numbers()
            cose_public_key = cbor2.dumps(
                {
                    1: 2,
                    3: -7,
                    -1: 1,
                    -2: public_numbers.x.to_bytes(32, "big"),
                    -3: public_numbers.y.to_bytes(32, "big"),
                },
            )
            passkey = self.env["usl.sign.passkey"].with_context(
                usl_sign_passkey_registration=INTERNAL_OPERATION,
            ).create(
                {
                    "enrollment_id": enrollment.id,
                    "name": f"Virtual platform passkey {suffix}",
                    "credential_id": credential_id,
                    "public_key": base64.b64encode(cose_public_key),
                    "sign_count": 0,
                    "aaguid": "00000000-0000-0000-0000-000000000000",
                    "transports": ["internal"],
                    "device_type": "single_device",
                    "backed_up": False,
                },
            )
            virtual_credential = {
                "credentialId": base64.b64encode(credential_bytes).decode(),
                "isResidentCredential": False,
                "rpId": "localhost",
                "privateKey": base64.b64encode(
                    private_key.private_bytes(
                        serialization.Encoding.DER,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    ),
                ).decode(),
                "signCount": 0,
            }
            return passkey, virtual_credential

        passkey, virtual_credential = create_passkey_fixture(self.partner, "one")
        passkey_two, virtual_credential_two = create_passkey_fixture(
            self.partner_two,
            "two",
        )

        role = self.env.ref("sign_oca.sign_role_customer")
        role_two = self.env.ref("sign_oca.sign_role_employee")
        signature_field = self.env.ref("sign_oca.sign_field_signature")
        layout = {
            "1": {
                "id": 1,
                "field_id": signature_field.id,
                "field_type": signature_field.field_type,
                "required": True,
                "name": signature_field.name,
                "role_id": role.id,
                "page": 1,
                "position_x": 12,
                "position_y": 72,
                "width": 28,
                "height": 9,
                "value": False,
                "default_value": signature_field.default_value,
                "placeholder": "",
            },
            "2": {
                "id": 2,
                "field_id": signature_field.id,
                "field_type": signature_field.field_type,
                "required": True,
                "name": signature_field.name,
                "role_id": role_two.id,
                "page": 1,
                "position_x": 52,
                "position_y": 72,
                "width": 28,
                "height": 9,
                "value": False,
                "default_value": signature_field.default_value,
                "placeholder": "",
            },
        }
        sign_request = self.env["sign.oca.request"].create(
            {
                "name": "Browser strong-signature acceptance",
                "data": base64.b64encode(self._pdf()),
                "filename": "browser-strong-signature.pdf",
                "company_id": self.company.id,
                "user_id": self.env.user.id,
                "policy_id": self.env.ref(
                    "usl_sign.policy_material_recurring_strong",
                ).id,
                "document_category": "commercial",
                "signer_type": "recurring",
                "risk_level": "material",
                "requested_trust": "strong_personal",
                "signing_order": True,
                "signatory_data": layout,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": self.partner.id,
                            "role_id": role.id,
                            "sequence": 10,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "partner_id": self.partner_two.id,
                            "role_id": role_two.id,
                            "sequence": 20,
                        },
                    ),
                ],
            },
        )
        sign_request.action_mark_ready()
        sign_request.action_send()
        signer, signer_two = sign_request.signer_ids.sorted(
            lambda row: (row.sequence, row.id),
        )
        invitation_token = signer._issue_access_token()
        session_token = signer._exchange_access_token(invitation_token)
        signing_path = f"/sign/session/{signer.id}/{session_token}?review=1"

        captured_requests = []
        active_virtual_credential = {"value": virtual_credential}
        original_navigate = ChromeBrowser.navigate_to
        original_handle_request = ChromeBrowser._handle_request_paused
        original_external_request = type(self)._request_handler

        def navigate_with_registered_passkey(browser, url, wait_stop=False):
            self._enable_virtual_platform_authenticator(
                browser,
                active_virtual_credential["value"],
            )

            def capture_request(request, **_params):
                if "/sign/strong/" in request["url"] and request.get("postData"):
                    captured_requests.append(
                        {"url": request["url"], "body": request["postData"]},
                    )

            browser._handlers["Network.requestWillBeSent"] = capture_request
            browser._websocket_request("Network.enable")
            browser.set_cookie("session_id", self.session.sid, "/", "localhost")
            browser.set_cookie(
                TEST_CURSOR_COOKIE_NAME,
                self.http_request_key,
                "/",
                "localhost",
            )
            localhost_url = url.replace(self.base_url().rstrip("/"), origin, 1)
            return original_navigate(browser, localhost_url, wait_stop=wait_stop)

        def allow_local_sign_services(test_class, session, prepared, **kwargs):
            del test_class
            if urlsplit(prepared.url).hostname in {
                "paperless-webserver",
                "usl-sign-dss",
                "usl-sign-step-ca",
            }:
                return test_common._super_send(session, prepared, **kwargs)
            return original_external_request(session, prepared, **kwargs)

        def run_signer_browser(path):
            self.browser_js(
                path,
                """
                window.addEventListener(
                    "beforeunload",
                    () => console.log("test successful"),
                    {once: true},
                );
                const status = document.getElementById("usl_strong_status");
                new MutationObserver(() => {
                    if (status.classList.contains("alert-danger")) {
                        console.error(status.textContent);
                    }
                }).observe(status, {attributes: true, childList: true, subtree: true});
                document.getElementById("usl_strong_consent").checked = true;
                document.getElementById("usl_strong_sign_button").click();
                """,
                ready=(
                    "document.getElementById('usl_strong_sign')?.dataset.ready === 'true' && "
                    "!document.getElementById('usl_strong_sign_button').disabled"
                ),
                login=None,
                timeout=120,
            )

        with (
            patch.object(
                ChromeBrowser,
                "navigate_to",
                new=navigate_with_registered_passkey,
            ),
            patch.object(
                ChromeBrowser,
                "_handle_request_paused",
                new=self._allow_localhost_requests(origin, original_handle_request),
            ),
            patch.object(
                type(self),
                "_request_handler",
                new=classmethod(allow_local_sign_services),
            ),
        ):
            run_signer_browser(signing_path)
            sign_request.invalidate_recordset(["data", "state"])
            signer.invalidate_recordset()
            signer_two.invalidate_recordset()
            passkey.invalidate_recordset()
            first_signed_sha256 = hashlib.sha256(
                base64.b64decode(sign_request.data),
            ).hexdigest()
            first_ceremony = self.env["usl.sign.ceremony"].search(
                [("request_id", "=", sign_request.id), ("signer_id", "=", signer.id)],
            )
            self.assertEqual(sign_request.state, "partial")
            self.assertEqual(signer.state, "signed")
            self.assertEqual(signer_two.state, "notified")
            self.assertEqual(first_ceremony.state, "completed")
            self.assertEqual(first_ceremony.passkey_id, passkey)
            self.assertNotEqual(first_signed_sha256, sign_request.original_sha256)

            invitation_token_two = signer_two._issue_access_token()
            session_token_two = signer_two._exchange_access_token(invitation_token_two)
            signing_path_two = (
                f"/sign/session/{signer_two.id}/{session_token_two}?review=1"
            )
            active_virtual_credential["value"] = virtual_credential_two
            run_signer_browser(signing_path_two)

        sign_request.invalidate_recordset()
        signer.invalidate_recordset()
        signer_two.invalidate_recordset()
        passkey.invalidate_recordset()
        passkey_two.invalidate_recordset()
        ceremony = self.env["usl.sign.ceremony"].search(
            [("request_id", "=", sign_request.id)],
        )
        self.assertEqual(len(ceremony), 2)
        self.assertEqual(set(ceremony.mapped("state")), {"completed"})
        self.assertEqual(set(ceremony.mapped("passkey_id")), {passkey, passkey_two})
        self.assertTrue(all(ceremony.mapped("certificate_serial")))
        self.assertTrue(all(ceremony.mapped("certificate_not_after")))
        first_ceremony = ceremony.filtered(lambda row: row.signer_id == signer)
        second_ceremony = ceremony.filtered(lambda row: row.signer_id == signer_two)
        self.assertEqual(first_ceremony.document_sha256, sign_request.original_sha256)
        self.assertEqual(second_ceremony.document_sha256, first_signed_sha256)
        self.assertEqual(signer.state, "signed")
        self.assertEqual(signer_two.state, "signed")
        self.assertEqual(signer.authentication_method, "passkey")
        self.assertEqual(signer_two.authentication_method, "passkey")
        self.assertTrue(signer.signed_document_sha256)
        self.assertTrue(signer_two.signed_document_sha256)
        self.assertTrue(passkey.last_used_at)
        self.assertTrue(passkey_two.last_used_at)
        self.assertTrue(sign_request.final_data)
        self.assertEqual(sign_request.achieved_trust, "strong_personal")
        self.assertEqual(sign_request.validation_status, "valid")
        self.assertTrue(
            sign_request.evidence_ids.filtered(lambda item: item.kind == "certificate"),
        )
        self.assertTrue(
            sign_request.evidence_ids.filtered(lambda item: item.kind == "consent"),
        )

        payloads = {"begin": [], "authorize": [], "finalize": []}
        for captured in captured_requests:
            route = urlsplit(captured["url"]).path.rsplit("/", 1)[-1]
            payloads[route].append(json.loads(captured["body"])["params"])
        self.assertEqual({route: len(rows) for route, rows in payloads.items()}, {
            "begin": 2,
            "authorize": 2,
            "finalize": 2,
        })
        for params in payloads["begin"]:
            self.assertEqual(set(params), {"csr_pem", "consent"})
        for params in payloads["authorize"]:
            self.assertEqual(set(params), {"ceremony_id", "credential"})
        for params in payloads["finalize"]:
            self.assertEqual(set(params), {"ceremony_id", "signature"})
        serialized_payloads = json.dumps(payloads).lower()
        for forbidden in ("privatekey", "private_key", "pkcs8", "jwk", "seed"):
            self.assertNotIn(forbidden, serialized_payloads)
