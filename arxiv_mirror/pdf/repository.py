from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.db import ArxivPaper, PaperVersion, PdfAsset
from ..models.schemas import ResolvedPaper


class PdfAssetRepositoryError(RuntimeError):
    """Raised when a PDF asset does not match the required state transition."""


@dataclass(frozen=True)
class PdfAssetRecord:
    id: uuid.UUID
    versioned_id: str
    arxiv_id: str
    version: int
    source: str
    local_path: str | None
    file_size: int | None

    @classmethod
    def from_model(cls, asset: PdfAsset) -> PdfAssetRecord:
        return cls(
            id=asset.id,
            versioned_id=asset.versioned_id,
            arxiv_id=asset.arxiv_id,
            version=asset.version,
            source=asset.source,
            local_path=asset.local_path,
            file_size=asset.file_size,
        )


class PdfAssetRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create(self, paper: ResolvedPaper) -> PdfAssetRecord:
        async with self._session_factory() as session:
            now = datetime.now(UTC)
            await session.execute(
                insert(ArxivPaper)
                .values(
                    id=paper.arxiv_id,
                    title=paper.title or "",
                    latest_version=paper.version,
                    source="explicit_request",
                    inserted_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[ArxivPaper.id])
            )
            await session.execute(
                insert(PaperVersion)
                .values(
                    base_id=paper.arxiv_id,
                    version=paper.version,
                    versioned_id=paper.versioned_id,
                    title_snapshot=paper.title,
                    pdf_status="pending",
                )
                .on_conflict_do_nothing(
                    index_elements=[PaperVersion.base_id, PaperVersion.version]
                )
            )
            asset = await self._get(session, paper.versioned_id)
            if asset is None:
                await session.execute(
                    insert(PdfAsset)
                    .values(
                        id=uuid.uuid4(),
                        versioned_id=paper.versioned_id,
                        arxiv_id=paper.arxiv_id,
                        version=paper.version,
                        source="pending",
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=[PdfAsset.versioned_id])
                )
            await session.commit()
            version = await session.get(
                PaperVersion,
                (paper.arxiv_id, paper.version),
            )
            if version is None or version.versioned_id != paper.versioned_id:
                raise PdfAssetRepositoryError(
                    f"Paper version is inconsistent for {paper.versioned_id}"
                )
            asset = await self._get(session, paper.versioned_id)
            if asset is None:
                raise PdfAssetRepositoryError(
                    f"PDF asset was not created for {paper.versioned_id}"
                )
            if asset.arxiv_id != paper.arxiv_id or asset.version != paper.version:
                raise PdfAssetRepositoryError(
                    f"PDF asset identity is inconsistent for {paper.versioned_id}"
                )
            return PdfAssetRecord.from_model(asset)

    async def mark_downloading(self, record: PdfAssetRecord) -> PdfAssetRecord:
        return await self._transition(
            record,
            expected="pending",
            source="downloading",
            version_status="downloading",
        )

    async def mark_completed(
        self,
        record: PdfAssetRecord,
        local_path: str,
        file_size: int,
    ) -> PdfAssetRecord:
        return await self._transition(
            record,
            expected="downloading",
            source="remote",
            version_status="completed",
            local_path=local_path,
            file_size=file_size,
            fetched_at=datetime.now(UTC),
        )

    async def mark_failed(self, record: PdfAssetRecord) -> PdfAssetRecord:
        return await self._transition(
            record,
            expected="downloading",
            source="failed",
            version_status="failed",
            local_path=None,
            file_size=None,
        )

    async def _transition(
        self,
        record: PdfAssetRecord,
        *,
        expected: str,
        source: str,
        version_status: str,
        **values,
    ) -> PdfAssetRecord:
        async with self._session_factory() as session:
            result = await session.execute(
                update(PdfAsset)
                .where(PdfAsset.id == record.id, PdfAsset.source == expected)
                .values(source=source, **values)
                .returning(PdfAsset)
            )
            asset = result.scalar_one_or_none()
            if asset is None:
                await session.rollback()
                raise PdfAssetRepositoryError(
                    f"PDF asset {record.versioned_id} is not {expected}"
                )

            await session.execute(
                update(PaperVersion)
                .where(
                    PaperVersion.base_id == record.arxiv_id,
                    PaperVersion.version == record.version,
                )
                .values(pdf_status=version_status)
            )
            await session.commit()
            return PdfAssetRecord.from_model(asset)

    @staticmethod
    async def _get(session: AsyncSession, versioned_id: str) -> PdfAsset | None:
        result = await session.execute(
            select(PdfAsset).where(PdfAsset.versioned_id == versioned_id)
        )
        return result.scalar_one_or_none()
