---
title: Send feedback from any page
type: how-to
description: Report a problem, suggest an improvement or ask a question without leaving the screen you are on, then follow the answer in My feedback.
lang: en
persona: everyone
app: Feedback
models:
  - project.task
screen: /odoo
journey: send-feedback-from-any-page
tour: usl_feedback_desktop_journey
source:
  - custom-addons/usl_feedback/tests/test_feedback_journeys.py
screenshot_01-open_drawer_png: de5104017e8ca04ee9c37bb6ecdc904c07359dc2e2a2b3d37521f1f593092ef4
screenshot_02-conversation_png: e0f5359fbb0539167df171ca61d246a31cb5aa8a5b282837d792a22c57d181d8
screenshot_03-chatter_png: 4a0b4dad78de8b853d185e1d1f1a893dfc8523c699c4a5849f54f1e490bd47bc
screenshot_04-recent_list_png: 3916564a34e6b7f610981552aaa6a456b4bcbda649c5849eba4e3ed7b367e237
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Send feedback from any page

Report a problem, suggest an improvement or ask a question without leaving the screen you are on, then follow the answer in My feedback.

1. Open the **Messages** drawer from the top bar. **Feedback** sits beside **New Message** in every tab.

   ![Open the Messages drawer from the top bar](send-feedback-from-any-page/01-open_drawer.png)

2. Choose **Feedback**.

3. The drawer closes and a floating conversation opens, like a chat. Odoo prepares a preview of the page you are on; it stays on your computer until you decide to send it.

   ![The drawer closes and a floating conversation opens, like a chat](send-feedback-from-any-page/02-conversation.png)

4. Describe what you saw and what you expected, in your own words. One message is enough; the product team asks if it needs more.

5. **Page details** is ticked when the screen you are on gives useful context: the action, the record you can read, the company and the release, never the address, form values or passwords. Untick it when the page does not matter.

6. Choose **Send feedback**.

7. Your message becomes a card for the product team, and the conversation turns into its chatter: answers and status changes appear here.

   ![Your message becomes a card for the product team, and the conversation turns into its chatter: answers and status change](send-feedback-from-any-page/03-chatter.png)

8. **New** starts another conversation. The card you just sent is kept.

9. **My feedback** lists every card you sent with its current status.

10. Choose a card to reopen its conversation and reply.

11. Come back to **My feedback** at any time from the same drawer to see where each report stands.

   ![Come back to My feedback at any time from the same drawer to see where each report stands](send-feedback-from-any-page/04-recent_list.png)
