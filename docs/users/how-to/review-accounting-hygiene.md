# Review Accounting Hygiene

1. Open **Review > Accounting Hygiene**. For an Accounting Manager, opening
   the queue first refreshes Hygiene and the current closing controls from the
   latest accounting records. Read-only accountants open the current results
   without changing them.
2. Keep the default **Open** filter. Prioritize Blocking, then Warning, then Attention.
3. Use the queue's business columns first: severity, title, area, detection
   date, affected amount and responsible role. Technical definition and result
   fields remain available from the optional-columns menu when needed.
4. Open an issue. Start with **What needs attention** and **Recommended next
   action**, then use **Why it matters**, **Accounting consequence** and
   **Evidence used by this control** for review context.
5. Assign a user when one person owns the follow-up.
6. Select **Open Related Record** and correct the underlying draft, evidence,
   reconciliation or analytic allocation. A technical result opens its control
   configuration instead.
   - For **Analytic Allocation**, the related journal items open with **Analytic
     Distribution** visible.
   - Select the journal items that need the same allocation, edit **Analytic
     Distribution** on one selected line and confirm Odoo's native multi-edit
     prompt. The distribution is applied to the selected items only.
   - Use separate selections when the items need different analytic
     distributions. Posted general-ledger amounts are not changed; Odoo updates
     the native analytic lines associated with those journal items.
7. Return and select **Check Resolution**.
8. Dismiss only a reviewed occurrence that is deliberately acceptable.
   Dismissal hides that occurrence from the active queue without changing
   accounting or disabling the Control. The same unchanged records stay
   dismissed; new records or materially changed evidence make the result
   actionable again.

Open **Control and detection details** only when you need result type,
confidence, company scope or the first and latest detection timestamps.

Each result links to its visible definition under **Configuration > Controls**.
Informational results remain traceable without creating a Closing warning.
Technical failures mean the evaluator did not produce an accounting conclusion;
ask a Technical Administrator to inspect the advanced evaluator details.

The issue's **Dismissal history** shows who dismissed each occurrence, when,
the evidence that was reviewed and when changed evidence reopened it. Use
**Configuration > Controls**—not **Dismiss**—when an Accounting Manager intends
to disable a Control for future evaluations.

Hygiene uses deterministic, configured controls. It does not post automatically
and does not claim probabilistic or AI matching.
