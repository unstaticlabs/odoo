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
screenshot_01-open_drawer_png: a7ec22c269b49b94221e44e1a44a35692e16df0d67eebc86a0da9c23558888e1
screenshot_02-conversation_png: 0d2fe16506148c5cbbfa5f2e66bdfbe89e00133ec61f850523dfe80c85efc7fd
screenshot_03-chatter_png: ec8fc888e4f3ad6072f95bf38e198fb516d9c7babfd0d50b9f783b907ff25eb2
screenshot_04-recent_list_png: 1b25fc5ca76425057d7639207ce9d0c3a888e01a1aef5b547b51101da639ca90
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
