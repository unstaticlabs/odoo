"""PostgreSQL search terms for the private lexical projections."""

from odoo.tools import SQL
from odoo.tools.misc import remove_accents


def folded_text(registry, text):
    if registry.has_unaccent:
        return registry.unaccent(SQL("%s::text", text))
    return SQL("%s::text", remove_accents(text))


def lexical_words(value):
    # The normal parser is fast for short text. For long documents, collect
    # words from the same dictionary without constructing a positional vector
    # for the whole document (PostgreSQL caps that vector at 1 MiB).
    return SQL("""
        CASE WHEN octet_length(%(value)s) <= 65536
             THEN tsvector_to_array(to_tsvector('pg_catalog.simple', %(value)s))
             ELSE ARRAY(
                 SELECT DISTINCT word
                   FROM ts_debug('pg_catalog.simple', %(value)s) AS token
                   CROSS JOIN LATERAL unnest(token.lexemes) AS words(word)
                  WHERE octet_length(word) < 2047
             )
         END
    """, value=value)


def prepare_fragment(env, content):
    env.cr.execute(SQL("""
        SELECT source.content, %s FROM (SELECT %s AS content) AS source
    """, lexical_words(SQL("source.content")), folded_text(env.registry, content)))
    return env.cr.fetchone()
