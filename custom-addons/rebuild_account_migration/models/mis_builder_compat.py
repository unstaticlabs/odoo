from odoo.addons.mis_builder.models.kpimatrix import KpiMatrix


def _load_account_names_including_archived(self):
    account_ids = set()
    for detail_rows in self._detail_rows.values():
        account_ids.update(detail_rows.keys())
    accounts = self._account_model.with_context(active_test=False).search([
        ("id", "in", list(account_ids)),
    ])
    self._account_names = {a.id: self._get_account_name(a) for a in accounts}


KpiMatrix._load_account_names = _load_account_names_including_archived
