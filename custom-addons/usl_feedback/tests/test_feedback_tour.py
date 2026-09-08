from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


class FeedbackTourCommon(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env,
            login=f"feedback-tour-{cls.__name__.lower()}",
            password="feedback-tour",
            groups="base.group_user",
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.env["ir.config_parameter"].sudo().set_str(
            "usl.release.commit",
            "b" * 40,
        )


@tagged("post_install", "-at_install", "usl_feedback_tour")
class TestFeedbackDesktopTour(FeedbackTourCommon):
    browser_size = "1440x900"

    def test_desktop_feedback_tour(self):
        self.start_tour("/odoo", "usl_feedback_desktop_journey", login=self.user.login)
        task = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", self.user.id)],
            limit=1,
        )
        self.assertEqual(task.name, "The desktop status is unclear after reload.")
        self.assertFalse(task.usl_feedback_context_included)


@tagged("post_install", "-at_install", "usl_feedback_tour", "mobile")
class TestFeedbackMobileTour(FeedbackTourCommon):
    browser_size = "390x844"

    def test_mobile_feedback_tour(self):
        self.start_tour("/odoo", "usl_feedback_mobile_journey", login=self.user.login)
        task = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", self.user.id)],
            limit=1,
        )
        self.assertEqual(task.name, "The mobile status is unclear after reload.")
        self.assertFalse(task.usl_feedback_context_included)


@tagged("post_install", "-at_install", "usl_feedback_capture")
class TestFeedbackWideCapture(FeedbackTourCommon):
    browser_size = "5120x1440"

    def test_wide_overflow_and_document_canvas_capture(self):
        self.browser_js(
            "/odoo?debug=assets",
            """(async () => {
                const { captureFeedbackPagePreview } = odoo.loader.modules.get(
                    "@usl_feedback/js/feedback_page_preview");
                const { toSvg } = odoo.loader.modules.get("@usl_feedback/lib/html_to_image");
                const root = document.createElement("main");
                root.style.cssText = "width:50000px;height:1440px;background:rgb(20,160,60);position:fixed;top:0;left:0";
                const privateNode = document.createElement("div");
                privateNode.dataset.uslFeedbackPrivate = "true";
                privateNode.style.cssText = "position:absolute;top:0;left:0;width:300px;height:300px;background:red";
                root.append(privateNode);
                const canvas = document.createElement("canvas");
                canvas.width = 20000;
                canvas.height = 64;
                canvas.style.cssText = "position:absolute;top:400px;left:0;width:5120px;height:64px";
                canvas.getContext("2d").fillRect(0, 0, canvas.width, canvas.height);
                root.append(canvas);
                document.body.append(root);
                try {
                    const svg = decodeURIComponent((await toSvg(root, {
                        width: 50000, height: 1440, canvasWidth: 1920, canvasHeight: 55, skipFonts: true,
                    })).split(",").slice(1).join(","));
                    const parsed = new DOMParser().parseFromString(svg, "image/svg+xml").documentElement;
                    if (parsed.getAttribute("width") !== "1920" || parsed.getAttribute("viewBox") !== "0 0 50000 1440") {
                        throw new Error("Intermediate SVG is not bounded without reflow");
                    }
                    const preview = await captureFeedbackPagePreview({ root });
                    if (preview.width !== 1920 || preview.height !== 540 || preview.blob.size > 5 * 1024 * 1024) {
                        throw new Error("Wide capture exceeds its viewport or output limits");
                    }
                    const image = new Image();
                    image.src = preview.previewUrl;
                    await image.decode();
                    const pixels = document.createElement("canvas");
                    pixels.width = image.width; pixels.height = image.height;
                    pixels.getContext("2d").drawImage(image, 0, 0);
                    const pixel = pixels.getContext("2d").getImageData(10, 10, 1, 1).data;
                    if (pixel[1] < 140 || pixel[0] > 40) {
                        throw new Error("Capture is blank or includes the private overlay");
                    }
                    preview.release();
                    const NativeImage = window.Image;
                    try {
                        window.Image = class {
                            set src(value) { queueMicrotask(() => this.onload()); }
                            decode() { return Promise.reject(new Error("Synthetic decode failure")); }
                        };
                        let rejected = false;
                        try { await captureFeedbackPagePreview({ root }); }
                        catch (error) { rejected = error.name === "EncodingError"; }
                        if (!rejected) { throw new Error("Decode rejection did not settle capture"); }
                    } finally { window.Image = NativeImage; }
                } finally { root.remove(); }
                console.log("test successful");
            })()""",
            ready="odoo.loader?.modules.has('@usl_feedback/js/feedback_page_preview')",
            login=self.user.login,
            timeout=120,
        )


@tagged("post_install", "-at_install", "usl_feedback_tour")
class TestFeedbackMaintainerDirectTour(HttpCase):
    browser_size = "1440x900"

    def test_maintainer_opens_cards_from_the_board(self):
        maintainer = new_test_user(
            self.env,
            login="feedback-tour-maintainer",
            password="feedback-tour",
            groups="usl_feedback.group_feedback_maintainer",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )
        for tour in (
            "usl_feedback_maintainer_form_journey",
            "usl_feedback_maintainer_quick_journey",
        ):
            self.start_tour(
                "/odoo/action-usl_feedback.action_feedback_maintainer",
                tour,
                login=maintainer.login,
            )
        cards = self.env["project.task"].sudo().search(
            [("usl_feedback_reporter_id", "=", maintainer.id)],
        )
        self.assertEqual(
            set(cards.mapped("name")),
            {"Keyboard shortcut for triage", "Batch export presets"},
        )
        for card in cards:
            self.assertTrue(card.project_id.usl_feedback_project)
            self.assertEqual(card.usl_feedback_agent_state, "triaged")
            self.assertEqual(card.usl_feedback_company_id, self.env.company)
            self.assertFalse(card.company_id)
        quick, full = (
            cards.filtered(lambda task: task.name == "Keyboard shortcut for triage"),
            cards.filtered(lambda task: task.name == "Batch export presets"),
        )
        self.assertEqual(quick.usl_feedback_category, "improvement")
        self.assertEqual(quick.stage_id, self.env.ref("usl_feedback.stage_feedback_new"))
        self.assertEqual(full.usl_feedback_category, "ux")
        self.assertEqual(full.stage_id, self.env.ref("usl_feedback.stage_feedback_declined"))
