from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from arxiv_mirror.models.db import ArxivPaper, Base, PaperVersion, PdfAsset
from arxiv_mirror.models.enums import ResolverState
from arxiv_mirror.models.schemas import ResolvedPaper
from arxiv_mirror.pdf.repository import PdfAssetRepository


@pytest.mark.asyncio
async def test_repository_persists_strict_state_transitions() -> None:
    database_url = os.getenv("ARXIV_MIRROR_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip(
            "ARXIV_MIRROR_TEST_DATABASE_URL is required for repository integration"
        )

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    paper = ResolvedPaper(
        versioned_id="2608.12345v1",
        arxiv_id="2608.12345",
        version=1,
        state=ResolverState.RESOLVED,
    )
    repository = PdfAssetRepository(session_factory)
    pending = await repository.get_or_create(paper)
    downloading = await repository.mark_downloading(pending)
    completed = await repository.mark_completed(
        downloading,
        "/data/pdfs/26/08/2608.12345v1.pdf",
        512,
    )

    assert completed.source == "remote"
    async with session_factory() as session:
        stored_paper = await session.get(ArxivPaper, paper.arxiv_id)
        version = await session.get(PaperVersion, (paper.arxiv_id, paper.version))
        asset = (
            await session.execute(
                select(PdfAsset).where(PdfAsset.versioned_id == paper.versioned_id)
            )
        ).scalar_one()
        assert stored_paper is not None and stored_paper.source == "explicit_request"
        assert version is not None and version.pdf_status == "completed"
        assert asset.local_path == completed.local_path

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()
