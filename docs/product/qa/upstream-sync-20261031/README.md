# Odoo 19.3 catch-up browser evidence

Recaptured 31 October 2026 from corrected source revision
`4a60a1664a00808d63d580e9c45d44cc50a4da1f`, after the attachment-fixture repair.
The task-only test image was rebuilt from that source and the current custom
add-ons were mounted. The disposable clean-install database contains an invented
company, supplier and posted EUR 100 bill. All names, references and amounts are
synthetic. Desktop viewport: 1440 × 900; mobile: 390 × 844. Browser chrome is
excluded. These replace the earlier pre-repair captures in this directory.

Each capture was personally inspected. JPEG metadata has only the standard JFIF
header, with no EXIF, XMP, comments, paths or credentials. Subsequent changes add
these images and documentation only; visible product code is unchanged.

## Observed journey

1. Sign in as the synthetic accounting collaborator and open the posted bill.
2. Open its Journal Items smart button: two accounting lines are displayed.
3. Search its label for “Synthetic service”: one matching line remains.
4. Reload the filtered URL: one matching line and the shared filter remain.
5. Return through the bill breadcrumb: the posted bill remains accessible.
6. Resize to mobile: posted status, vendor, reference and dates remain legible;
   secondary actions collapse into the native menu. Reset the viewport afterwards.

The screenshots show layout and fixture state, not proof of functional behavior.
The journey above was exercised separately. Native and full-distribution navigation suites passed 153 desktop and 20 mobile
tests. The complete affected custom frontend selection also passed 344 desktop
and 327 mobile tests. This is a focused smoke
journey, not a claim that every application or browser-history path was exercised.

- [Posted bill, desktop](posted-bill-desktop.jpg): native form and journal shortcut.
- [Journal overview, desktop](journal-desktop.jpg): both synthetic accounting lines.
- [Filtered journal, desktop](filtered-journal-desktop.jpg): shared filter after reload.
- [Posted bill, mobile](posted-bill-mobile.jpg): compact form and native action menus.

Desktop review found the existing dense accounting table horizontally scrollable
within its form; mobile retains a vertical form and tabs. No product redesign or
new navigation behavior is introduced by this catch-up.
