"""Adapt archived Office and email PDFs to the selected Gotenberg API."""

import json
import os
import re
from contextlib import ExitStack
from pathlib import Path

import httpx
from gotenberg_client import GotenbergClient
from gotenberg_client.options import PdfAFormat


def _uses_gotenberg_9() -> bool:
    major = os.environ.get("USL_PAPERLESS_GOTENBERG_API_MAJOR")
    if not major:
        image = os.environ.get("USL_PAPERLESS_GOTENBERG_IMAGE", "")
        name = image.split("@", 1)[0].rsplit("/", 1)[-1]
        tag = name.partition(":")[2]
        return bool(re.match(r"^9(?:[.-]|$)", tag))
    if major not in {"8", "9"}:
        raise ValueError("USL_PAPERLESS_GOTENBERG_API_MAJOR must be 8 or 9")
    return major == "9"


def _remediate(profile: PdfAFormat) -> dict[str, str]:
    return {"op": "remediate", "profile": profile.to_form()["pdfa"]}


class PdfA9Format:
    def __init__(self, profile: PdfAFormat, *, office: bool):
        self.profile = profile
        self.office = office

    def to_form(self) -> dict[str, str]:
        if self.office:
            return {"nativePdfa": self.profile.to_form()["pdfa"]}
        return {"stages": json.dumps([_remediate(self.profile)])}


def pdfa_format(profile: PdfAFormat, *, office: bool = False) -> PdfAFormat | PdfA9Format:
    return PdfA9Format(profile, office=office) if _uses_gotenberg_9() else profile


def merge_email_pdfs(
    host: str,
    timeout: int,
    paths: list[Path],
    profile: PdfAFormat | None,
) -> bytes:
    if not _uses_gotenberg_9():
        with GotenbergClient(host=host, timeout=timeout) as client, client.merge.merge() as route:
            if profile is not None:
                route.pdf_format(profile)
            return route.merge(paths).run().content

    stages = [{"op": "merge"}]
    if profile is not None:
        stages.append(_remediate(profile))
    with ExitStack() as files:
        # The renderer sorts multipart parts by filename. Preserve Paperless's layout order.
        uploads = [
            ("files", (f"{index:05d}_{path.name}", files.enter_context(path.open("rb")), "application/pdf"))
            for index, path in enumerate(paths)
        ]
        response = httpx.post(
            host.rstrip("/") + "/forms/pdf/pipe",
            data={"stages": json.dumps(stages)},
            files=uploads,
            timeout=timeout,
        )
    response.raise_for_status()
    return response.content
