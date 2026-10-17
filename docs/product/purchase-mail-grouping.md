# Purchase mail attachments

## Native UBL/CII compatibility

At the distribution's Odoo `saas~19.3` base
`b426963bb82ed8848767dc8845cff9c7a0fa0515`, native
`account.edi.common._retrieve_invoice_line_vals` checks whether optional
Enterprise deferred-period fields exist before parsing them, but returns both
keys with `None` even when those fields do not exist. A nonzero UBL invoice
line then fails native `account.move.line.create` with an invalid-field error.
The native attachment decoder catches the error, leaving a draft without the
imported lines.

USL Accounting extends that native method and removes only an absent field's
`None` value. Installed fields keep all their values. Unsupported nonempty
values still reach native validation and fail; the extension neither adds
fields nor discards financial information. The explicit dependency on native
`account_edi_ubl_cii` establishes the inherited model's loading order.

This is a distribution compatibility bridge, not a replacement importer. On
an upstream update, rerun the native UBL import and optional-field tests, and
remove the override when upstream stops emitting absent empty keys. The
field-present test models the optional registry shape at the delegate
boundary; it does not qualify an Enterprise installation.
