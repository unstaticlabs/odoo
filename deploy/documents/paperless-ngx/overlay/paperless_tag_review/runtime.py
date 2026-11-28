from contextlib import contextmanager
from contextvars import ContextVar


automatic_tag_change = ContextVar("automatic_tag_change", default=False)


@contextmanager
def classifier_run():
    token = automatic_tag_change.set(True)
    try:
        yield
    finally:
        automatic_tag_change.reset(token)
