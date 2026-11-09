"""Shared annotations of native, query-scoped Remaining balances.

No journal or reconciliation record is written by this model. Native aggregation
reads complete groups once per distinct grouping path, never child pages.
"""
import hashlib
import json
from datetime import date, datetime

from psycopg2.errors import UniqueViolation

from odoo import _, api, fields, models
from odoo.exceptions import ConcurrencyError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import date_utils

_WRITE_REVIEW = object()
_GROUP_FIELDS = {"account_id", "partner_id", "date:month"}


def _canonical(value):
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            result = [normalize(value) for value in item]
            # Membership operands are sets. Their order must not turn the
            # same selected companies or filter IDs into a different review.
            if (len(result) == 3 and isinstance(result[0], str)
                    and result[1] in ("in", "not in") and isinstance(result[2], list)
                    and all(value is None or isinstance(value, (str, int, float, bool)) for value in result[2])):
                result[2].sort(key=lambda value: json.dumps(value, sort_keys=True))
            return result
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class ReconciliationReview(models.Model):
    _name = "account.reconciliation.review"
    _description = "Shared reconciliation Remaining review"
    _rec_name = "reviewed_at"

    scope_key = fields.Char(required=True, index=True, readonly=True)
    account_id = fields.Many2one("account.account", required=True, readonly=True, ondelete="cascade")
    partner_id = fields.Many2one("res.partner", readonly=True, ondelete="cascade")
    access_key = fields.Char(required=True, index=True, readonly=True)
    company_ids = fields.Many2many("res.company", required=True, readonly=True)
    domain = fields.Json(required=True, readonly=True, groups="base.group_system")
    path = fields.Json(required=True, readonly=True, groups="base.group_system")
    baseline = fields.Json(required=True, readonly=True)
    baseline_context = fields.Json(readonly=True)
    acknowledged = fields.Json(readonly=True)
    reviewer_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    reviewed_at = fields.Datetime(required=True, readonly=True)
    acknowledged_by_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    acknowledged_at = fields.Datetime(readonly=True)
    revision = fields.Integer(required=True, default=1, readonly=True)
    _scope_unique = models.Constraint("UNIQUE(scope_key)", "This reconciliation scope already has a shared review.")

    @api.model
    def _access_key(self):
        lines = self.env["account.move.line"]
        lines.check_access("read")
        # No uid: colleagues with the same native visible query share a mark.
        # User-dependent native rules resolve to a different domain only when
        # they actually change the caller's journal-item visibility.
        # Native optimization removes redundant owner-ceiling clauses for
        # delegated readers without discarding real record-rule differences.
        return _digest(list(lines._access_domain("read").optimize_full(lines)))

    @api.model
    def _access_domain(self, operation):
        domain = super()._access_domain(operation)
        if domain.is_false() or self.env.su:
            return domain
        # Metadata discovery asks has_access() for every model. Unavailable
        # related models restrict visibility; they must not raise while probing.
        accounts = self.env["account.account"].with_context(active_test=False)
        if not self.env["account.move.line"].has_access("read") or not accounts.has_access("read"):
            return Domain.FALSE
        domain &= Domain("access_key", "=", self._access_key())
        domain &= Domain("account_id", "in", accounts._search([]))
        partners = self.env["res.partner"].with_context(active_test=False)
        partner_domain = Domain("partner_id", "=", False)
        if partners.has_access("read"):
            partner_domain |= Domain("partner_id", "in", partners._search([]))
        domain &= partner_domain
        domain &= Domain("company_ids", "not any", [("id", "not in", self.env.companies.ids)])
        return domain

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        if self.env.context.get("_write_reconciliation_review") is not _WRITE_REVIEW:
            raise UserError(_("Use Mark reviewed to record the current Remaining balance and reviewer."))
        return super().create(vals_list)

    def write(self, vals):
        self.check_access("write")
        if self.env.context.get("_write_reconciliation_review") is not _WRITE_REVIEW:
            raise UserError(_("Use Mark reviewed or Acknowledge change to update a shared review."))
        return super().write(vals)

    @api.model
    def _prepare(self, domain, paths):
        if not isinstance(domain, list) or len(domain) > 512 or len(_canonical(domain)) > 64000:
            raise ValidationError(_("The review filters are too large. Narrow the search and try again."))
        if not isinstance(paths, list) or not 1 <= len(paths) <= 200:
            raise ValidationError(_("Request between 1 and 200 reconciliation groups."))
        normalized = list(Domain.AND([domain, [("parent_state", "=", "posted"), ("account_id.reconcile", "=", True)]]))
        companies = sorted(self.env.companies.ids)
        access_key = self._access_key()
        descriptors = []
        relation_ids = {"account_id": set(), "partner_id": set()}
        for path in paths:
            if not isinstance(path, list) or not 1 <= len(path) <= 3:
                raise ValidationError(_("Use an Account, Partner or Month group containing an account."))
            seen = set()
            clean = []
            for item in path:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    raise ValidationError(_("The reconciliation group is invalid. Reload the list."))
                field, value = item
                if field not in _GROUP_FIELDS or field in seen:
                    raise ValidationError(_("Use an Account, Partner or Month group containing an account."))
                seen.add(field)
                if field == "date:month":
                    try:
                        parsed = date.fromisoformat(value)
                    except (ValueError, TypeError):
                        raise ValidationError(_("Use a resolved calendar month for the review.")) from None
                    if parsed.day != 1:
                        raise ValidationError(_("Use a resolved calendar month for the review."))
                elif (type(value) is not int or value <= 0) and value is not False:
                    raise ValidationError(_("The reconciliation group is invalid. Reload the list."))
                clean.append([field, value])
            if "account_id" not in seen or not dict(clean)["account_id"]:
                raise ValidationError(_("A review must identify an account group."))
            for field, value in clean:
                if field in relation_ids and value:
                    relation_ids[field].add(value)
            scope = {"domain": normalized, "path": clean, "companies": companies,
                     "access": access_key, "active_test": self.env.context.get("active_test", True),
                     "currency_context": {"company_id": self.env.company.id, "currency_id": self.env.company.currency_id.id}}
            descriptors.append({**scope, "key": _digest(scope)})
        # Validate relations together: expanding many groups must not create a
        # record-rule query for every account or partner.
        for field, relation in (("account_id", "account.account"), ("partner_id", "res.partner")):
            self.env[relation].browse(sorted(relation_ids[field])).check_access("read")
        return descriptors

    @api.model
    def _totals(self, domain, descriptors):
        lines = self.env["account.move.line"]
        paths = list(dict.fromkeys(tuple(item[0] for item in scope["path"]) for scope in descriptors))
        aggregate_domain = Domain.AND([domain, [("parent_state", "=", "posted"), ("account_id.reconcile", "=", True)]])
        # One native query per distinct path (at most three in the list), not
        # per requested group. Keep the native currency/rate implementation.
        groups = [lines._read_group(
            aggregate_domain, list(path),
            ["amount_residual:sum", "amount_residual:sum_currency", "company_currency_id:recordset"],
        ) for path in paths]
        by_group = {}
        for fields_group, rows in zip(paths, groups):
            for row in rows:
                values = []
                for value in row[:-3]:
                    if isinstance(value, models.BaseModel):
                        value = value.id or False
                    elif isinstance(value, (date, datetime)):
                        value = value.isoformat()[:10]
                    values.append(value)
                key = _canonical(list(zip(fields_group, values)))
                currencies = row[-1]
                mixed = len(currencies) > 1
                currency = self.env.company.currency_id if mixed or not currencies else currencies
                # Match native ListRenderer/getAggregatesFromGroupData: normal
                # sum for one currency, native sum_currency for mixed groups.
                by_group[key] = ({str(currency.id): row[-2] if mixed else row[-3]}, currencies.ids, mixed)
        totals = []
        for scope in descriptors:
            amount, currencies, mixed = by_group.get(_canonical(scope["path"]),
                                                     ({str(self.env.company.currency_id.id): 0}, [], False))
            scope["source_currencies"] = currencies
            scope["presentation"] = {"currency_id": int(next(iter(amount))), "company_id": self.env.company.id}
            if mixed:
                scope["presentation"].update({"rate_company_id": self.env.company.id,
                                              "valuation_date": fields.Date.to_string(fields.Date.context_today(lines))})
            identity = {key: value for key, value in scope.items()
                        if key not in ("key", "source_currencies", "presentation")}
            scope["key"] = _digest(identity)
            totals.append(amount)
        return totals

    @api.model
    def _balance_token(self, current):
        # Match the displayed monetary precision, including negative zero.
        rounded = {}
        for key, amount in current.items():
            value = self.env["res.currency"].browse(int(key)).round(amount)
            rounded[key] = value or 0
        return _digest(rounded)

    def _status(self, scope, current):
        if self:
            self.ensure_one()
        baseline = self.baseline if self else {}
        acknowledged = self.acknowledged if self else None
        currency_ids = sorted({int(key) for vector in (baseline, current, acknowledged or {}) for key in vector})
        currencies = self.env["res.currency"].browse(currency_ids)
        amounts = []
        currency_changed = bool(self and set(baseline) != set(current))
        same = not currency_changed
        same_ack = isinstance(acknowledged, dict) and set(acknowledged) == set(current)
        for currency in currencies:
            key = str(currency.id)
            old, now = baseline.get(key, 0), current.get(key, 0)
            same &= currency.compare_amounts(old, now) == 0
            same_ack &= currency.compare_amounts((acknowledged or {}).get(key, 0), now) == 0
            amounts.append({"currency_id": currency.id, "currency": currency.name,
                            "baseline": currency.round(old) if key in baseline else None,
                            "current": currency.round(now) if key in current else None,
                            "delta": None if not self or currency_changed else currency.round(now - old)})
        state = "unreviewed" if not self else "reviewed" if same else "acknowledged" if same_ack else "changed"
        return {"scope_key": scope["key"], "state": state, "revision": self.revision if self else 0,
                "observed_balance": self._balance_token(current),
                "amounts": amounts, "currency_changed": currency_changed, "source_currency_ids": scope.get("source_currencies", []),
                "presentation": scope.get("presentation", {}),
                "reviewed_context": self.baseline_context if self else False, "reviewer": self.reviewer_id.display_name if self else False,
                "reviewed_at": fields.Datetime.to_string(self.reviewed_at) if self else False,
                "acknowledged_by": self.acknowledged_by_id.display_name if self and self.acknowledged_by_id else False,
                "acknowledged_at": fields.Datetime.to_string(self.acknowledged_at) if self and self.acknowledged_at else False,
                "domain": scope["domain"], "path": scope["path"], "company_ids": scope["companies"],
                "group_domain": self._group_domain(scope["path"]),
                "companies": self.env["res.company"].browse(scope["companies"]).mapped("display_name"),
                "can_review": self.env[self._name].has_access("write") if self else self.env[self._name].has_access("create")}

    @api.model
    def _group_domain(self, path):
        conditions = []
        for field, value in path:
            if field == "date:month":
                start = date.fromisoformat(value)
                conditions.extend([("date", ">=", value), ("date", "<", date_utils.add(start, months=1).isoformat())])
            else:
                conditions.append((field, "=", value))
        return list(Domain(conditions))

    @api.model
    @api.readonly
    def get_review_status(self, domain, paths):
        self.check_access("read")
        scopes = self._prepare(domain, paths)
        current = self._totals(domain, scopes)
        snapshots = {item.scope_key: item for item in self.search([("scope_key", "in", [s["key"] for s in scopes])])}
        return [snapshots.get(scope["key"], self.browse())._status(scope, amount)
                for scope, amount in zip(scopes, current)]

    @api.model
    def _change_review(self, domain, path, expected_revision, observed_balance, dismiss):
        scope = self._prepare(domain, [path])[0]
        current = self._totals(domain, [scope])[0]
        if observed_balance != self._balance_token(current):
            raise UserError(_("The Remaining balance changed. Reload the group before continuing."))
        snapshot = self.search([("scope_key", "=", scope["key"])], limit=1)
        if snapshot:
            snapshot.check_access("write")
            self.env.cr.execute("SELECT id FROM account_reconciliation_review WHERE id = %s FOR UPDATE", [snapshot.id])
            snapshot.invalidate_recordset()
        else:
            self.check_access("write" if dismiss else "create")
        if type(expected_revision) is not int or expected_revision != (snapshot.revision if snapshot else 0):
            raise UserError(_("A colleague changed this review. Reload the group before continuing."))
        if dismiss and not snapshot:
            raise UserError(_("Review the Remaining balance before acknowledging a change."))
        model = snapshot.with_context(_write_reconciliation_review=_WRITE_REVIEW)
        if dismiss:
            model.write({"acknowledged": current, "acknowledged_by_id": self.env.uid,
                         "acknowledged_at": fields.Datetime.now(), "revision": snapshot.revision + 1})
        else:
            values = {"baseline": current, "baseline_context": scope["presentation"], "acknowledged": False, "acknowledged_by_id": False,
                      "acknowledged_at": False, "reviewer_id": self.env.uid, "reviewed_at": fields.Datetime.now(),
                      "revision": snapshot.revision + 1 if snapshot else 1}
            if snapshot:
                model.write(values)
            else:
                values.update({"scope_key": scope["key"], "access_key": scope["access"],
                               "account_id": dict(scope["path"])["account_id"],
                               "partner_id": dict(scope["path"]).get("partner_id") or False,
                               "company_ids": [fields.Command.set(scope["companies"])],
                               "domain": scope["domain"], "path": scope["path"]})
                try:
                    with self.env.cr.savepoint():
                        # Normal metadata ACL + native company/account/partner read
                        # checks above precede this technical-field-only privilege.
                        # sudo retains the requester uid; stored filters are never
                        # returned by the ordinary status API.
                        snapshot = self.with_context(_write_reconciliation_review=_WRITE_REVIEW).sudo().create(values)
                except UniqueViolation:
                    # Another first review committed after our RR snapshot.
                    # Native request transaction retry rereads its revision.
                    raise ConcurrencyError("Concurrent first reconciliation review") from None
        return snapshot.with_user(self.env.user)._status(scope, current)

    @api.model
    def mark_reviewed(self, domain, path, expected_revision, observed_balance):
        return self._change_review(domain, path, expected_revision, observed_balance, False)

    @api.model
    def dismiss_variance(self, domain, path, expected_revision, observed_balance):
        return self._change_review(domain, path, expected_revision, observed_balance, True)
