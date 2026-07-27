# Reconciliation and Residuals

Reconciliation links debit and credit journal items that settle the same economic balance.

A full reconciliation leaves no residual. A partial reconciliation keeps the links but leaves an amount open for a later payment, refund, write-off or correction. The matching-reference chip identifies the linked set and uses the same color wherever it appears.

Bank Matching starts from a bank transaction and searches for its likely counterpart. General Reconciliation starts from reconcilable ledger accounts and is useful for receivables, payables, clearing and intermediary accounts.

Rules-based candidate filters prioritize opposite-sign amounts and nearby dates. They are removable aids, not autonomous decisions. Undo removes the selected reconciliation links; it does not delete the underlying documents or journal entries.

Bank matching rules and smart partner inference have different responsibilities:

- partner inference identifies a likely counterparty and retains its source and
  confidence;
- a bank matching rule proposes or creates a specific accounting counterpart;
- invoice and payment suggestions rank existing accounting items without
  creating another accounting representation.

For that reason, old partner-only reconciliation models are redundant in the
current rebuild. Rules remain appropriate for stable direct-accounting patterns
such as bank charges. Rule suggestions are configuration proposals, not runtime
actions: deterministic or AI-authored suggestions remain inert until an
Accounting Manager approves them.
