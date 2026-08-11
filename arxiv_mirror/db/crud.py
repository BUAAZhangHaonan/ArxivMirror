from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db import (
    ArxivPaper,
    PaperVersion,
    PdfAsset,
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


async def get_pdf_asset(session: AsyncSession, versioned_id: str) -> PdfAsset | None:
    result = await session.execute(
        select(PdfAsset).where(PdfAsset.versioned_id == versioned_id)
    )
    return result.scalar_one_or_none()


async def count_pdf_assets(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(PdfAsset.id)))
    return result.scalar_one()


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
