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

- **Include page preview** shares the image shown in the form. Review it and
  clear the option if it includes information the team does not need. The preview
  stays on your device until you send it. Messages, chats, alerts and password
  fields stay out of the preview.
- **Page details** shares context such as the action you opened, a record you can
  read, the company and the release. Clear it if the current page is unrelated.
  Odoo excludes inaccessible records, URLs, access tokens and form values.
- **Add files** attaches the files you select. Remove anything that does not help
  the team understand the report.

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
