from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db import (
    ArxivPaper,
    BatchItem,
    BatchJob,
    PdfAsset,
    ParsedText,
    PaperVersion,
    ResolverAudit,
    SyncState,
)


# --- ArxivPaper ---

async def upsert_paper(session: AsyncSession, *, id: str, **kwargs) -> ArxivPaper:
    stmt = insert(ArxivPaper).values(id=id, **kwargs)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={k: stmt.excluded[k] for k in kwargs},
    )
    await session.execute(stmt)
    await session.flush()
    result = await session.execute(select(ArxivPaper).where(ArxivPaper.id == id))
    return result.scalar_one()


async def get_paper(session: AsyncSession, paper_id: str) -> ArxivPaper | None:
    result = await session.execute(select(ArxivPaper).where(ArxivPaper.id == paper_id))
    return result.scalar_one_or_none()


async def get_paper_by_doi(session: AsyncSession, doi: str) -> ArxivPaper | None:
    result = await session.execute(select(ArxivPaper).where(ArxivPaper.doi == doi))
    return result.scalar_one_or_none()


async def find_paper_by_normalized_title(
    session: AsyncSession, normalized_title: str
) -> ArxivPaper | None:
    result = await session.execute(
        select(ArxivPaper).where(ArxivPaper.normalized_title == normalized_title)
    )
    return result.scalar_one_or_none()


async def search_papers_by_title_trgm(
    session: AsyncSession, title: str, limit: int = 10, threshold: float = 0.3
) -> list[ArxivPaper]:
    similarity = func.similarity(ArxivPaper.title, title)
    result = await session.execute(
        select(ArxivPaper)
        .where(similarity >= threshold)
        .order_by(similarity.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_papers(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(ArxivPaper.id)))
    return result.scalar_one()


# --- PaperVersion ---

async def upsert_version(
    session: AsyncSession, *, base_id: str, version: int, **kwargs
) -> PaperVersion:
    stmt = insert(PaperVersion).values(base_id=base_id, version=version, **kwargs)
    stmt = stmt.on_conflict_do_update(
        index_elements=["base_id", "version"],
        set_={k: stmt.excluded[k] for k in kwargs},
    )
    await session.execute(stmt)
    await session.flush()
    result = await session.execute(
        select(PaperVersion).where(
            PaperVersion.base_id == base_id, PaperVersion.version == version
        )
    )
    return result.scalar_one()


async def get_version(
    session: AsyncSession, base_id: str, version: int
) -> PaperVersion | None:
    result = await session.execute(
        select(PaperVersion).where(
            PaperVersion.base_id == base_id, PaperVersion.version == version
        )
    )
    return result.scalar_one_or_none()


async def get_latest_version(
    session: AsyncSession, base_id: str
) -> PaperVersion | None:
    result = await session.execute(
        select(PaperVersion)
        .where(PaperVersion.base_id == base_id)
        .order_by(PaperVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# --- PdfAsset ---

async def create_pdf_asset(
    session: AsyncSession, *, versioned_id: str, arxiv_id: str, version: int, **kwargs
) -> PdfAsset:
    asset = PdfAsset(
        versioned_id=versioned_id, arxiv_id=arxiv_id, version=version, **kwargs
    )
    session.add(asset)
    await session.flush()
    return asset


async def get_pdf_asset(session: AsyncSession, versioned_id: str) -> PdfAsset | None:
    result = await session.execute(
        select(PdfAsset).where(PdfAsset.versioned_id == versioned_id)
    )
    return result.scalar_one_or_none()


async def get_pdf_asset_by_id(session: AsyncSession, asset_id: uuid.UUID) -> PdfAsset | None:
    result = await session.execute(select(PdfAsset).where(PdfAsset.id == asset_id))
    return result.scalar_one_or_none()


async def get_pdf_asset_by_sha256(
    session: AsyncSession, sha256: str
) -> PdfAsset | None:
    result = await session.execute(
        select(PdfAsset).where(PdfAsset.sha256 == sha256)
    )
    return result.scalar_one_or_none()


async def update_pdf_asset(
    session: AsyncSession, asset_id: uuid.UUID, **kwargs
) -> None:
    await session.execute(update(PdfAsset).where(PdfAsset.id == asset_id).values(**kwargs))


async def count_pdf_assets(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(PdfAsset.id)))
    return result.scalar_one()


async def get_pending_pdf_assets(
    session: AsyncSession, limit: int = 50
) -> list[PdfAsset]:
    result = await session.execute(
        select(PdfAsset)
        .where(PdfAsset.source == "pending")
        .order_by(PdfAsset.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


async def claim_pdf_assets(
    session: AsyncSession, limit: int = 50
) -> list[PdfAsset]:
    """Atomically claim pending PDF assets using SELECT FOR UPDATE SKIP LOCKED."""
    result = await session.execute(
        select(PdfAsset)
        .where(PdfAsset.source == "pending")
        .order_by(PdfAsset.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    assets = list(result.scalars().all())
    if assets:
        ids = [a.id for a in assets]
        await session.execute(
            update(PdfAsset)
            .where(PdfAsset.id.in_(ids))
            .values(source="downloading")
        )
        await session.flush()
    return assets


# --- ParsedText ---

async def create_parsed_text(
    session: AsyncSession, *, pdf_asset_id: uuid.UUID, versioned_id: str, **kwargs
) -> ParsedText:
    pt = ParsedText(pdf_asset_id=pdf_asset_id, versioned_id=versioned_id, **kwargs)
    session.add(pt)
    await session.flush()
    return pt


async def get_parsed_text(session: AsyncSession, versioned_id: str) -> ParsedText | None:
    result = await session.execute(
        select(ParsedText).where(ParsedText.versioned_id == versioned_id)
    )
    return result.scalar_one_or_none()


async def update_parsed_text(
    session: AsyncSession, text_id: uuid.UUID, **kwargs
) -> None:
    await session.execute(
        update(ParsedText).where(ParsedText.id == text_id).values(**kwargs)
    )


async def get_pending_parsed_texts(
    session: AsyncSession, limit: int = 50
) -> list[ParsedText]:
    result = await session.execute(
        select(ParsedText)
        .where(ParsedText.parse_status == "pending")
        .limit(limit)
    )
    return list(result.scalars().all())


# --- SyncState ---

async def get_sync_state(session: AsyncSession, name: str) -> SyncState | None:
    result = await session.execute(select(SyncState).where(SyncState.name == name))
    return result.scalar_one_or_none()


async def upsert_sync_state(session: AsyncSession, *, name: str, **kwargs) -> SyncState:
    stmt = insert(SyncState).values(name=name, **kwargs)
    stmt = stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={k: stmt.excluded[k] for k in kwargs},
    )
    await session.execute(stmt)
    await session.flush()
    result = await session.execute(select(SyncState).where(SyncState.name == name))
    return result.scalar_one()


# --- ResolverAudit ---

async def create_resolver_audit(
    session: AsyncSession,
    *,
    input: str,
    input_type: str | None = None,
    resolved_versioned_id: str | None = None,
    strategy: str | None = None,
    latency_ms: int | None = None,
) -> ResolverAudit:
    audit = ResolverAudit(
        input=input,
        input_type=input_type,
        resolved_versioned_id=resolved_versioned_id,
        strategy=strategy,
        latency_ms=latency_ms,
    )
    session.add(audit)
    await session.flush()
    return audit


# --- BatchJob ---

async def create_batch_job(session: AsyncSession, **kwargs) -> BatchJob:
    job = BatchJob(**kwargs)
    session.add(job)
    await session.flush()
    return job


async def get_batch_job(session: AsyncSession, job_id: uuid.UUID) -> BatchJob | None:
    result = await session.execute(select(BatchJob).where(BatchJob.id == job_id))
    return result.scalar_one_or_none()


async def update_batch_job(session: AsyncSession, job_id: uuid.UUID, **kwargs) -> None:
    await session.execute(update(BatchJob).where(BatchJob.id == job_id).values(**kwargs))


# --- BatchItem ---

async def create_batch_item(
    session: AsyncSession, *, batch_id: uuid.UUID, versioned_id: str, arxiv_id: str, **kwargs
) -> BatchItem:
    item = BatchItem(batch_id=batch_id, versioned_id=versioned_id, arxiv_id=arxiv_id, **kwargs)
    session.add(item)
    await session.flush()
    return item


async def get_batch_items(session: AsyncSession, batch_id: uuid.UUID) -> list[BatchItem]:
    result = await session.execute(
        select(BatchItem).where(BatchItem.batch_id == batch_id)
    )
    return list(result.scalars().all())


async def update_batch_item(session: AsyncSession, item_id: uuid.UUID, **kwargs) -> None:
    await session.execute(
        update(BatchItem).where(BatchItem.id == item_id).values(**kwargs)
    )
