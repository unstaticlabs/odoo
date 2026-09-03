from io import BytesIO
from unittest import TestCase

from paperless_usl_ranges import ranged_file_response


class _Request:
    def __init__(self, *, method="GET", headers=None):
        self.method = method
        self.headers = headers or {}


class TestRangedFileResponse(TestCase):
    content = b"0123456789"

    def response(self, *, method="GET", range_header=None, if_range=None):
        headers = {}
        if range_header is not None:
            headers["Range"] = range_header
        if if_range is not None:
            headers["If-Range"] = if_range
        return ranged_file_response(
            _Request(method=method, headers=headers),
            BytesIO(self.content),
            content_type="application/pdf",
            content_disposition="attachment; filename=test.pdf",
            etag="checksum",
        )

    @staticmethod
    def body(response):
        return b"".join(response.streaming_content)

    def test_full_download_is_streamed_and_advertises_ranges(self):
        response = self.response()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Length"], "10")
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(response["ETag"], '"checksum"')
        self.assertEqual(self.body(response), self.content)

    def test_closed_open_and_suffix_ranges(self):
        cases = (
            ("bytes=2-5", "bytes 2-5/10", b"2345"),
            ("bytes=7-", "bytes 7-9/10", b"789"),
            ("bytes=-3", "bytes 7-9/10", b"789"),
        )
        for value, content_range, expected in cases:
            with self.subTest(value=value):
                response = self.response(range_header=value)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response["Content-Range"], content_range)
                self.assertEqual(self.body(response), expected)

    def test_unsatisfiable_range_returns_416(self):
        for value in ("bytes=50-60", "bytes=2-1", "bytes=" + "9" * 201 + "-"):
            with self.subTest(value=value):
                response = self.response(range_header=value)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response["Content-Range"], "bytes */10")

    def test_head_has_range_headers_without_body(self):
        response = self.response(method="HEAD", range_header="bytes=1-3")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Length"], "3")
        self.assertEqual(response.content, b"")

    def test_multiple_ranges_and_stale_if_range_fall_back_to_full_response(self):
        for range_header, if_range in (
            ("bytes=0-1,5-6", None),
            ("bytes=0-1", '"different"'),
        ):
            with self.subTest(range_header=range_header, if_range=if_range):
                response = self.response(
                    range_header=range_header,
                    if_range=if_range,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.body(response), self.content)
