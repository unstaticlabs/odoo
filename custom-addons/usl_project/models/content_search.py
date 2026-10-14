"""Build live content domains from native readable fields, without an index."""

import math
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import fields
from odoo.exceptions import AccessError
from odoo.fields import Domain


_CREDENTIAL_FIELD = re.compile(
    r"(?:^|_)(?:password|passwd|secret|token|api_key|apikey|credential)(?:_|$)", re.IGNORECASE,
)
_EXCLUDED_FIELDS = {"display_name", "access_url", "access_warning", "usl_search_text", "activity_ids", "portal_user_names"}
_TEXT_TYPES = {"char", "text", "html"}
_RELATION_TYPES = {"many2one", "many2many", "one2many"}


def company_domain(model):
    """Selected companies also constrain models with permissive native rules."""
    field = model._fields.get("company_id")
    if field and field.type == "many2one" and model.has_field_access(field, "read"):
        return Domain("company_id", "in", [False, *model.env.companies.ids])
    return Domain.TRUE


def _typed_values(model, text):
    try:
        number = float(text.replace(",", "."))
        if not math.isfinite(number):
            number = None
    except ValueError:
        number = None
    day = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            day = fields.Date.to_date(text)
        except ValueError:
            pass
    day_bounds = None
    if day and day.year < 9999:
        try:
            tz = ZoneInfo(model.env.context.get("tz") or model.env.user.tz or "UTC")
        except ZoneInfoNotFoundError:
            tz = timezone.utc
        day_bounds = tuple(
            datetime.combine(value, time.min, tz).astimezone(timezone.utc).replace(tzinfo=None)
            for value in (day, day + timedelta(days=1))
        )
    return number, day, day_bounds


def _scalar_domain(name, definition, text, typed):
    kind = definition.get("type")
    number, day, bounds = typed
    if kind in _TEXT_TYPES:
        return Domain(name, "ilike", text)
    if kind in {"selection", "tags"}:
        keys = [
            key for key, label, *_extra in (definition.get("selection", []) if kind == "selection" else definition.get("tags", []))
            if text.casefold() in str(label).casefold() or text.casefold() in str(key).casefold()
        ]
        return Domain(name, "in", keys) if keys else Domain.FALSE
    if kind in {"integer", "float", "monetary"} and number is not None:
        if kind != "integer" or number.is_integer():
            return Domain(name, "=", int(number) if kind == "integer" else number)
    if kind == "date" and day:
        return Domain(name, "=", day.isoformat())
    if kind == "datetime" and bounds:
        return Domain(name, ">=", fields.Datetime.to_string(bounds[0])) & Domain(name, "<", fields.Datetime.to_string(bounds[1]))
    if kind == "boolean":
        booleans = {"true": True, "yes": True, "oui": True, "false": False, "no": False, "non": False}
        if text.casefold() in booleans:
            return Domain(name, "=", booleans[text.casefold()])
    return Domain.FALSE


def _field_domain(model, path, text, typed, seen=frozenset()):
    """Resolve live related scalar fields through each owner's own read query."""
    name, *rest = path
    marker = (model._name, tuple(path))
    if marker in seen or not model.has_access("read"):
        return Domain.FALSE
    field = model._fields.get(name)
    if not field or not model.has_field_access(field, "read"):
        return Domain.FALSE
    seen = seen | {marker}
    if field.related:
        return _field_domain(model, [*field.related, *rest], text, typed, seen)
    if rest:
        if field.type not in _RELATION_TYPES or not field.store:
            return Domain.FALSE
        target = model.env[field.comodel_name]
        predicate = _field_domain(target, rest, text, typed, seen)
        if predicate.is_false():
            return predicate
        return Domain(name, "any", target.with_context(active_test=False)._search(
            company_domain(target) & predicate,
        ))
    definition = model.fields_get([name], attributes=["type", "selection", "searchable"]).get(name)
    if not definition or not definition.get("searchable"):
        return Domain.FALSE
    return _scalar_domain(name, definition, text, typed)


def _label_query(model, text, cache):
    """The related model's own search supplies its ACLs and record rules."""
    if model._name not in cache:
        if not model.has_access("read"):
            cache[model._name] = None
        else:
            name = model._rec_name
            field = model._fields.get(name)
            if not field or field.type not in _TEXT_TYPES or not field._description_searchable or not model.has_field_access(field, "read"):
                cache[model._name] = None
            else:
                if model._name == "res.users":
                    # Native user name is an authorized public identity. The
                    # default company and underlying contact are not ownership
                    # boundaries for discovering a task's readable assignee.
                    predicate = Domain(name, "ilike", text)
                    scope = Domain.TRUE
                else:
                    predicate = _field_domain(model, [name], text, _typed_values(model, text))
                    scope = company_domain(model)
                cache[model._name] = model.with_context(active_test=False)._search(scope & predicate)
    return cache[model._name]


def _property_domains(model, name, text, typed, cache):
    field = model._fields[name]
    owner_field = model._fields.get(field.definition_record)
    if not owner_field or not model.has_field_access(owner_field, "read"):
        return []
    # Native Properties grants a readable parent access to its schema even when
    # the definition owner is private. Derive representatives through the
    # caller's own parent query; never discover owners by elevated enumeration.
    grouped = model._read_group(
        company_domain(model) & Domain(owner_field.name, "!=", False),
        [owner_field.name], ["id:min"], order="id:min",
    )
    representatives = model.browse([record_id for _owner, record_id in grouped])._filtered_access("read")
    clauses = []
    seen = set()
    representatives.fetch([owner_field.name])
    for record in representatives.with_context(prefetch_fields=False):
        owner_id = record[owner_field.name].id
        owner_scope = Domain(owner_field.name, "=", owner_id)
        # This native accessor elevates only the definition metadata read.
        for definition in field._get_properties_definition(record) or []:
            key = definition.get("name")
            kind = definition.get("type")
            if not key or kind in {"signature", "separator"} or _CREDENTIAL_FIELD.search(
                re.sub(r"\W+", "_", str(definition.get("string", "")).casefold()),
            ):
                continue
            marker = (owner_id, key, kind, str(definition.get("selection")), definition.get("comodel"))
            if marker in seen:
                continue
            seen.add(marker)
            path = f"{name}.{key}"
            if kind in {"many2one", "many2many"}:
                comodel = definition.get("comodel")
                if comodel in model.env.registry:
                    query = _label_query(model.env[comodel], text, cache)
                    if query is not None:
                        clauses.append(owner_scope & Domain(path, "in", query.get_result_ids()))
            else:
                clauses.append(owner_scope & _scalar_domain(path, definition, text, typed))
    return clauses


def readable_content_domain(model, value):
    """Search business values visible to this caller, never credential/binary data.

    Related labels use independently authorized native queries rather than raw
    dotted-field comparisons. No cross-user cache or persistent index is kept.
    """
    text = str(value or "").strip()
    if not text or not model.has_access("read"):
        return Domain.FALSE
    typed = _typed_values(model, text)
    clauses = []
    related = {}
    metadata = model.fields_get(attributes=["type", "selection", "searchable", "store", "relation"])
    for name, definition in metadata.items():
        field = model._fields[name]
        if name in _EXCLUDED_FIELDS or _CREDENTIAL_FIELD.search(name):
            continue
        if not definition.get("searchable") or not model.has_field_access(field, "read"):
            continue
        kind = definition["type"]
        if kind == "properties":
            clauses.extend(_property_domains(model, name, text, typed, related))
        elif kind in _RELATION_TYPES and field.store:
            try:
                query = _label_query(model.env[field.comodel_name], text, related)
            except AccessError:
                query = None
            if query is not None:
                clauses.append(Domain(name, "any", query))
        else:
            clauses.append(_field_domain(model, [name], text, typed) if field.related else _scalar_domain(name, definition, text, typed))
    return company_domain(model) & Domain.OR(clauses)
