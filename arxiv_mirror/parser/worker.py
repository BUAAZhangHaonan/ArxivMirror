"""Parser worker: polls parsed_texts with parse_status='pending' and processes them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncEngine

from ..config import get_settings
from ..db.crud import get_pending_parsed_texts, update_parsed_text
from ..db.engine import get_session_factory
from .mineru_adapter import MineruAdapter

logger = logging.getLogger(__name__)


async def run_worker(engine: AsyncEngine) -> None:
    """Main parser worker loop. Polls for pending parsed_texts and processes them."""
    settings = get_settings()
    session_factory = get_session_factory()
    adapter = MineruAdapter(
        binary=settings.mineru_binary,
        timeout=settings.mineru_timeout,
    )
    semaphore = asyncio.Semaphore(settings.mineru_concurrency)

    if not adapter.available:
        logger.warning("MinerU not available. Parser worker will idle.")

    try:
        while True:
            if not adapter.available:
                await asyncio.sleep(30)
                continue

            async with session_factory() as session:
                pending = await get_pending_parsed_texts(session, limit=20)
                if not pending:
                    await asyncio.sleep(10)
                    continue

            async with asyncio.TaskGroup() as tg:
                for text_entry in pending:
                    tg.create_task(
                        _parse_text(text_entry, adapter, session_factory, semaphore)
                    )

    finally:
        pass


async def _parse_text(text_entry, adapter, session_factory, semaphore):
    """Parse a single text entry with bounded concurrency."""
    async with semaphore:
        try:
            # Get the associated pdf_asset to find the file
            async with session_factory() as session:
                from ..db.crud import get_pdf_asset_by_id
                from pathlib import Path
                asset = await get_pdf_asset_by_id(session, text_entry.pdf_asset_id)
                if asset is None or asset.local_path is None:
                    await update_entry(session_factory, text_entry.id, "failed", "No PDF file found")
                    return

                pdf_path = Path(asset.local_path)
                if not pdf_path.exists():
                    await update_entry(session_factory, text_entry.id, "failed", f"PDF file not found: {pdf_path}")
                    return

            # Update status to parsing
            async with session_factory() as session:
                await update_parsed_text(session, text_entry.id, parse_status="parsing")
                await session.commit()

            result = await adapter.parse(pdf_path)

            if result.success:
                async with session_factory() as session:
                    await update_parsed_text(
                        session,
                        text_entry.id,
                        parse_status="completed",
                        full_text=result.full_text,
                        sections=result.sections,
                    )
                    await session.commit()
            else:
                await update_entry(session_factory, text_entry.id, "failed", result.error)

        except Exception:
            logger.exception("Error parsing text %s", text_entry.versioned_id)
            try:
                await update_entry(session_factory, text_entry.id, "failed", "Internal error")
            except Exception:
                pass


async def update_entry(session_factory, text_id, status, error_msg=None):
    async with session_factory() as session:
        kwargs = {"parse_status": status}
        if error_msg:
            kwargs["error_message"] = error_msg[:2000]
        await update_parsed_text(session, text_id, **kwargs)
        await session.commit()
