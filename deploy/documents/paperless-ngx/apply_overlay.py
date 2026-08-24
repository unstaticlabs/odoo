"""Apply the USL Paperless route overlay to one exact upstream source tree."""

from hashlib import sha256
from pathlib import Path

URLS_PATH = Path("/usr/src/paperless/src/paperless/urls.py")
EXPECTED_URLS_SHA256 = (
    "d46ce3f52652fc32f6e4e2e1897cec0000fca0ad7b2bb3b41739902b564afc35"
)
IMPORT_ANCHOR = "from paperless.views import UserViewSet\n"
IMPORT_REPLACEMENT = (
    IMPORT_ANCHOR + "from paperless_ai.semantic_api import SemanticSearchView\n"
)
ROUTE_ANCHOR = """                            re_path(
                                "^selection_data/",
                                SelectionDataView.as_view(),
                                name="selection_data",
                            ),
"""
ROUTE_REPLACEMENT = (
    ROUTE_ANCHOR
    + """                            re_path(
                                "^semantic_search/",
                                SemanticSearchView.as_view(),
                                name="semantic_search",
                            ),
"""
)
ROUTE_MISMATCH_MESSAGE = "Paperless route anchor is incompatible with the overlay"


def replace_once(source: str, anchor: str, replacement: str) -> str:
    if source.count(anchor) != 1:
        raise RuntimeError(ROUTE_MISMATCH_MESSAGE)
    return source.replace(anchor, replacement, 1)


source_bytes = URLS_PATH.read_bytes()
actual_hash = sha256(source_bytes).hexdigest()
if actual_hash != EXPECTED_URLS_SHA256:
    raise RuntimeError(
        "Paperless urls.py does not match qualified v3.0.5 "
        f"({actual_hash} != {EXPECTED_URLS_SHA256})",
    )

source = source_bytes.decode("utf-8")
source = replace_once(source, IMPORT_ANCHOR, IMPORT_REPLACEMENT)
source = replace_once(source, ROUTE_ANCHOR, ROUTE_REPLACEMENT)
URLS_PATH.write_text(source, encoding="utf-8")
