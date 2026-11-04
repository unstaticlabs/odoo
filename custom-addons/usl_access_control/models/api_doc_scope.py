from contextlib import contextmanager
from contextvars import ContextVar

_API_DOC_INDEX_LOOKUPS = ContextVar("usl_api_doc_index_lookups", default=None)


@contextmanager
def api_doc_index_scope():
    """Share identical Agent policy lookups across one API index build.

    The index asks about every public method of every model, and each answer
    needs the same few rows. Only the lookups are shared: every access decision
    is still evaluated, and nothing outlives the build, so a policy change is
    visible to the next request.
    """
    token = _API_DOC_INDEX_LOOKUPS.set({})
    try:
        yield
    finally:
        _API_DOC_INDEX_LOOKUPS.reset(token)


def api_doc_index_lookup(key, lookup):
    lookups = _API_DOC_INDEX_LOOKUPS.get()
    if lookups is None:
        return lookup()
    if key not in lookups:
        lookups[key] = lookup()
    return lookups[key]
