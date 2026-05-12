from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncEngine

from ..config import get_settings
from ..db.crud import claim_pdf_assets, update_pdf_asset
from ..db.engine import get_session_factory
from .downloader import PdfDownloader
from .s3_mirror import S3Mirror
from .store import PdfStore

logger = logging.getLogger(__name__)


async def run_worker(engine: AsyncEngine) -> None:
    """Main worker loop. Claims pdf_assets atomically, downloads them concurrently."""
    settings = get_settings()
    store = PdfStore(settings.pdf_storage_dir)
    downloader = PdfDownloader()
    s3 = S3Mirror()
    session_factory = get_session_factory()
    semaphore = asyncio.Semaphore(settings.pdf_download_concurrency)

    try:
        while True:
            # Claim a batch of assets atomically (SELECT FOR UPDATE SKIP LOCKED)
            async with session_factory() as session:
                assets = await claim_pdf_assets(
                    session, limit=settings.pdf_download_concurrency * 2
                )
                await session.commit()

            if not assets:
                await asyncio.sleep(5)
                continue

            # Download concurrently with bounded concurrency
            async with asyncio.TaskGroup() as tg:
                for asset in assets:
                    tg.create_task(
                        _process_asset(
                            asset, downloader, s3, store, session_factory, semaphore
                        )
                    )
    finally:
        await downloader.close()


async def _process_asset(
    asset,
    downloader: PdfDownloader,
    s3: S3Mirror,
    store: PdfStore,
    session_factory,
    semaphore: asyncio.Semaphore,
) -> None:
    """Download a single asset with semaphore-bounded concurrency."""
    async with semaphore:
        try:
            dest = store.get_path(asset.versioned_id)
            success = False

            if s3.enabled:
                success = await s3.fetch_pdf(asset.versioned_id, dest)

            if not success:
                result = await downloader.download(asset.versioned_id, dest)
                if result.success:
                    async with session_factory() as session:
                        await update_pdf_asset(
                            session,
                            asset.id,
                            source="remote",
                            local_path=str(result.local_path),
                            sha256=result.sha256,
                            file_size=result.file_size,
                            fetched_at=datetime.now(timezone.utc),
                        )
                        await session.commit()
                else:
                    logger.error(
                        "Failed to download %s: %s",
                        asset.versioned_id,
                        result.error,
                    )
                    async with session_factory() as session:
                        await update_pdf_asset(session, asset.id, source="failed")
                        await session.commit()
            else:
                # S3 fetch succeeded, mark as remote
                async with session_factory() as session:
                    await update_pdf_asset(
                        session,
                        asset.id,
                        source="remote",
                        local_path=str(dest),
                        fetched_at=datetime.now(timezone.utc),
                    )
                    await session.commit()
        except Exception:
            logger.exception("Error processing pdf asset %s", asset.versioned_id)
            try:
                async with session_factory() as session:
                    await update_pdf_asset(session, asset.id, source="failed")
                    await session.commit()
            except Exception:
                logger.exception(
                    "Failed to mark asset %s as failed", asset.versioned_id
                )
