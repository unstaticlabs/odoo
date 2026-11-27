"""A shared native title order for Home and concrete-model content searches."""

from odoo import fields, models
from odoo.tools import SQL

from odoo.addons.usl_project.models.search_words import lexical_words


RELEVANCE_HELP = (
    "Use an ilike content domain. For Home relevance, order by usl_search_text and "
    "set search_relevance_query to the same text. Other native orders are unchanged."
)


def relevance_order(model, table, query, *, reverse=False):
    if not isinstance(query, str) or not query.strip() or len(query) > 2048:
        raise ValueError(model.env._("Enter a non-empty search of up to 2,048 characters to order by relevance."))
    model.check_field_access(model._fields["usl_search_text"], "read")
    descending = SQL("ASC") if reverse else SQL("DESC")
    if not model.has_field_access(model._fields["name"], "read"):
        return SQL("%s %s", table.id, descending)
    title = model.env.registry.unaccent(SQL("lower(coalesce(%s, ''))", table.name))
    needle = model.env.registry.unaccent(SQL("lower(%s::text)", query.strip()))
    words = lexical_words(needle)
    return SQL("""
        CASE WHEN %(title)s = %(needle)s THEN 0
             WHEN strpos(%(title)s, %(needle)s) > 0 THEN 1
             WHEN cardinality(%(words)s) > 0 AND %(title_words)s @> %(words)s THEN 2
             ELSE 3 END %(direction)s, %(id)s %(descending)s
    """, title=title, needle=needle, words=words, title_words=lexical_words(title),
        direction=SQL("DESC") if reverse else SQL("ASC"), id=table.id, descending=descending)


class ProjectTask(models.Model):
    _inherit = "project.task"
    usl_search_text = fields.Char(help=RELEVANCE_HELP)

    def _order_field_to_sql(self, table, field_expr, direction, nulls):
        if field_expr == "usl_search_text":
            return relevance_order(self, table, self.env.context.get("search_relevance_query"), reverse=direction == SQL("DESC"))
        return super()._order_field_to_sql(table, field_expr, direction, nulls)


class ProjectProject(models.Model):
    _inherit = "project.project"
    usl_search_text = fields.Char(help=RELEVANCE_HELP)

    def _order_field_to_sql(self, table, field_expr, direction, nulls):
        if field_expr == "usl_search_text":
            return relevance_order(self, table, self.env.context.get("search_relevance_query"), reverse=direction == SQL("DESC"))
        return super()._order_field_to_sql(table, field_expr, direction, nulls)


class ResPartner(models.Model):
    _inherit = "res.partner"
    usl_search_text = fields.Char(help=RELEVANCE_HELP)

    def _order_field_to_sql(self, table, field_expr, direction, nulls):
        if field_expr == "usl_search_text":
            return relevance_order(self, table, self.env.context.get("search_relevance_query"), reverse=direction == SQL("DESC"))
        return super()._order_field_to_sql(table, field_expr, direction, nulls)
