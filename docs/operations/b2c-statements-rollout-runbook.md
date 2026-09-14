# Rolling out the money-handler statements

What #212 changes about a running database, in the order a release meets it, and
how to know each step did what it was meant to. `b2c-channel-imports.md` says how
to set a shop up and how the tool is used; this says how the change reaches a
database that already holds a year of B2C history, and how to get back if it
should not have.

The change is unusually safe in one respect and unusually exposed in another. It
posts nothing at deploy time — the only thing the release itself writes is a
record of what the historical reconstruction already settled. But the first time
somebody presses **Settle the drop** on production it posts supplier bills,
payments out of clearing accounts and bank movements, and there is no button that
takes those back. So the deploy is cheap to reverse and the first run is not, and
the two want to be kept apart.

## What the release itself does

Installing or upgrading `usl_b2c_ingest` runs `adopt_reconstruction` — on install
through `post_init_hook`, on upgrade through
`migrations/saas~19.3.1.2.0/post-adopt-reconstruction.py`. Production is upgraded,
a clone is installed into, and both reach the same code.

It reads every posted entry whose reference matches the reconstruction's
vocabulary (`etsy:wallet:`, `stripe:fees:`, `revolut:fees:`,
`printful:wallet:consumption:`) and writes one `b2c.channel.settlement` or
`b2c.supply.settlement` per month it finds. **It posts no accounting entry and
changes no existing record.** Its whole purpose is to let the tool see that those
months are already settled, so the first drop over an old export bills nothing.

It is idempotent: each month is adopted once, guarded by a search on the
settlement it would create. Running the upgrade twice adopts nothing the second
time.

The two rows it writes are new models, so nothing that exists today reads them.
**Deleting every row they hold returns the database to exactly its state before
the upgrade** — which is what makes the deploy reversible.

*Worked when*: the upgrade log carries both lines —

```
Adopted N month(s) of commission the reconstruction settled.
Adopted N fulfilment(s) the reconstruction already settled.
```

and no `account.move` was created by the upgrade. A warning of the form
`<ref> booked X of supply where Odoo holds Y of fulfilment` is **not** a failure:
it says the reconstruction and Odoo count a month differently, posts nothing, and
is there so somebody can look. Note which months it names.

## Before the release goes anywhere

The branch is behind `19-usl-staging` and must be brought current before it is
qualified. Merge staging in — never rebase, the branch is shared — then re-run
both seals, because the sealed action surface is only meaningful against the tree
that ships:

```bash
git fetch origin 19-usl-staging
git merge origin/19-usl-staging          # merge, not rebase
make action-risk-discover                # runtime discovery; needs the stack up
make action-risk-refresh
make action-risk-inventory               # must print PASS
```

A static-only discovery drops around 350 route and server actions, so the
candidate has to come from a running database. `check-source` alone is not the
gate; the runtime check is.

## On staging

Staging carries a restored production clone, so this is the rehearsal that
counts.

1. **Let the release land and prove it deployed.** The merge train's rules apply
   unchanged — a push run on `19-usl-staging` that completed successfully and has
   your merge commit as an ancestor, then
   `scripts/check-staging-deployment --repository unstaticlabs/odoo --sha <merge commit> --head-ref 19-usl-staging`,
   then `scripts/usl-stack-observe staging release` reading `admitted`.

2. **Read the adoption in the upgrade log**, as above. This is the only part of
   the change that runs unattended against real data, and staging is where you
   find out what it did to a real ledger rather than to a fixture.

3. **Check the ledger did not move.** The adoption posts nothing, so the trial
   balance either side of the upgrade is identical. If it is not, stop: something
   other than adoption ran.

4. **Do the configuration by hand**, following *Setting a shop up for the first
   time* in `b2c-channel-imports.md`. Two parts of it are worth calling out:

   - **Converting the three Miscellaneous journals to Bank** is a change to
     production-shaped accounting data, not a setting. Odoo recomputes payment
     methods when the type changes. Do it on staging first and confirm each
     journal shows one inbound and one outbound method — a journal without them
     makes `_pay_from_clearing` fail at the moment it registers a payment, which
     is halfway through a run rather than before it.
   - **Set `fiscalyear_lock_date` and `tax_lock_date`** to the last filed period
     before running anything. The tool refuses to write behind them and reports
     what it would have posted instead. That refusal is the strongest guarantee
     available that a filed period stays as filed, it costs nothing, and it is
     the difference between a mistake that is reported and one that is posted.

5. **Drop the real exports and settle them.** Then check the four accounts named
   in `b2c-channel-imports.md` behave: the clearing accounts fall to nil, the
   wallet holds what the supplier says it holds, and the suspense account holds
   one entry per payout and per top-up.

6. **Run it a second time, changing nothing.** A second **Settle the drop** must
   post nothing at all and leave every balance where the first left it. This is
   the property the whole design rests on, and it is cheap to check.

## On production

Promotion carries the whole staging head, one entry at a time, as always. What is
specific to this change is what happens after it lands:

- **The upgrade runs the adoption against the real ledger.** Read the same two
  log lines and the same warnings. The months it names are the months to look at
  before anybody settles anything.
- **Do not settle a drop on the day of the deploy.** Nothing forces the two to
  happen together: the release only teaches the database what is already settled.
  Configure, verify the adoption, and settle when somebody is watching.
- **Set the lock dates before the first run**, if they are not already set. On
  production this is the control that matters most.

### If it has to come back

The deploy itself reverts like any other — promote the previous staging head.
Because the release posts nothing, no accounting entry needs unwinding, and the
adoption rows can be dropped:

```python
from odoo.addons.usl_b2c_ingest.hooks import CHANNEL_FAMILIES, SUPPLY_FAMILY

refs = [family[0] for family in CHANNEL_FAMILIES.values()]
env["b2c.channel.settlement"].search(
    [("batch_id", "=", False), ("move_id.ref", "=like", "%")],
).filtered(
    lambda row: any(row.move_id.ref.startswith(ref) for ref in refs),
).unlink()
env["b2c.supply.settlement"].search(
    [("batch_id", "=", False)],
).filtered(
    lambda row: (row.move_id.ref or "").startswith(SUPPLY_FAMILY[0]),
).unlink()
```

Adoption rows name no batch and their document is a reconstruction entry; both
conditions together identify them. `batch_id` alone is not enough — it is
`ondelete="set null"`, so a row whose drop was later deleted also has none.
Both models are `ondelete="restrict"` towards their document, so this deletes the
record of settlement and never the document itself.

**Once a drop has been settled, this is no longer the way back.** By then real
bills, payments and entries are posted, and they are reversed as accounting is
reversed — credit notes and reversing entries, through the accounting app, not by
deleting rows.

## Known, carried deliberately

- **Revolut Merchant is not read.** Its clearing accounts are at nil and no new
  Revolut sales appear, so nothing accumulates. The adoption still recognises
  `revolut:fees:` months, so turning it on later starts from a true position.
- **Bank lines are matched by hand**, four kinds of them. That is the intended
  end state of this change, not a gap in it.
- **A month the books are closed to is reported, not posted.** Expect findings
  naming closed months on the first run over a statement that reaches back years.
  They are the lock dates doing their job.
- **`_expense_booked` reads an entry's balance in company currency** while the
  settlement it writes is stamped with the currency named in the reference. For
  the reconstruction's own months that only differs where a reference named a
  currency other than the company's, which today is Revolut alone. It is worth
  correcting before a second currency is turned on.
- **The adoption pairs a provider with the first channel it finds carrying that
  code**, across companies. This distribution runs one company for B2C, so it
  resolves correctly today; it would mis-attribute in a second company holding
  its own Etsy channel.
