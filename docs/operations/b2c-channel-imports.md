# Importing channel exports

The channels email a CSV; this turns it into commerce, sales, stock and
accounting. Nothing outside a batch changes until the batch is applied, and
every stage can be run again without adding anything.

## What a drop is read as

| Export | What it is believed for |
| --- | --- |
| Etsy — sold orders | The money Etsy charged and kept, and the destination |
| Etsy — sold order items | What was bought, and each transaction's identity |
| Medusa — orders | The destination and the carriage, discount and tax |
| Medusa — sold items per order | The lines, one row per order |
| Medusa — sold items | The lines, one row per line |
| Printful (read over its API) | What fulfilment cost, and when it shipped |
| Etsy — payment account statement | What Etsy kept, and what it paid out |
| Stripe — balance history | What Stripe kept, and what it paid out |
| Printful — wallet transactions | What drew on the wallet, and what topped it up |

A file is recognised by the columns it declares, not by its name or the order
of them. An order export is matched on the whole set; a statement on the columns
its writer always writes, because Stripe adds one column per metadata key an
account happens to use and nothing about that changes what the file is. A file
no parser knows is reported, naming the columns it lacks and the closest export.

**An order export is not an account of what a channel kept.** Etsy's sold-orders
file states the card processing fee and nothing else — not the transaction fee,
the listing fee, the regulatory fee or the advertising — and Medusa's states
nothing at all, because Medusa keeps nothing and Stripe keeps everything. Drop
the statements as well, or the clearing accounts keep the difference for ever.
When one is missing the drop still completes, saying which account will keep it;
dropping the statement later bills exactly the difference.

Medusa's item exports carry neither the destination nor the carriage. Without
the destination there is no rate to charge, so a drop of those alone is stopped
and the **orders** export is asked for by name. Export all three.

## The routine

1. **B2C → Channel Imports → New.** Drop the files in. Name the drop.
2. **Read the files.** Nothing outside the batch changes. The report says how
   many orders are new, how many Odoo already holds, and what needs attention.
3. **Resolve products.** Every line is matched to the product it sold, through
   the mappings already confirmed. A variant matched exactly is *derived*, and
   confirming it writes the mapping so the next sale of the same thing needs
   nobody. Anything else says what it needs: a product Odoo does not know, a
   name pointing at two products, or an attribute with no value for what the
   channel sold — which can be added from the finding itself.
4. **Read the supplier.** Printful is asked what each order cost and when it
   shipped. A fulfilment whose reference no export carries is tied to its sale
   by recipient and date, or reported as tied to none.
5. **Check readiness.** What would make the drop post the wrong numbers, each
   finding stopping only the step it would spoil.
6. **Apply.** Contacts, canonical orders, sales orders priced at what was
   actually paid, and deliveries completed on the day the goods left.
7. **Reconcile.** What these exports now say has become of the orders they
   name: goods an earlier drop could not send because they had not left yet,
   and the sales the channel has since cancelled or refunded.
8. **Invoice.** One invoice per sale whose goods have gone out, dated the day
   of the sale, settled into the channel's clearing account. An order paid and
   not yet shipped is held instead, and becomes an invoice in the drop that
   says it shipped.
9. **Bill the commission.** One bill per channel per month, paid out of the
   same clearing account.
10. **Settle the supply.** One document per month for what the supplier drew on
    its wallet, paid out of the wallet itself.

What is left is the bank: each payout meets the clearing account, which by then
holds exactly what the payout brings, and each wallet top-up meets the wallet.

Every step can be run again. A drop applied, reconciled, invoiced, billed and
settled twice leaves the ledger exactly as the first run left it — which is
what makes re-dropping a wider export the way to correct a narrower one.

## What happens to an order that does not stand

A channel exports an order's present condition, not only that it once happened.
The word it uses is read as what became of the order, and an unknown word is
read as a sale that stands: channels invent vocabulary far more often than they
invent outcomes, and taking a word nobody recognises for a cancellation would
erase revenue that exists.

| The channel says | What is done |
| --- | --- |
| Nothing, or a word this does not know | Nothing. It is a sale. |
| Cancelled, and Odoo has no sale for it yet | No sale is created. It never became commerce. |
| Cancelled, before the goods left | The sale is cancelled and anything held for it is paid back. |
| Cancelled or refunded, after the goods left | A credit note is posted and paid back out of the clearing account. A cancellation of goods that have already gone out is also reported: whether they came back is a stock fact no export states. |
| Refunded in part | The credit note is drafted, not posted, and a finding names the amount. Which line was refunded decides the account and the rate, and that is a person's to say. |

Etsy names no refund at all. It restates the order's money after adjustment, so
a refund there is the difference between what the buyer paid and what the order
is now worth — which is also how an untouched order is told from one refunded
to nothing.

## Money for goods that have not left

VAT on a supply of goods falls due when the goods are delivered, so an order
paid and not yet shipped is not a sale yet and is not invoiced. What the
customer paid is recorded against the customer, which leaves the receivable in
credit — an advance — and states no revenue and no VAT.

The drop that says the order shipped sends the goods, invoices it, and settles
the invoice against the advance already held. Nothing is collected twice, and
at closing the credit balances are what the customer-advances account is
presented from.

## Configuration this depends on

Per channel (**B2C → Configuration → Channels**):

- **Printful store** — which store fulfils this channel.
- **Carriage product** — what customers pay to ship is not revenue from goods;
  this product's income account is where it goes.
- **Clearing journals** — one per currency the channel collects in, each
  posting its payments to that channel's clearing account.
- **Channel operator** and **Commission product** — who bills the commission
  and what it is bought as.
- **Payment processor**, **Processor** and **Processing product** — for a shop
  of one's own, which keeps nothing itself. Whose statement says what its
  payments cost, who bills it, and what it is bought as. It is a banking cost
  rather than a selling commission, and rarely the same account.

Per company (**Settings → Accounting**, or on the company record):

- **Print-on-demand supplier**, **Supplier wallet**, **Supply of goods sold**
  and **Supply for no sale** — who draws on the wallet, the journal the wallet
  is held in, and the two products a month of supply is bought as. Without them
  the supply cannot be settled and the button says so.
- **B2C bank** and **B2C transfers** — the bank the channels pay into, whose
  suspense account is where a payout waits for its statement line, and the
  journal the movements are written in. Without them nothing can be posted for
  a payout and the button says so.
- **Registered for the One Stop Shop**, and the two accounts destination VAT
  reaches before and after registration. Until registration it accrues as a
  liability to be regularised rather than as VAT collected under a scheme the
  shop is not in.
- **Position for B2C sales outside the Union.** Nothing is owed outside the
  Union, but that is the answer to more than one question: a consumer buying
  goods and a business buying services are both untaxed, for entirely different
  reasons, at the same rate. Nothing in a customer record tells the two apart,
  so the position for consumer goods is named here rather than deduced, and a
  sale outside the Union is stated under it. Leave it empty to let the fiscal
  positions decide. Inside the Union the destination still decides.

In the chart:

- Channel prices are what the customer paid, so the taxes those sales bear must
  be **included in the price**, not added to it.
- A fiscal position built for a country must be the one that answers for it. A
  generic position with no country and a lower sequence answers first and taxes
  a German sale at nothing.
- Every product sold states **one** sales tax, and every sold product's
  category names a usable income account.
- No position a sale would be stated under maps to a **retired rate**. Removing
  one needs the archived records to be visible: write the mapping with
  `active_test` disabled, or the ORM keeps it and says nothing.

The readiness check reports each of these against the drop in hand. It also
**puts right** the ones that are a matter of fact rather than judgement, unless
*Keep the chart correct* is unticked on the drop. It is ticked by default: most
of what the check would otherwise report is a defect in the chart rather than in
the drop, and reporting a thing that can be put right without putting it right
wastes the reader's attention. Untick it to see what the chart would be reported
for without changing it.

## The four accounts, and what empties each

| Account | Filled by | Emptied by |
| --- | --- | --- |
| Etsy clearing | Each invoice, settled to the channel | The commission bill, then the payout entry |
| Stripe clearing | Each Medusa invoice | Stripe's own charge, then the payout entry |
| Printful wallet | Each top-up entry | The monthly supply document |
| Bank suspense | Payouts and top-ups waiting | The bank statement line, matched by hand |

Medusa has no account of its own. A shop of one's own holds no money: what its
customers paid sits in the Stripe clearing account, and it is Stripe that keeps
a part of it.

A month is billed the **difference** between what it is now said to have cost
and what documents already stand for it, so the exports can arrive in any order
and a wider one dropped later corrects a narrower one instead of charging twice.
An order export may complete a month nothing has spoken for; it can never take
back what a statement stated, being only part of the same account.

**Correct the chart** settles all of them at once, across the chart rather than
across the drop, because a chart defect is wrong whether or not anything is
being imported. It is worth pressing once on a shop the tool has not run on
before. Each correction states the fact it depends on and does nothing where
that fact does not hold, so it is safe to press again, and each says in the log
what it changed:

| Correction | Acts only when |
| --- | --- |
| Retire the positions that answer for everyone | The position names no country, and a country with one of its own is being answered for by it. A position a partner has been pinned to is reported instead: that is somebody's decision, not a defect. |
| State the price as including its tax | The rate has never been posted at. One the books have already used means something by being stated outside the price, and this cannot know what. |
| Give each catalog product one rate | The chart tells a goods rate from a services rate. Where two rates are stated at the same amount and nothing says which is which, the choice is a person's. |
| Name where each category's revenue goes | The category names no account yet. One that does has been decided. |
| Reverse-charge the operator's commission | The operator is in another Member State, no position requiring a tax number answers for it, and none has been set by hand. |

What it deliberately does **not** do is create master data or choose records for
you — which product is the carriage, which partner is the marketplace, which
journal a channel clears through. Those name this shop's own records, and a
wrong guess there is exactly the kind of silent error the readiness check
exists to prevent. They stay in the configuration above.

## What a sale cost

A drop states what was sold; the supplier states what it cost. Reading the
supplier records each Printful order as a fulfilment event carrying the whole
bill — items, shipping, tax and fees — and naming the sale lines it shipped.
The event allocates that across those lines pro rata by revenue and writes each
line's cost, and the last shipped unit cost becomes the print-on-demand
product's standard price. So margin is native: no step of this writes a line's
cost itself, because a second opinion would be silently overruled by the next
fulfilment.

Carriage is excluded from the allocation. What the customer paid to ship is
revenue, not a cost, and spreading the supplier's bill over it would understate
the goods.

A refund is a fulfilment too, costing the negative of what was charged, and it
usually arrives long after the sale. So a drop that holds no orders of its own
still updates what earlier sales cost: fulfilment is matched to the order Odoo
already has, not only to the orders in hand.

## What the supplier drew

Printful does not invoice an order. It draws on a wallet the shop tops up, so
the ledger owes it one document a month for what it drew, credited to the
wallet the money came out of. **Settle the supply** posts that document and
pays it from the wallet, which leaves the wallet's balance a fact the bank can
be reconciled against rather than an opinion.

It bills exactly the number the margin uses — the whole fulfilment cost, the
supplier's own carriage included — so the cost in the accounts and the cost in
the margin can never disagree. A fulfilment answering to no sale is marketing
or prototyping: it drew on the wallet just the same, so it is billed, but on a
line of its own and never as the cost of something sold.

What each document settled is kept, per fulfilment, so a cost that changes
afterwards is billed as the difference rather than charged again. A month that
nets negative — a refund arriving after its bill was paid — is a credit note,
and the wallet is credited back by exactly what it was charged.

A top-up is a bank movement and stays yours to reconcile. When the supplier has
drawn more than the wallet was ever paid, the drop says so: a prepaid balance
cannot be in credit, and a missing top-up is the usual reason.

## Destination VAT

**B2C → Destination VAT** holds one return per quarter. Computing it reads the
quarter's invoices for the taxable amount and the VAT each country bore. While
the shop is not registered, the return is what the liability would be; the
ledger carries it in the account for liabilities to be regularised.

## The Printful token

Set the system parameter `usl_b2c_ingest.printful_token`. It is read-only
access to the supplier's own orders. It is never in the repository and a
rotated token needs no release.

## One sale recorded twice

A sale exported by the older shop and again by the one that replaced it is one
sale. The finding names both records; resolving it keeps the fuller — the lines
first, then the money — cancels the other and points it at its replacement.
Nothing is deleted, and the ledger is untouched.
