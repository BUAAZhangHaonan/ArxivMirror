from __future__ import annotations

import asyncio

from sqlalchemy.exc import SQLAlchemyError

from ..models.schemas import ResolvedPaper
from .downloader import PdfDownloader, PdfDownloadError
from .repository import PdfAssetRecord, PdfAssetRepository, PdfAssetRepositoryError
from .store import PdfStore


class PdfAssetStateError(RuntimeError):
    """Raised when an asset is not in a state that permits a download."""


class PdfDownloadService:
    def __init__(
        self,
        repository: PdfAssetRepository,
        downloader: PdfDownloader,
        store: PdfStore,
    ) -> None:
        self._repository = repository
        self._downloader = downloader
        self._store = store
        self._lock = asyncio.Lock()

    async def fetch(self, paper: ResolvedPaper) -> PdfAssetRecord:
        async with self._lock:
            expected_id = f"{paper.arxiv_id}v{paper.version}"
            if paper.versioned_id != expected_id:
                raise PdfAssetStateError(
                    f"Versioned ID does not match paper version: {paper.versioned_id}"
                )
            destination = self._store.get_path(paper.versioned_id)
            record = await self._repository.get_or_create(paper)
            if record.source == "remote":
                return self._validated_ready(record)
            if record.source != "pending":
                raise PdfAssetStateError(
                    f"PDF asset {record.versioned_id} is {record.source}"
                )

            downloading = await self._repository.mark_downloading(record)
            try:
                result = await self._downloader.download(
                    record.versioned_id,
                    destination,
                )
                completed = await self._repository.mark_completed(
                    downloading,
                    str(result.local_path),
                    result.file_size,
                )
            except asyncio.CancelledError:
                destination.unlink(missing_ok=True)
                await self._repository.mark_failed(downloading)
                raise
            except (PdfDownloadError, PdfAssetRepositoryError, SQLAlchemyError):
                destination.unlink(missing_ok=True)
                await self._repository.mark_failed(downloading)
                raise

            return self._validated_ready(completed)

    def _validated_ready(self, record: PdfAssetRecord) -> PdfAssetRecord:
        try:
            self._store.validate_ready(
                record.versioned_id,
                record.local_path,
                record.file_size,
            )
        except ValueError as exc:
            raise PdfAssetStateError(str(exc)) from exc
        return record

    async def close(self) -> None:
        await self._downloader.close()


__all__ = ["PdfAssetStateError", "PdfDownloadError", "PdfDownloadService"]
