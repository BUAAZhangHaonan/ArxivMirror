from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from arxiv_mirror.models.enums import ResolverState
from arxiv_mirror.models.schemas import ResolvedPaper
from arxiv_mirror.pdf.downloader import DownloadResult, PdfDownloadError
from arxiv_mirror.pdf.repository import PdfAssetRecord, PdfAssetRepositoryError
from arxiv_mirror.pdf.service import PdfAssetStateError, PdfDownloadService
from arxiv_mirror.pdf.store import PdfStore


def _paper(versioned_id: str = "2608.12345v1") -> ResolvedPaper:
    return ResolvedPaper(
        versioned_id=versioned_id,
        arxiv_id=versioned_id.rsplit("v", 1)[0],
        version=int(versioned_id.rsplit("v", 1)[1]),
        state=ResolverState.RESOLVED,
    )


class MemoryRepository:
    def __init__(self, records: list[PdfAssetRecord] | None = None) -> None:
        self.records = {record.versioned_id: record for record in records or []}

    async def get_or_create(self, paper: ResolvedPaper) -> PdfAssetRecord:
        record = self.records.get(paper.versioned_id)
        if record is None:
            record = PdfAssetRecord(
                id=uuid.uuid4(),
                versioned_id=paper.versioned_id,
                arxiv_id=paper.arxiv_id,
                version=paper.version,
                source="pending",
                local_path=None,
                file_size=None,
            )
            self.records[paper.versioned_id] = record
        return record

    async def mark_downloading(self, record: PdfAssetRecord) -> PdfAssetRecord:
        assert record.source == "pending"
        return self._replace(record, source="downloading")

    async def mark_completed(
        self,
        record: PdfAssetRecord,
        local_path: str,
        file_size: int,
    ) -> PdfAssetRecord:
        assert record.source == "downloading"
        return self._replace(
            record,
            source="remote",
            local_path=local_path,
            file_size=file_size,
        )

    async def mark_failed(self, record: PdfAssetRecord) -> PdfAssetRecord:
        assert record.source == "downloading"
        return self._replace(
            record,
            source="failed",
            local_path=None,
            file_size=None,
        )

    def _replace(self, record: PdfAssetRecord, **values) -> PdfAssetRecord:
        updated = PdfAssetRecord(
            **{**record.__dict__, **values},
        )
        self.records[record.versioned_id] = updated
        return updated


class WritingDownloader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def download(self, versioned_id: str, destination: Path) -> DownloadResult:
        self.calls.append(versioned_id)
        await asyncio.sleep(0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = b"%PDF-1.7\ncontent"
        destination.write_bytes(content)
        return DownloadResult(local_path=destination, file_size=len(content))

    async def close(self) -> None:
        pass


class FailingDownloader(WritingDownloader):
    async def download(self, versioned_id: str, destination: Path) -> DownloadResult:
        self.calls.append(versioned_id)
        raise PdfDownloadError(f"PDF download failed for {versioned_id}")


class FailIfCalledDownloader(WritingDownloader):
    async def download(self, versioned_id: str, destination: Path) -> DownloadResult:
        raise AssertionError("downloader must not be called")


class CompletionFailingRepository(MemoryRepository):
    async def mark_completed(
        self,
        record: PdfAssetRecord,
        local_path: str,
        file_size: int,
    ) -> PdfAssetRecord:
        raise PdfAssetRepositoryError("database completion failed")


@pytest.mark.asyncio
async def test_concurrent_same_id_downloads_once(tmp_path: Path) -> None:
    repository = MemoryRepository()
    downloader = WritingDownloader()
    service = PdfDownloadService(repository, downloader, PdfStore(tmp_path))

    first, second = await asyncio.gather(
        service.fetch(_paper()),
        service.fetch(_paper()),
    )

    assert downloader.calls == ["2608.12345v1"]
    assert first == second
    assert first.source == "remote"


@pytest.mark.asyncio
async def test_only_explicit_target_is_processed(tmp_path: Path) -> None:
    pending = [
        PdfAssetRecord(
            id=uuid.uuid4(),
            versioned_id=f"2608.{index:05d}v1",
            arxiv_id=f"2608.{index:05d}",
            version=1,
            source="pending",
            local_path=None,
            file_size=None,
        )
        for index in range(1, 45)
    ]
    repository = MemoryRepository(pending)
    downloader = WritingDownloader()
    service = PdfDownloadService(repository, downloader, PdfStore(tmp_path))

    target = _paper("2608.00044v1")
    await service.fetch(target)

    assert downloader.calls == [target.versioned_id]
    assert repository.records[target.versioned_id].source == "remote"
    assert all(
        repository.records[record.versioned_id].source == "pending"
        for record in pending[:-1]
    )


@pytest.mark.asyncio
async def test_download_failure_is_terminal_and_not_retried(tmp_path: Path) -> None:
    repository = MemoryRepository()
    downloader = FailingDownloader()
    service = PdfDownloadService(repository, downloader, PdfStore(tmp_path))

    with pytest.raises(PdfDownloadError):
        await service.fetch(_paper())
    with pytest.raises(PdfAssetStateError, match="failed"):
        await service.fetch(_paper())

    assert downloader.calls == ["2608.12345v1"]
    assert repository.records["2608.12345v1"].source == "failed"


@pytest.mark.asyncio
async def test_completion_failure_removes_file_and_marks_failed(tmp_path: Path) -> None:
    repository = CompletionFailingRepository()
    service = PdfDownloadService(repository, WritingDownloader(), PdfStore(tmp_path))
    destination = PdfStore(tmp_path).get_path("2608.12345v1")

    with pytest.raises(PdfAssetRepositoryError, match="completion failed"):
        await service.fetch(_paper())

    assert not destination.exists()
    assert repository.records["2608.12345v1"].source == "failed"


@pytest.mark.asyncio
async def test_mismatched_versioned_id_fails_before_repository(tmp_path: Path) -> None:
    repository = MemoryRepository()
    service = PdfDownloadService(
        repository,
        FailIfCalledDownloader(),
        PdfStore(tmp_path),
    )
    paper = _paper()
    paper.versioned_id = "2608.12345v2"

    with pytest.raises(PdfAssetStateError, match="does not match"):
        await service.fetch(paper)

    assert repository.records == {}


@pytest.mark.asyncio
async def test_ready_asset_never_calls_downloader(tmp_path: Path) -> None:
    store = PdfStore(tmp_path)
    paper = _paper()
    path = store.get_path(paper.versioned_id)
    path.parent.mkdir(parents=True)
    content = b"%PDF-1.7\ncontent"
    path.write_bytes(content)
    ready = PdfAssetRecord(
        id=uuid.uuid4(),
        versioned_id=paper.versioned_id,
        arxiv_id=paper.arxiv_id,
        version=paper.version,
        source="remote",
        local_path=str(path),
        file_size=len(content),
    )
    service = PdfDownloadService(
        MemoryRepository([ready]),
        FailIfCalledDownloader(),
        store,
    )

    assert await service.fetch(paper) == ready


@pytest.mark.asyncio
async def test_invalid_ready_asset_fails_without_download(tmp_path: Path) -> None:
    paper = _paper()
    ready = PdfAssetRecord(
        id=uuid.uuid4(),
        versioned_id=paper.versioned_id,
        arxiv_id=paper.arxiv_id,
        version=paper.version,
        source="remote",
        local_path=str(tmp_path / "wrong.pdf"),
        file_size=1,
    )
    service = PdfDownloadService(
        MemoryRepository([ready]),
        FailIfCalledDownloader(),
        PdfStore(tmp_path),
    )

    with pytest.raises(PdfAssetStateError, match="path is invalid"):
        await service.fetch(paper)
