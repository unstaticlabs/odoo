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
screenshot_01-open_drawer_png: f078fb8fc78cdd3cec29e26d13f0312029944f6be991317aa57e13dddf47a837
screenshot_02-conversation_png: 7b96c4ca832900e2660cb2b588e7a8b72317fd54940e9dbe69311edd6b381c0a
screenshot_03-chatter_png: 429428dfce95619f5efd390783238da4066bca7ec08b0314e3c0409a2f1bf8a7
screenshot_04-recent_list_png: 5ebd013bd0d01f69299a236a85e88658e67fd7e77a9efa84428467cfa2335c87
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
