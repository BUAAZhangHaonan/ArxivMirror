from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from arxiv_mirror.config import Settings
from arxiv_mirror.pdf.downloader import PdfDownloader, PdfDownloadError
from arxiv_mirror.pdf.store import PdfStore


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        assert size == 64 * 1024
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        chunks: list[bytes] | None = None,
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self.content = FakeContent(chunks or [])
        self.content_length = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.closed = False
        self.calls: list[tuple[str, str | None]] = []

    def get(self, url: str, *, proxy: str | None):
        self.calls.append((url, proxy))
        return self.response

    async def close(self) -> None:
        self.closed = True


def _downloader(
    tmp_path: Path, response: FakeResponse
) -> tuple[PdfDownloader, FakeSession]:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        pdf_download_timeout=1,
        arxiv_download_delay_seconds=0,
        pdf_max_file_size=1024,
    )
    downloader = PdfDownloader(settings)
    session = FakeSession(response)
    downloader._session = session
    return downloader, session


@pytest.mark.asyncio
async def test_download_uses_one_http_request_and_atomic_file(tmp_path: Path) -> None:
    content = [b"%PD", b"F-1.7\ncontent"]
    downloader, session = _downloader(
        tmp_path,
        FakeResponse(chunks=content, content_length=sum(map(len, content))),
    )
    destination = tmp_path / "26" / "08" / "2608.12345v1.pdf"

    result = await downloader.download("2608.12345v1", destination)

    assert len(session.calls) == 1
    assert result.local_path == destination
    assert result.file_size == destination.stat().st_size
    assert not destination.with_suffix(".pdf.tmp").exists()
    if os.name == "posix":
        assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_http_failure_is_not_retried(tmp_path: Path) -> None:
    downloader, session = _downloader(tmp_path, FakeResponse(status=503))
    destination = tmp_path / "2608.12345v1.pdf"

    with pytest.raises(PdfDownloadError, match="HTTP 503"):
        await downloader.download("2608.12345v1", destination)

    assert len(session.calls) == 1
    assert not destination.exists()
    assert not destination.with_suffix(".pdf.tmp").exists()


@pytest.mark.asyncio
async def test_non_pdf_response_is_rejected(tmp_path: Path) -> None:
    downloader, session = _downloader(
        tmp_path,
        FakeResponse(chunks=[b"not a PDF"]),
    )
    destination = tmp_path / "2608.12345v1.pdf"

    with pytest.raises(PdfDownloadError, match="not a PDF"):
        await downloader.download("2608.12345v1", destination)

    assert len(session.calls) == 1
    assert not destination.exists()


@pytest.mark.asyncio
async def test_existing_path_fails_before_http_request(tmp_path: Path) -> None:
    downloader, session = _downloader(
        tmp_path,
        FakeResponse(chunks=[b"%PDF-1.7"]),
    )
    destination = tmp_path / "2608.12345v1.pdf"
    destination.write_bytes(b"existing")

    with pytest.raises(PdfDownloadError, match="path is not empty"):
        await downloader.download("2608.12345v1", destination)

    assert session.calls == []
    assert destination.read_bytes() == b"existing"


@pytest.mark.parametrize(
    "versioned_id",
    ["2608.12345", "2613.12345v1", "../../paper.v1", "2608.12345v0"],
)
def test_store_rejects_invalid_versioned_ids(
    tmp_path: Path,
    versioned_id: str,
) -> None:
    with pytest.raises(ValueError, match="Invalid versioned arXiv ID"):
        PdfStore(tmp_path).get_path(versioned_id)
