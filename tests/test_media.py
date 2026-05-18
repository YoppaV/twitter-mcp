"""Tests for the media-download layer: dataclasses + the ``downloader`` helper.

The ``downloader`` is pure (no Playwright): it streams bytes off Twitter's CDN
via an ``httpx.AsyncClient``. Here we drive it with a fake client whose
``stream()`` returns an async context manager that yields canned chunks, so
nothing touches the network.

Run with: ``venv/bin/python -m unittest tests.test_media -v``
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from twitter_sdk import downloader  # noqa: E402
from twitter_sdk.models import (  # noqa: E402
    DownloadedMedia,
    MediaDownloadResult,
    SkippedMedia,
)

FIXTURES = Path(__file__).parent / "fixtures"
TINY_JPEG = (FIXTURES / "tiny.jpg").read_bytes()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeStreamResponse:
    """Async context manager mimicking ``httpx``'s streaming Response."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []
        self._raise_exc = raise_exc

    async def __aenter__(self) -> "FakeStreamResponse":
        if self._raise_exc is not None:
            raise self._raise_exc
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def aiter_bytes(self) -> Any:
        for chunk in self._chunks:
            yield chunk


class FakeAsyncClient:
    """Mimics the surface of ``httpx.AsyncClient`` used by ``fetch_bytes``."""

    def __init__(self, response: FakeStreamResponse) -> None:
        self._response = response
        self.stream_calls: list[tuple[str, str]] = []

    def stream(self, method: str, url: str, **_: Any) -> FakeStreamResponse:
        self.stream_calls.append((method, url))
        return self._response


class DataclassTests(unittest.TestCase):
    def test_downloaded_media_roundtrips(self) -> None:
        dm = DownloadedMedia(
            kind="photo",
            source_url="https://pbs.twimg.com/media/x.jpg",
            saved_path="/tmp/x.jpg",
            byte_size=287,
            content_type="image/jpeg",
        )
        d = asdict(dm)
        self.assertEqual(d["kind"], "photo")
        self.assertEqual(d["byte_size"], 287)
        self.assertEqual(d["content_type"], "image/jpeg")

    def test_skipped_media_optional_fields_default_to_none(self) -> None:
        sm = SkippedMedia(
            kind="video",
            source_url="https://video.twimg.com/v.mp4",
            reason="video_not_requested",
        )
        self.assertIsNone(sm.duration_ms)
        self.assertIsNone(sm.byte_size)

    def test_skipped_media_carries_duration(self) -> None:
        sm = SkippedMedia(
            kind="video",
            source_url="https://video.twimg.com/v.mp4",
            reason="video_not_requested",
            duration_ms=39000,
        )
        self.assertEqual(sm.duration_ms, 39000)

    def test_media_download_result_holds_both_lists(self) -> None:
        result = MediaDownloadResult(
            source_id="8000",
            source_kind="tweet",
            downloaded=(),
            skipped=(),
        )
        self.assertEqual(result.source_id, "8000")
        self.assertEqual(result.source_kind, "tweet")
        self.assertEqual(asdict(result)["downloaded"], ())


class FetchBytesTests(unittest.TestCase):
    def test_success_concatenates_chunks(self) -> None:
        response = FakeStreamResponse(
            status_code=200,
            headers={"content-type": "image/jpeg"},
            chunks=[TINY_JPEG[:120], TINY_JPEG[120:]],
        )
        client = FakeAsyncClient(response)
        outcome = _run(
            downloader.fetch_bytes(
                client,
                "https://pbs.twimg.com/media/x.jpg",
                max_bytes=downloader.IMAGE_MAX_BYTES,
            )
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.data, TINY_JPEG)
        self.assertEqual(outcome.content_type, "image/jpeg")
        self.assertIsNone(outcome.reason)
        self.assertEqual(client.stream_calls, [("GET", "https://pbs.twimg.com/media/x.jpg")])

    def test_404_is_reported(self) -> None:
        response = FakeStreamResponse(status_code=404)
        outcome = _run(
            downloader.fetch_bytes(
                FakeAsyncClient(response),
                "https://pbs.twimg.com/media/x.jpg",
                max_bytes=None,
            )
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "http_404")
        self.assertIsNone(outcome.data)

    def test_server_error_is_download_error(self) -> None:
        response = FakeStreamResponse(status_code=503)
        outcome = _run(
            downloader.fetch_bytes(
                FakeAsyncClient(response),
                "https://pbs.twimg.com/media/x.jpg",
                max_bytes=None,
            )
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "download_error")

    def test_oversized_aborts(self) -> None:
        response = FakeStreamResponse(
            status_code=200,
            headers={"content-type": "image/jpeg"},
            chunks=[b"a" * 100, b"b" * 100, b"c" * 100],
        )
        outcome = _run(
            downloader.fetch_bytes(
                FakeAsyncClient(response),
                "https://pbs.twimg.com/media/x.jpg",
                max_bytes=150,
            )
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "too_large")

    def test_network_error_is_download_error(self) -> None:
        response = FakeStreamResponse(raise_exc=RuntimeError("connection reset"))
        outcome = _run(
            downloader.fetch_bytes(
                FakeAsyncClient(response),
                "https://pbs.twimg.com/media/x.jpg",
                max_bytes=None,
            )
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "download_error")

    def test_max_bytes_none_allows_large_payload(self) -> None:
        big = b"z" * (5 * 1024 * 1024)
        response = FakeStreamResponse(
            status_code=200,
            headers={"content-type": "video/mp4"},
            chunks=[big],
        )
        outcome = _run(
            downloader.fetch_bytes(
                FakeAsyncClient(response),
                "https://video.twimg.com/v.mp4",
                max_bytes=None,
            )
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(len(outcome.data or b""), len(big))


class IsDownloadableUrlTests(unittest.TestCase):
    def test_accepts_known_twitter_cdns(self) -> None:
        self.assertTrue(
            downloader.is_downloadable_media_url("https://pbs.twimg.com/media/a.jpg")
        )
        self.assertTrue(
            downloader.is_downloadable_media_url("https://video.twimg.com/ext_tw/v.mp4")
        )
        self.assertTrue(
            downloader.is_downloadable_media_url("https://pbs.x.com/media/b.png")
        )

    def test_rejects_blob_and_data_uris(self) -> None:
        self.assertFalse(
            downloader.is_downloadable_media_url("blob:https://x.com/abc-123")
        )
        self.assertFalse(
            downloader.is_downloadable_media_url("data:image/png;base64,iVBORw0KGgo=")
        )

    def test_rejects_non_cdn_host(self) -> None:
        self.assertFalse(
            downloader.is_downloadable_media_url("https://evil.example.com/x.jpg")
        )

    def test_rejects_empty(self) -> None:
        self.assertFalse(downloader.is_downloadable_media_url(""))


class ContentTypeToFormatTests(unittest.TestCase):
    def test_known_content_types(self) -> None:
        self.assertEqual(downloader.content_type_to_format("image/jpeg", "jpg"), "jpeg")
        self.assertEqual(downloader.content_type_to_format("image/png", "jpg"), "png")
        self.assertEqual(downloader.content_type_to_format("image/webp", "jpg"), "webp")
        self.assertEqual(downloader.content_type_to_format("video/mp4", "mp4"), "mp4")

    def test_strips_parameters(self) -> None:
        self.assertEqual(
            downloader.content_type_to_format("image/webp; charset=binary", "jpg"),
            "webp",
        )

    def test_falls_back_to_extension(self) -> None:
        self.assertEqual(downloader.content_type_to_format("", "png"), "png")
        self.assertEqual(
            downloader.content_type_to_format("application/octet-stream", "mp4"),
            "mp4",
        )
        self.assertEqual(downloader.content_type_to_format("", "jpg"), "jpeg")

    def test_format_to_extension(self) -> None:
        self.assertEqual(downloader.format_to_extension("jpeg"), "jpg")
        self.assertEqual(downloader.format_to_extension("png"), "png")
        self.assertEqual(downloader.format_to_extension("mp4"), "mp4")


if __name__ == "__main__":
    unittest.main()
