# Exchange gain/loss entry-direction guard

## Product decision

French exchange-loss accounts `666…` are normally debited and exchange-gain
accounts `766…` are normally credited. A user-created draft that uses either
family on the other side must therefore be reviewed before posting.

Odoo's account type already carries the normal expense/income meaning, but
standard Community configuration does not provide a per-account directional
posting warning. A hard debit-only or credit-only constraint was rejected:
native exchange adjustments legitimately reverse earlier rate effects, and
refunds or formal reversals must preserve their opposite accounting direction.

The selected approach is a narrow configurable confirmation:

- **Automatic from French Account Code** protects `666…` as normally debit and
  `766…` as normally credit;
- an Accounting Manager can explicitly choose **No Direction Check**,
  **Normally Debit** or **Normally Credit** on any account;
- a normal user-created draft displays the affected account and expected side;
- posting remains blocked until the user corrects the line or selects
  **Confirm exceptional direction**;
- confirmation is tied to the exact affected lines and amounts, so editing
  them requires confirmation again;
- the confirmation is recorded in the journal entry's chatter.

## Safe exceptions

The guard does not interfere with:

- native exchange-difference entries;
- customer or supplier refunds;
- Odoo formal reversals linked through `reversed_entry_id`;
- source-traced reconstruction of historical accounting.

An unlinked manual correction is not silently classified as a reversal. It
requires the explicit confirmation so its exceptional direction remains
visible and attributable.

## Configuration and agent contract

Open **Accounting > Configuration > Chart of Accounts**, select an account and
use **Entry Direction Check**. The default is automatic and has no effect on
accounts outside `666…` and `766…`.

The configured policy, computed warning, confirmation state and chatter
evidence use normal structured Odoo fields. A future MCP client can inspect the
same facts before deciding whether to correct or confirm a draft.
