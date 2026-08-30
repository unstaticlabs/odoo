"""Bounded HTTP byte-range responses for Paperless document binaries."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import BinaryIO

from django.http import FileResponse
from django.http import HttpResponse
from django.http import StreamingHttpResponse

_SINGLE_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK_SIZE = 64 * 1024


class _LimitedFileIterator:
    def __init__(self, handle: BinaryIO, remaining: int) -> None:
        self.handle = handle
        self.remaining = remaining

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self.remaining <= 0:
            raise StopIteration
        chunk = self.handle.read(min(_CHUNK_SIZE, self.remaining))
        if not chunk:
            self.remaining = 0
            raise StopIteration
        self.remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        self.handle.close()


def _file_size(handle: BinaryIO) -> int:
    position = handle.tell()
    handle.seek(0, 2)
    size = handle.tell()
    handle.seek(position)
    return size


def _range_bounds(value: str, size: int) -> tuple[int, int] | None:
    if len(value) > 200:
        return None
    match = _SINGLE_RANGE.fullmatch(value.strip())
    if not match or size <= 0:
        return None
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None
    try:
        start = int(start_text) if start_text else None
        end = int(end_text) if end_text else None
    except ValueError:
        return None
    if start is None:
        suffix = end
        if suffix <= 0:
            return None
        return max(0, size - suffix), size - 1
    if start >= size:
        return None
    end = size - 1 if end is None else min(end, size - 1)
    if end < start:
        return None
    return start, end


def _apply_headers(response: HttpResponse, headers: dict[str, str]) -> None:
    for key, value in headers.items():
        response[key] = value


def ranged_file_response(
    request,
    handle: BinaryIO,
    *,
    content_type: str,
    content_disposition: str,
    etag: str | None,
) -> HttpResponse:
    """Return a full or single-range response without loading the file in memory."""
    size = _file_size(handle)
    quoted_etag = f'"{etag}"' if etag else None
    range_header = request.headers.get("Range", "").strip()
    if "," in range_header:
        range_header = ""
    if range_header and request.headers.get("If-Range") not in (None, quoted_etag):
        range_header = ""

    common_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": content_disposition,
    }
    if quoted_etag:
        common_headers["ETag"] = quoted_etag

    if range_header:
        bounds = _range_bounds(range_header, size)
        if bounds is None:
            handle.close()
            response = HttpResponse(status=416, content_type=content_type)
            _apply_headers(response, common_headers)
            response.headers["Content-Range"] = f"bytes */{size}"
            response.headers["Content-Length"] = "0"
            return response
        start, end = bounds
        length = end - start + 1
        if request.method == "HEAD":
            handle.close()
            response = HttpResponse(status=206, content_type=content_type)
        else:
            handle.seek(start)
            response = StreamingHttpResponse(
                _LimitedFileIterator(handle, length),
                status=206,
                content_type=content_type,
            )
        _apply_headers(response, common_headers)
        response.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        response.headers["Content-Length"] = str(length)
        return response

    if request.method == "HEAD":
        handle.close()
        response = HttpResponse(status=200, content_type=content_type)
        _apply_headers(response, common_headers)
        response.headers["Content-Length"] = str(size)
        return response

    response = FileResponse(handle, content_type=content_type)
    _apply_headers(response, common_headers)
    response.headers["Content-Length"] = str(size)
    return response
