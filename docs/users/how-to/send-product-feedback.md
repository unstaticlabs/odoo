---
title: Choose what to share with product feedback
type: how-to
description: Control the page preview and context, understand who can see your feedback, and recover an interrupted conversation.
lang: en
persona: everyone
app: Feedback
models:
  - project.task
actions:
  - usl_feedback.action_feedback_maintainer
source:
  - custom-addons/usl_feedback
  - custom-addons/usl_feedback/static/tests/tours/feedback_tour.js
generated: false
---

# Choose what to share with product feedback

Use the illustrated guide to [send feedback from any page](send-feedback-from-any-page.md).
If the current page contains sensitive or unrelated details, choose what to
include before sending.

## Review what you are about to send

- **Include page preview** shares the image shown in the form. Check any record
  details or chatter visible in the image. Clear the option if the team only
  needs your description. Odoo leaves chat pop-ups, the messaging menu,
  notifications and password fields out of the image. The preview stays on
  your device until you send it.
- **Page details** shares context such as the action you opened, a record you can
  read, the company and the release. Clear it if the current page is unrelated.
  Odoo excludes inaccessible records, URLs, access tokens and form values.
- **Add files** attaches the files you select. Remove anything that does not help
  the team understand the report.

Check the assistant named in the form. In Gemini mode, Google receives the
feedback conversation, your language and company, and any page details you
include. Gemini can also receive up to three images from the report, including
the selected preview and eligible PNG or JPEG attachments. To help find related
reports, Odoo sends a summary of the shared feedback board with recent task
titles. If the Odoo Projects connection is configured, Gemini can also read
related feedback cards. Odoo asks Gemini to store the conversation so later
replies can use it. In local mode, Odoo generates the replies itself.

Workspace members can read and discuss feedback and its files. Feedback
maintainers manage task fields and stages. Sharing a link in feedback keeps the
source record's existing access rules.

## Recover a saved report

If the assistant cannot reply, the conversation says your report is saved.
Select **Try again** to resume. If the preview is unavailable, attach an image
you have checked yourself or continue with a text description.

Find the conversation in **My feedback** to follow up. The feedback tab's action
menu can withdraw a report after confirmation. Withdrawal stops the workflow and
keeps the conversation and attached files.

[Lire le guide illustré en français](envoyer-un-retour-depuis-une-page.md).
