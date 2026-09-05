import json
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from odoo.tests import HttpCase, tagged
from odoo.tests import common as test_common
from odoo.tests.common import (
    TEST_CURSOR_COOKIE_NAME,
    ChromeBrowser,
    new_test_user,
)
from odoo.tools.pdf import PdfWriter

from odoo.addons.usl_sign.services import field_content, field_value

from .test_sign import FakeDSS, _renderer_result


@tagged("post_install", "-at_install")
class TestSignBrowserJourneys(HttpCase):
    """Exercise requester and Standard signer journeys without biometrics."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write(
            {
                "street": "1 rue de la Paix",
                "zip": "75001",
                "city": "Paris",
                # Exercise the governed built-in wordmark used by the
                # document renderer when no custom company logo is set.
                "company_registry": "983982950",
            },
        )
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

    @staticmethod
    def _pdf(pages=1):
        stream = BytesIO()
        writer = PdfWriter()
        for _index in range(pages):
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
                    "Prepare and send",
                    "Needs attention",
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
                const scroller = document.querySelector(
                    ".usl_sign_workspace > .o_content",
                );
                if (!scroller || getComputedStyle(scroller).overflowY !== "auto") {
                    throw new Error("The dashboard content area is not scrollable.");
                }
                const startButton = document.querySelector(
                    ".usl_sign_workspace header .btn-primary",
                );
                if (!startButton || startButton.textContent.trim() !== "Request signatures") {
                    throw new Error("The primary Request signatures action is missing.");
                }
                startButton.click();
                const dialog = await waitFor(
                    () => document.querySelector(".o_dialog"),
                    "The Request signatures dialog did not open.",
                );
                const dialogText = dialog.textContent.replace(/\\s+/g, " ").trim();
                for (const expected of [
                    "Request signatures",
                    "Use a template",
                    "Upload a PDF",
                    "Document",
                    "Signers",
                    "Request details",
                    "Request name",
                    "Message to signers",
                    "Related Odoo record",
                    "Signing method",
                ]) {
                    if (!dialogText.includes(expected)) {
                        throw new Error(`The Request signatures dialog is missing: ${expected}`);
                    }
                }
                if (dialog.querySelector("details, .usl_sign_step_label")) {
                    throw new Error("Request details must stay visible in the standard form.");
                }
                const detailsGrid = dialog.querySelector(
                    ".usl_sign_start_details .usl_sign_start_grid",
                );
                if (!detailsGrid) {
                    throw new Error("The responsive Request details layout is missing.");
                }
                const gridStyle = getComputedStyle(detailsGrid);
                if (gridStyle.display !== "grid" || gridStyle.gridTemplateColumns.split(" ").length !== 2) {
                    throw new Error("Request details are not using the desktop two-column layout.");
                }
                console.log("test successful");
            })();
            """,
            ready="document.querySelectorAll('.usl_sign_work_card').length === 5",
            login=self.workspace_user.login,
            timeout=60,
        )

    def test_template_editor_places_repeats_scrolls_and_deletes_fields(self):
        template = self.env["sign.oca.template"].create(
            {
                "name": "Browser field editor regression",
                "filename": "browser-field-editor.pdf",
                "data": field_value(self._pdf(pages=3)),
                "company_id": self.company.id,
            },
        )
        action = self.env["ir.actions.client"].create(
            {
                "name": template.name,
                "tag": "usl_sign_template_configure",
                "params": {
                    "res_model": template._name,
                    "res_id": template.id,
                },
            },
        )
        self.browser_js(
            f"/odoo/action-{action.id}",
            """
            (async () => {
                const sleep = (milliseconds) => new Promise(
                    (resolve) => setTimeout(resolve, milliseconds),
                );
                const waitFor = async (callback, message) => {
                    for (let attempt = 0; attempt < 400; attempt++) {
                        const result = callback();
                        if (result) {
                            return result;
                        }
                        await sleep(50);
                    }
                    throw new Error(message);
                };
                const iframe = await waitFor(
                    () => document.querySelector(".o_sign_oca_iframe"),
                    "The PDF editor iframe did not render.",
                );
                const iframeDocument = await waitFor(
                    () => iframe.contentDocument?.querySelectorAll(".page").length === 3 &&
                        iframe.contentDocument.querySelector(".o_sign_oca_ready")
                        ? iframe.contentDocument
                        : null,
                    "The three PDF pages did not render.",
                );
                const editor = document.querySelector(".usl_sign_editor");
                const workspace = document.querySelector(".usl_sign_editor_workspace");
                if (getComputedStyle(editor).display !== "flex" || workspace.clientHeight < 200) {
                    throw new Error("The editor does not own a usable scrollable viewport.");
                }
                const viewer = iframeDocument.getElementById("viewerContainer");
                if (!viewer || viewer.scrollHeight <= viewer.clientHeight) {
                    throw new Error("The PDF viewer is not vertically scrollable.");
                }
                viewer.scrollTop = Math.min(250, viewer.scrollHeight - viewer.clientHeight);
                await new Promise((resolve) => requestAnimationFrame(resolve));
                if (viewer.scrollTop <= 0) {
                    throw new Error("The PDF viewer did not retain its scroll position.");
                }

                const initials = Array.from(document.querySelectorAll(
                    ".usl_sign_field_type",
                )).find((button) => button.textContent.trim() === "Initials");
                if (!initials) {
                    throw new Error("The Initials field type is missing.");
                }
                initials.click();
                const firstPage = await waitFor(
                    () => {
                        const candidate = iframeDocument.querySelector(
                            '.page[data-page-number="1"]',
                        );
                        return candidate?.dataset.uslEditorReady === "1" ? candidate : null;
                    },
                    "The first PDF page was not ready for field placement.",
                );
                const rectangle = firstPage.getBoundingClientRect();
                firstPage.dispatchEvent(new MouseEvent("click", {
                    bubbles: true,
                    clientX: rectangle.left + rectangle.width * 0.7,
                    clientY: rectangle.top + rectangle.height * 0.7,
                }));
                const firstField = await waitFor(
                    () => iframeDocument.querySelector(".o_sign_oca_field"),
                    "Click placement did not create the field.",
                );
                firstField.click();
                const inspector = document.querySelector(".usl_sign_field_inspector");
                inspector.style.height = "260px";
                if (getComputedStyle(inspector).overflowY !== "auto") {
                    throw new Error("The field inspector is not scrollable.");
                }
                inspector.scrollTop = inspector.scrollHeight;
                const deleteButton = await waitFor(
                    () => Array.from(inspector.querySelectorAll("button")).find(
                        (button) => button.textContent.includes("Delete field"),
                    ),
                    "The field delete action is missing.",
                );
                deleteButton.click();
                await waitFor(
                    () => iframeDocument.querySelectorAll(".o_sign_oca_field").length === 0,
                    "The field inspector did not delete the field.",
                );

                firstPage.dispatchEvent(new MouseEvent("click", {
                    bubbles: true,
                    clientX: rectangle.left + rectangle.width * 0.65,
                    clientY: rectangle.top + rectangle.height * 0.65,
                }));
                const source = await waitFor(
                    () => iframeDocument.querySelector(".o_sign_oca_field"),
                    "The replacement Initials field was not created.",
                );
                source.click();
                const everyPageButton = await waitFor(
                    () => Array.from(inspector.querySelectorAll("button")).find(
                        (button) => button.textContent.includes("Place on every page"),
                    ),
                    "The every-page action is missing.",
                );
                everyPageButton.click();
                await waitFor(
                    () => iframeDocument.querySelectorAll(".o_sign_oca_field").length === 3,
                    "The Initials field was not copied to every page.",
                );
                source.dispatchEvent(new MouseEvent("contextmenu", {
                    button: 2,
                    bubbles: true,
                    cancelable: true,
                }));
                await waitFor(
                    () => iframeDocument.querySelectorAll(".o_sign_oca_field").length === 2,
                    "Right-click did not delete the selected field.",
                );
                console.log("test successful");
            })();
            """,
            ready="Boolean(document.querySelector('.usl_sign_editor'))",
            login=self.workspace_user.login,
            timeout=90,
        )

    def test_requester_prepares_sends_and_monitors_routine_agreement(self):
        self.company.email = "requester-browser@example.test"
        template = self.env["sign.oca.template"].create(
            {
                "name": "Browser routine agreement template",
                "filename": "browser-routine-agreement.pdf",
                "data": field_value(self._pdf()),
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
                    input.value = value;
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
                    "The Request signatures dialog did not open.",
                );
                const templateChoice = startDialog.querySelector(
                    'input[type="radio"][data-value="template"]',
                );
                if (!templateChoice) {{
                    throw new Error("The template starting point is missing.");
                }}
                templateChoice.click();
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
                        (dialog) => dialog.textContent.includes("1. Choose the people"),
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
                    "Ready. Each signer receives a private link",
                    "Review request",
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
                    "Signers",
                    "Signing method",
                    "Deadline",
                    "Status",
                    "Overview",
                    "Files",
                    "Method, result & proof",
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
            ready="document.querySelectorAll('.usl_sign_work_card').length === 5",
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

    def test_native_library_opens_on_creation_first_templates(self):
        action = self.env.ref("sign_oca.sign_oca_template_act_window")
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
                const upload = await waitFor(
                    () => Array.from(document.querySelectorAll("button")).find(
                        (button) => button.textContent.trim() === "Upload PDF",
                    ),
                    "The primary Upload PDF action is missing.",
                );
                if (!upload.classList.contains("btn-primary")) {
                    throw new Error("Upload PDF is not the primary Templates action.");
                }
                if (!document.querySelector(".o_kanban_renderer")) {
                    throw new Error("Templates is not using the native template kanban.");
                }
                console.log("test successful");
            })();
            """,
            ready=(
                "Boolean(document.querySelector('.o_kanban_renderer'))"
            ),
            login=self.workspace_user.login,
            timeout=60,
        )

    def test_pocket_id_enrollment_page_uses_company_facing_copy(self):
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Public journey copy fixture",
                "policy_version": "browser-acceptance-v1",
                "review_note": "Identity reviewed after Pocket ID connection.",
            },
        )
        action = enrollment.with_user(self.reviewer).action_copy_invitation()
        self.assertEqual(action["tag"], "usl_sign.copy_setup_link")
        invitation_url = action["params"]["url"]
        response = self.url_open(urlsplit(invitation_url).path)
        self.assertEqual(response.status_code, 200)
        page = response.text
        self.assertIn("Connect your signing identity", page)
        self.assertIn(self.company.name, page)
        self.assertIn("Your account stays under your control", page)
        self.assertIn("This only connects your account", page)
        self.assertIn("Private signing session", page)
        self.assertIn("usl-sign-motion--identity", page)
        self.assertIn("it does not sign a document", page)
        self.assertNotIn("Pocket ID verified", page)
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertIn(
            "publickey-credentials-get=()",
            response.headers["Permissions-Policy"],
        )
        self.browser_js(
            urlsplit(invitation_url).path,
            """
            (() => {
                const enrollment = document.getElementById("usl_strong_enrollment");
                const button = document.getElementById("usl_enroll_button");
                if (enrollment?.dataset.ready !== "true" || button?.disabled) {
                    throw new Error("Pocket ID enrollment is incorrectly blocked on HTTP.");
                }
                if (document.body.textContent.includes("A secure browser is required")) {
                    throw new Error("Enrollment displayed the Strong-signing HTTPS gate.");
                }
                console.log("test successful");
            })();
            """,
            ready="document.getElementById('usl_strong_enrollment')?.dataset.ready === 'true'",
            timeout=60,
        )

    def test_strong_signing_page_is_focused_company_facing_and_isolated(self):
        self.company.email = "sign-browser@example.test"
        enrollment = self.env["usl.sign.enrollment"].create(
            {
                "partner_id": self.partner.id,
                "company_id": self.company.id,
                "relationship_basis": "recurring_partner",
                "relationship_reference": "Strong public-page fixture",
                "policy_version": "browser-acceptance-v1",
                "review_note": "Known recurring signer reviewed for acceptance.",
            },
        )
        enrollment._bind_pocket_identity(
            issuer="http://pocket-id.localhost:1411",
            claims={"sub": "strong-page-fixture", "name": self.partner.name},
        )
        enrollment.with_user(self.reviewer).action_confirm_identity()
        role = self.env.ref("sign_oca.sign_role_customer")
        initials = self.env.ref("usl_sign.field_initials")
        signature = self.env.ref("sign_oca.sign_field_signature")
        signatory_data = {
            str(page): {
                "id": page,
                "field_id": initials.id,
                "field_type": initials.field_type,
                "kind": "initials",
                "required": True,
                "name": initials.name,
                "role_id": role.id,
                "tabindex": page,
                "page": page,
                "position_x": 72,
                "position_y": 88,
                "width": 14,
                "height": 6,
                "value": False,
                "default_value": initials.default_value,
                "placeholder": "",
            }
            for page in range(1, 5)
        }
        signatory_data["5"] = {
            "id": 5,
            "field_id": signature.id,
            "field_type": signature.field_type,
            "kind": "signature",
            "required": True,
            "name": signature.name,
            "role_id": role.id,
            "tabindex": 5,
            "page": 4,
            "position_x": 12,
            "position_y": 65,
            "width": 28,
            "height": 9,
            "value": False,
            "default_value": signature.default_value,
            "placeholder": "",
        }
        sign_request = self.env["sign.oca.request"].create(
            {
                "name": "Material agreement",
                "data": field_value(self._pdf(pages=4)),
                "filename": "material-agreement.pdf",
                "company_id": self.company.id,
                "user_id": self.env.user.id,
                "policy_id": self.env.ref(
                    "usl_sign.policy_material_recurring_strong",
                ).id,
                "document_category": "commercial",
                "signer_type": "recurring",
                "risk_level": "material",
                "requested_trust": "strong_personal",
                "signatory_data": signatory_data,
                "signer_ids": [
                    (0, 0, {"partner_id": self.partner.id, "role_id": role.id}),
                ],
            },
        )
        sign_request.action_mark_ready()
        with patch.object(
            type(sign_request.signer_ids),
            "_send_signer_invitation",
            return_value=True,
        ), patch.object(
            type(sign_request),
            "_sign_dss_client",
            return_value=Mock(
                prepare_signing_fields=lambda document, fields: document,
            ),
        ):
            sign_request.action_send()
        signer = sign_request.signer_ids
        session_token = signer._exchange_access_token(signer._issue_access_token())
        response = self.url_open(
            f"/sign/session/{signer.id}/{session_token}?review=1",
        )
        self.assertEqual(response.status_code, 200)
        page = response.text
        self.assertIn('id="usl_strong_sign_context"', page)
        self.assertIn("usl_sign.document_portal", page)
        self.assertNotIn("/usl_sign/static/src/js/strong_sign.js", page)
        self.assertNotIn('id="usl_strong_sign_button"', page)
        self.assertNotIn("Exact document SHA-256", page)
        self.assertNotIn("Pocket ID verified", page)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("'nonce-", response.headers["Content-Security-Policy"])
        self.assertIn(
            "style-src-attr 'unsafe-inline'",
            response.headers["Content-Security-Policy"],
        )
        self.assertNotIn(
            "script-src 'self' 'unsafe-eval' 'unsafe-inline'",
            response.headers["Content-Security-Policy"],
        )
        self.assertIn(
            "publickey-credentials-get=()",
            response.headers["Permissions-Policy"],
        )
        self.browser_js(
            f"/sign/session/{signer.id}/{session_token}?review=1",
            """
            (async () => {
                for (let attempt = 0; attempt < 240; attempt++) {
                    const iframe = document.querySelector("iframe");
                    if (
                        iframe?.contentDocument?.querySelector(
                            '.o_sign_oca_field[data-field="5"] [role="button"]',
                        ) && document.getElementById("sign_oca_button") &&
                        iframe.contentDocument.querySelector(
                            ".o_sign_sign_item_navigator",
                        )?.textContent.includes("Click to start")
                    ) {
                        break;
                    }
                    await new Promise((resolve) => setTimeout(resolve, 50));
                }
                const iframe = document.querySelector("iframe");
                const signatureField = iframe?.contentDocument?.querySelector(
                    '.o_sign_oca_field[data-field="5"] [role="button"]',
                );
                const header = document.querySelector(".usl_sign_portal_header");
                const guide = iframe?.contentDocument?.querySelector(
                    ".o_sign_sign_item_navigator",
                );
                const submit = document.getElementById("sign_oca_button");
                const assignedFields = Array.from(
                    iframe?.contentDocument?.querySelectorAll(".o_sign_oca_field") || [],
                );
                if (
                    !signatureField || assignedFields.length !== 5 || !guide ||
                    !submit || !submit.disabled
                ) {
                    throw new Error("Strong did not render the shared incomplete field workspace.");
                }
                if (
                    document.querySelectorAll(".o_sign_oca_footer").length !== 1 ||
                    document.querySelector("#usl_sign_submission_status")
                ) {
                    throw new Error("Strong rendered a competing or duplicate signer status surface.");
                }
                if (
                    typeof window.uslStrongCeremony !== "function" ||
                    typeof window.uslStrongSign !== "undefined"
                ) {
                    throw new Error("Strong did not expose the ceremony-only adapter.");
                }
                const ceremonySource = String(window.uslStrongCeremony);
                if (
                    ceremonySource.includes("sign_oca_button") ||
                    ceremonySource.includes("usl_sign_submission_status")
                ) {
                    throw new Error("The Strong ceremony still owns shared signer UI state.");
                }
                const fieldBox = signatureField.closest(".o_sign_oca_field").getBoundingClientRect();
                const fieldContainer = signatureField.closest(".o_sign_oca_field");
                const fieldStyle = getComputedStyle(fieldContainer);
                if (
                    fieldStyle.position !== "absolute" ||
                    !fieldContainer.style.top ||
                    !fieldContainer.style.left ||
                    fieldBox.width < 20 ||
                    fieldBox.height < 20 ||
                    !signatureField.textContent.trim()
                ) {
                    throw new Error("Strong did not position the shared field over the PDF.");
                }
                if (!header?.textContent.includes("Strong personal signature")) {
                    throw new Error("Strong did not disclose its method in the shared workspace.");
                }
                const pdfViewer = iframe.contentWindow.PDFViewerApplication?.pdfViewer;
                const viewer = iframe.contentDocument.getElementById("viewerContainer");
                if (pdfViewer?.currentPageNumber !== 1 || viewer.scrollTop > 2) {
                    throw new Error("Strong did not start on the first PDF page.");
                }
                guide.click();
                await new Promise((resolve) => setTimeout(resolve, 450));
                if (document.querySelector(".modal") || !signatureField.isConnected) {
                    throw new Error("The optional guide filled a field instead of only navigating.");
                }
                if (!guide.textContent.includes("Next")) {
                    throw new Error("The Strong field guide did not advance from its start state.");
                }
                for (let step = 0; step < 4; step++) {
                    guide.click();
                    await new Promise((resolve) => setTimeout(resolve, 450));
                }
                if (
                    !signatureField.isConnected ||
                    iframe.contentDocument.activeElement !== signatureField ||
                    pdfViewer?.currentPageNumber !== 4
                ) {
                    throw new Error("The Strong field guide did not reach the final signature.");
                }
                const guideLine = iframe.contentDocument.querySelector(
                    ".o_sign_sign_item_navline",
                );
                const targetBox = signatureField.closest(
                    ".o_sign_oca_field",
                ).getBoundingClientRect();
                const lineStartX = Number.parseFloat(guideLine.style.left);
                const lineStartY = Number.parseFloat(guideLine.style.top);
                const lineLength = Number.parseFloat(guideLine.style.width);
                const lineAngle = Number.parseFloat(
                    guideLine.style.transform.match(/rotate\\(([-0-9.]+)rad\\)/)?.[1],
                );
                const lineEndX = lineStartX + Math.cos(lineAngle) * lineLength;
                const lineEndY = lineStartY + Math.sin(lineAngle) * lineLength;
                if (
                    guideLine.hidden ||
                    Math.abs(lineEndX - (targetBox.left - 3)) > 2 ||
                    Math.abs(lineEndY - (targetBox.top + targetBox.height / 2)) > 2
                ) {
                    throw new Error("The field guide line did not terminate on its target field.");
                }
                console.log("test successful");
            })();
            """,
            ready="Boolean(document.getElementById('sign_oca_button'))",
            timeout=60,
        )
        enrollment.with_user(self.reviewer).action_revoke(
            reason="Exercise the missing-identity signer guidance.",
        )
        unavailable_response = self.url_open(
            f"/sign/session/{signer.id}/{session_token}?review=1",
        )
        self.assertEqual(unavailable_response.status_code, 200)
        self.assertIn("Set up your signing identity first", unavailable_response.text)
        self.assertIn("personal setup link", unavailable_response.text)
        self.assertIn("usl-sign-motion--identity", unavailable_response.text)
        self.assertNotIn("id=\"usl_strong_sign_button\"", unavailable_response.text)
        self.assertIn(
            "frame-ancestors 'none'",
            unavailable_response.headers["Content-Security-Policy"],
        )

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
        initials_field = self.env.ref("usl_sign.field_initials")
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
            "7": {
                "id": 7,
                "field_id": initials_field.id,
                "field_type": initials_field.field_type,
                "kind": "initials",
                "required": True,
                "name": initials_field.name,
                "role_id": role.id,
                "tabindex": 7,
                "page": 1,
                "position_x": 53,
                "position_y": 88,
                "width": 24,
                "height": 8,
                "value": False,
                "default_value": initials_field.default_value,
                "placeholder": "",
            },
            "8": {
                "id": 8,
                "field_id": initials_field.id,
                "field_type": initials_field.field_type,
                "kind": "initials",
                "required": True,
                "name": initials_field.name,
                "role_id": role.id,
                "tabindex": 8,
                "page": 1,
                "position_x": 53,
                "position_y": 56,
                "width": 24,
                "height": 8,
                "value": False,
                "default_value": initials_field.default_value,
                "placeholder": "",
            },
        }
        sign_request = self.env["sign.oca.request"].create(
            {
                "name": "Browser Standard-signature acceptance",
                "data": field_value(self._pdf()),
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
                type(sign_request),
                "_sign_dss_client",
                return_value=FakeDSS(),
            ),
            patch.object(
                type(sign_request),
                "_completion_certificate_render",
                return_value=_renderer_result(self._pdf()),
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
            patch.object(
                type(self.env["usl.document"]),
                "upload_from_odoo",
                return_value={
                    "state": "duplicate",
                    "document_id": archived.id,
                    "message": "Checksum-identical dossier reused.",
                },
            ),
            patch.object(
                type(self.env["usl.document"]),
                "action_sync_permissions",
                autospec=True,
            ),
        ):
            self.browser_js(
                signing_path,
                """
                (async () => {
                    const iframe = document.querySelector(".o_sign_oca_iframe");
                    for (let attempt = 0; attempt < 400; attempt++) {
                        if (
                            iframe.contentDocument?.querySelector(".o_sign_oca_field") &&
                            iframe.contentDocument.querySelector(
                                ".o_sign_sign_item_navigator",
                            )?.textContent.includes("Click to start")
                        ) {
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
                    const renderedFields = Array.from(
                        iframe.contentDocument.querySelectorAll(".o_sign_oca_field"),
                    );
                    if (renderedFields.some((field) => {
                        const box = field.getBoundingClientRect();
                        return box.width < 12 || box.height < 12;
                    })) {
                        throw new Error("A Standard signing field rendered without a usable target.");
                    }
                    const actions = document.querySelector(".usl_sign_portal_actions");
                    if (!actions) {
                        throw new Error("The desktop signer actions are missing.");
                    }
                    const footer = document.querySelector(".o_sign_oca_footer");
                    const guide = iframe.contentDocument.querySelector(
                        ".o_sign_sign_item_navigator",
                    );
                    if (!footer || getComputedStyle(footer).display === "none" || !guide) {
                        throw new Error("Incomplete fields hid the manual signing controls.");
                    }
                    if (footer.scrollWidth > footer.clientWidth + 1 || footer.clientHeight > 210) {
                        throw new Error("The signer footer overflows or obscures too much of the document.");
                    }
                    if (guide.getAttribute("role") !== "button" || guide.tabIndex !== 0) {
                        throw new Error("The restored field guide is not keyboard accessible.");
                    }
                    const guideBox = guide.getBoundingClientRect();
                    if (getComputedStyle(guide).position !== "fixed" || guideBox.left > 2) {
                        throw new Error(
                            `The field guide is not attached to the left document edge: ` +
                            `left=${guideBox.left}, cssLeft=${getComputedStyle(guide).left}, ` +
                            `parent=${guide.parentElement?.tagName}.${guide.parentElement?.className || ""}.`,
                        );
                    }
                    const firstInput = iframe.contentDocument.querySelector(
                        '.o_sign_oca_field input[type="text"]',
                    );
                    const firstValueBeforeGuide = firstInput?.value;
                    guide.click();
                    await new Promise((resolve) => setTimeout(resolve, 80));
                    if (!firstInput || firstInput.value !== firstValueBeforeGuide) {
                        throw new Error("The optional guide filled a field automatically.");
                    }
                    if (!guide.textContent.includes("Next")) {
                        throw new Error("The field guide did not advance from its start state.");
                    }
                    const viewer = iframe.contentDocument.getElementById("viewerContainer");
                    viewer.scrollTop = Math.max(0, viewer.scrollHeight - viewer.clientHeight);
                    viewer.dispatchEvent(new WheelEvent("wheel", {deltaY: 40, bubbles: true}));
                    const manualScrollTop = viewer.scrollTop;
                    await new Promise((resolve) => setTimeout(resolve, 450));
                    if (Math.abs(viewer.scrollTop - manualScrollTop) > 2) {
                        throw new Error("Manual scrolling did not cancel guided navigation.");
                    }
                    if (!guide.isConnected || !guide.textContent.includes("Next")) {
                        throw new Error("Manual scrolling disabled the field guide.");
                    }
                    window.dispatchEvent(new Event("resize"));
                    await new Promise((resolve) => setTimeout(resolve, 30));
                    if (!firstInput.isConnected) {
                        throw new Error("A resize replaced the active signing fields.");
                    }
                    console.log("test successful");
                })();
                """,
                ready="Boolean(document.getElementById('sign_oca_button'))",
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
                    const guide = iframe.contentDocument.querySelector(
                        ".o_sign_sign_item_navigator",
                    );
                    const guideBox = guide?.getBoundingClientRect();
                    if (
                        !guideBox || guideBox.left < -1 ||
                        guideBox.right > iframe.contentDocument.documentElement.clientWidth + 1
                    ) {
                        throw new Error("The mobile field guide overflows the document viewport.");
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
                    const signatureControl = signatureField.querySelector('[role="button"]');
                    if (!signatureControl?.textContent.includes("Add signature")) {
                        throw new Error("The empty signature field has no visible action label.");
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
                    if (
                        document.querySelectorAll(".modal").length !== 1 ||
                        !document.querySelector(".modal")?.textContent.includes(
                            "Adopt Your Signature"
                        )
                    ) {
                        throw new Error(
                            "The signature field opened more than one dialog."
                        );
                    }
                    const signatureName = document.querySelector(
                        ".modal .o_web_sign_name_input",
                    );
                    signatureName.value = "Preferred Legal Name";
                    signatureName.dispatchEvent(new InputEvent("input", {bubbles: true}));
                    await new Promise((resolve) => setTimeout(resolve, 100));
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
                    const initialsField = iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field[data-field="7"]',
                    );
                    if (!initialsField) {
                        throw new Error("The initials field did not render.");
                    }
                    initialsField.click();
                    let initialsButton;
                    for (let attempt = 0; attempt < 200 && !initialsButton; attempt++) {
                        initialsButton = Array.from(
                            document.querySelectorAll(".modal .btn-primary"),
                        ).find((candidate) =>
                            candidate.textContent.includes("Adopt Initials")
                        );
                        if (!initialsButton || initialsButton.disabled) {
                            initialsButton = null;
                            await new Promise((resolve) => setTimeout(resolve, 50));
                        }
                    }
                    const initialsModal = document.querySelector(".modal");
                    if (
                        !initialsButton ||
                        document.querySelectorAll(".modal").length !== 1 ||
                        !initialsModal.textContent.includes("Adopt Your Initials") ||
                        initialsModal.textContent.includes("Adopt Your Signature")
                    ) {
                        throw new Error(
                            "The initials field did not open its dedicated dialog."
                        );
                    }
                    const initialsName = initialsModal.querySelector(
                        ".o_web_sign_name_input",
                    );
                    if (initialsName?.value !== "Preferred Legal Name") {
                        throw new Error(
                            "The initials dialog did not preserve the adopted full name."
                        );
                    }
                    initialsButton.click();
                    for (
                        let attempt = 0;
                        attempt < 200 && !iframe.contentDocument?.querySelector(
                            '.o_sign_oca_field[data-field="7"] img',
                        );
                        attempt++
                    ) {
                        await new Promise((resolve) => setTimeout(resolve, 50));
                    }
                    if (!iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field[data-field="7"] img',
                    )) {
                        throw new Error("The adopted initials were not placed.");
                    }
                    const repeatedInitials = iframe.contentDocument?.querySelector(
                        '.o_sign_oca_field[data-field="8"]',
                    );
                    for (
                        let attempt = 0;
                        attempt < 200 && (
                            document.querySelector(".modal") ||
                            !iframe.contentDocument?.querySelector(
                                '.o_sign_oca_field[data-field="8"] img',
                            )
                        );
                        attempt++
                    ) {
                        await new Promise((resolve) => setTimeout(resolve, 50));
                    }
                    if (
                        document.querySelector(".modal") ||
                        !repeatedInitials ||
                        !iframe.contentDocument?.querySelector(
                            '.o_sign_oca_field[data-field="8"] img',
                        )
                    ) {
                        throw new Error(
                            "Repeated initials did not reuse the adopted choice automatically."
                        );
                    }
                    emailInput.value = "invalid-address";
                    emailInput.dispatchEvent(new InputEvent("input", {bubbles: true}));
                    emailInput.dispatchEvent(new Event("change", {bubbles: true}));
                    const consent = document.getElementById("usl_sign_consent");
                    const privacy = document.getElementById("usl_sign_consent_privacy");
                    if (
                        !privacy?.textContent.includes("Sharing your approximate location is optional") ||
                        consent.getAttribute("aria-describedby") !== privacy.id ||
                        document.querySelector(".usl_sign_proof_details")
                    ) {
                        throw new Error("The concise signing privacy notice is missing or inaccessible.");
                    }
                    consent.checked = true;
                    consent.dispatchEvent(new Event("change", {bubbles: true}));
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
                    button.click();
                    let processing;
                    for (let attempt = 0; attempt < 50; attempt++) {
                        processing = document.getElementById("usl_sign_processing");
                        if (processing && document.activeElement === processing) {
                            break;
                        }
                        await new Promise((resolve) => setTimeout(resolve, 20));
                    }
                    const processingStatus = processing?.querySelector(
                        ".usl_sign_processing_status",
                    );
                    if (
                        !processing || document.activeElement !== processing ||
                        !processing.textContent.includes("Securing your signature") ||
                        !processingStatus?.textContent.includes(
                            "Saving and checking your signature",
                        ) ||
                        !processing.querySelector(".usl_sign_processing_scan_band") ||
                        !processing.querySelector(".usl_sign_processing_scan_edge") ||
                        getComputedStyle(
                            processing.querySelector(".usl_sign_processing_scan"),
                        ).animationName !== "usl-sign-processing-scan" ||
                        getComputedStyle(
                            processing.querySelector(".usl_sign_processing_signature"),
                        ).animationName !== "none" ||
                        processing.querySelector(
                            ".usl_sign_processing_spinner_track, .usl_sign_processing_spinner",
                        ) ||
                        processing.querySelector(
                            ".usl_sign_processing_check, .usl_sign_processing_seal",
                        ) ||
                        getComputedStyle(iframe).display !== "none" ||
                        getComputedStyle(document.querySelector(".o_sign_oca_footer")).display !==
                            "none" ||
                        document.querySelector("#usl_sign_submission_status")
                    ) {
                        throw new Error(
                            "Signing did not open the focused intermediary screen: " +
                            JSON.stringify({
                                processing: Boolean(processing),
                                activeElement: document.activeElement?.id,
                                text: processing?.textContent.trim(),
                                status: processingStatus?.textContent.trim(),
                                prematureSuccess: Boolean(
                                    processing.querySelector(
                                        ".usl_sign_processing_check, .usl_sign_processing_seal",
                                    ),
                                ),
                                iframeDisplay: getComputedStyle(iframe).display,
                                footerDisplay: getComputedStyle(
                                    document.querySelector(".o_sign_oca_footer"),
                                ).display,
                                duplicateStatus: Boolean(
                                    document.querySelector("#usl_sign_submission_status"),
                                ),
                            }),
                        );
                    }
                    const closeAttempt = new Event("beforeunload", {cancelable: true});
                    window.dispatchEvent(closeAttempt);
                    if (!closeAttempt.defaultPrevented) {
                        throw new Error("The active signing operation does not warn before closing.");
                    }
                    window.addEventListener(
                        "beforeunload",
                        () => console.log("test successful"),
                        {once: true},
                    );
                })();
                """,
                ready="Boolean(document.getElementById('sign_oca_button'))",
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
        self.assertTrue(sign_request.archive_dossier_document_id)
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
        self.assertTrue(
            sign_request.signatory_data["7"]["value"].startswith(
                "data:image/png;base64,",
            ),
        )
        self.assertEqual(
            sign_request.signatory_data["8"]["value"],
            sign_request.signatory_data["7"]["value"],
        )
        consent_evidence = sign_request.evidence_ids.filtered(
            lambda evidence: evidence.kind == "consent",
        )
        self.assertEqual(len(consent_evidence), 1)
        consent_payload = json.loads(field_content(consent_evidence.data))
        self.assertEqual(
            consent_payload["reviewed_document_sha256"],
            sign_request.original_sha256,
        )
        self.assertEqual(consent_payload["consent"], sign_request.consent_text_snapshot)
