# Import Medusa items with native identities

This is a manual exchange path for one authorized Medusa v2 order response or
orders page. It retains native order and item IDs through the existing B2C
import batch. It does not poll the shop, schedule imports, or synchronize an
existing order's financial history.

## Produce the file

For a single order, make an authorized request to
`GET /admin/orders/:id?fields=%2Bitems.total`. In a client that encodes query
parameters for you, enter `+items.total` as the value of `fields`. Keep the
leading plus. On the shop, a list of explicit fields can omit the computed
`items.total` even when it appears in the request.

Save only these fields from the response. The default response can include
customer and address data.

`id,display_id,created_at,status,payment_status,fulfillment_status,currency_code,items.id,items.title,items.product_title,items.variant_sku,items.variant_title,items.quantity,items.unit_price,items.total`

A bounded orders page is also accepted if it contains those fields and every
item has an explicit total. If a total is missing, use the single-order request
before converting the response.

The source contracts are the [Medusa Admin API](https://docs.medusajs.com/api/admin#orders_getordersid)
and [native order line item DTO](https://docs.medusajs.com/resources/references/order/interfaces/order.OrderLineItemDTO).
The GET client and its authorization remain the operator's existing Medusa
access. This implementation performs no network request or credential storage.
The converter accepts `{"order": ...}` or `{"orders": [...]}` and does not treat
one page as the whole shop. Retain the response privately.

Run from the distribution checkout:

```sh
python3 custom-addons/usl_b2c_ingest/parsers/medusa_exchange.py \
  /private/tmp/medusa-response.json --output /private/tmp/medusa-native-items.csv
```

Use a new output filename; the command refuses to overwrite an existing file.
It emits only a fixed column list and discards customer, address and metadata
fields even if they are present in the input. The selected order IDs, item
IDs, products and amounts remain private source evidence; do not commit the
response or resulting CSV to the public repository.

The exact semicolon-separated UTF-8 CSV header is:

```text
order_id;order_number;line_id;date;order_status;payment_status;fulfillment_status;currency;sku;product;variant;qty;unit_price;total
```

`order_id` and `line_id` are the native IDs. `order_number` is the display ID.
The amounts remain in Medusa v2's main currency unit; there is no conversion to
cents. Each item's explicit `total`, including zero, is required and copied.
No order total, shipping, discount or tax amount is inferred from these lines.
The order status, payment status and fulfilment status remain separate facts.

## Review in the existing import flow

Use the existing B2C Administrator import rights to create a batch in the
intended company and upload the CSV. Read-only reviewers can inspect normalized
rows; they cannot parse the batch or read restricted raw payloads.
Read the files and review the native product mapping and identity findings.
The batch, file and source rows retain their existing company record rules.
Parsing changes only import state; it does not create sales or accounting moves.
Existing native apply and settlement actions retain their permissions and
business rules. An identified-items file alone does not provide the destination
or complete order economics required by those workflows.

Two identical legitimate items remain two rows because their native line IDs
differ. Repeating a native snapshot does not add lines. New canonical orders
retain native order IDs and display IDs separately; canonical line IDs retain
the native item IDs. A later historical file resolves the same order through
its display ID. A different nonempty native ID on an existing order is a
conflict, even when the display ID agrees. Conversely, an identified file can resolve a historical order
without rewriting its reconstructed lines. That fallback requires a display-only
historical identity (no external order ID, or an explicitly identical external
and display pair); a missing display ID does not establish that an external ID
is historical. An identity is checked again before
materialization, so an older preview can reuse an order imported in the meantime.
All matching order and item rows then link to the retained order together.
New Medusa orders also retain a protected native identity unique within their
company. This database constraint prevents concurrent imports under different
display numbers from creating two orders. A competing transaction is refused;
read its batch again to reuse the committed sale. Existing orders are not
backfilled or rewritten, and their canonical keys remain unchanged.

Distinct orders can use different item layouts in one batch. For a single
order, use one complete item snapshot: historical and native item lines are
not matched by product content. Historical files have no immutable line IDs,
so such a match would guess an identity. Complete order-header files may still
accompany the selected item layout. The two existing historical layouts keep
their original compatibility with each other.

If one order has both line layouts, the error names that order. Remove its
redundant item file or split that order into a separate batch; keep unrelated
orders in the original batch. Read the other layout separately to see that the
sale is already known before applying anything. Native parsing is atomic for
the whole batch: a refused parse retains its previous rows, including those of
unrelated orders. Native apply also retains its existing batch-wide blocking
findings; this slice introduces no partial-apply mechanism.

Repeated native files with identical item IDs are deduplicated. Different
native item sets for the same order are refused as conflicting snapshots;
price, payment or delivery disagreements for the same IDs remain blocking
findings. Native order-status contradictions are also blocking between the
identified-items and complete-orders formats when both retain the same native
order ID, regardless of file order or format priority. The finding names the
conflicting files and asks the operator to re-export them from the same current
snapshot, replace their older versions and read the batch again. Historical
layouts without a native order ID keep their existing vocabulary compatibility.
Choose one complete source snapshot rather than combining versions.
Equivalent numeric spellings such as `10` and `10.0` agree for comparison;
original files and the retained payload keep their source spelling.
Existing historical orders and their reconstructed line IDs are never rewritten.

Reading a newer payment, delivery or refund state records what that snapshot
says. It does not automatically reconcile, cancel, post, refund or overwrite a
known order. Use the existing separately authorized native lifecycle workflow
when its complete source evidence is available. Automatic collection and
state synchronization remain later work on feedback #2588.

## Qualification scope

The regression corpus uses invented orders, products and companies. It exercises
the native response-to-CSV-to-batch path, duplicate-content items, replay,
historical/native overlap, identity collisions, stale previews, separate source
states, French recovery and company isolation. The financial nonmutation
assertion fingerprints ten full business/ledger tables around parse-only state
journeys; creating the disposable fixtures themselves is outside that assertion.
A synthetic end-to-end apply with a complete header and mapped products creates
one native sale with two distinct items; replay and a historical backfill retain
that sale without creating accounting moves. This is not qualification of every
Medusa tax-inclusive or tax-exclusive price configuration. The source unit price
and item total stay unmodified, and native total/tax checks remain mandatory.
No live customer coordinates or real accounting actions are used.
