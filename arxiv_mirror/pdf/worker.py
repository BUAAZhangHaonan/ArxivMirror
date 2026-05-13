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
    """Producer-consumer worker for pipelined PDF downloads.

    One producer continuously claims pending assets from DB and feeds them
    into a bounded queue.  N consumers each pull from the queue, download,
    and loop — so a fast slot picks up the next task immediately without
    waiting for the rest of the batch.
    """
    settings = get_settings()
    concurrency = settings.pdf_download_concurrency
    store = PdfStore(settings.pdf_storage_dir)
    downloader = PdfDownloader()
    s3 = S3Mirror()
    session_factory = get_session_factory()

    queue: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 2)

    async def producer() -> None:
        while True:
            try:
                async with session_factory() as session:
                    assets = await claim_pdf_assets(session, limit=concurrency)
                    await session.commit()
                if not assets:
                    await asyncio.sleep(5)
                    continue
                for asset in assets:
                    await queue.put(asset)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Producer error, retrying in 5s")
                await asyncio.sleep(5)

    async def consumer() -> None:
        while True:
            asset = await queue.get()
            try:
                await _process_asset(asset, downloader, s3, store, session_factory)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error processing pdf asset %s", asset.versioned_id)
            finally:
                queue.task_done()

    producer_task = asyncio.create_task(producer())
    consumer_tasks = [asyncio.create_task(consumer()) for _ in range(concurrency)]

    try:
        await asyncio.gather(producer_task, *consumer_tasks)
    finally:
        await downloader.close()
        await s3.close()


async def _process_asset(
    asset,
    downloader: PdfDownloader,
    s3: S3Mirror,
    store: PdfStore,
    session_factory,
) -> None:
    try:
        dest = store.get_path(asset.versioned_id)

        # Try S3 mirror first
        if s3.enabled:
            result = await s3.fetch_pdf(asset.versioned_id, dest)
            if result.success:
                async with session_factory() as session:
                    await update_pdf_asset(
                        session,
                        asset.id,
                        source="s3_mirror",
                        local_path=str(result.local_path),
                        sha256=result.sha256,
                        file_size=result.file_size,
                        fetched_at=datetime.now(timezone.utc),
                    )
                    await session.commit()
                return

            logger.warning(
                "S3 fetch failed for %s: %s, falling back to HTTP",
                asset.versioned_id,
                result.error,
            )

        # HTTP download
        dl_result = await downloader.download(asset.versioned_id, dest)
        if dl_result.success:
            async with session_factory() as session:
                await update_pdf_asset(
                    session,
                    asset.id,
                    source="remote",
                    local_path=str(dl_result.local_path),
                    sha256=dl_result.sha256,
                    file_size=dl_result.file_size,
                    fetched_at=datetime.now(timezone.utc),
                )
                await session.commit()
        else:
            logger.error("Failed to download %s: %s", asset.versioned_id, dl_result.error)
            async with session_factory() as session:
                await update_pdf_asset(session, asset.id, source="failed")
                await session.commit()
    except Exception:
        logger.exception("Error processing pdf asset %s", asset.versioned_id)
        try:
            async with session_factory() as session:
                await update_pdf_asset(session, asset.id, source="failed")
                await session.commit()
        except Exception:
            logger.exception("Failed to mark asset %s as failed", asset.versioned_id)
