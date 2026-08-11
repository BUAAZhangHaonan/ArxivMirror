"""Harvest orchestrator: full/incremental OAI-PMH sync with DB writes."""

from __future__ import annotations

import logging
import unicodedata
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ..db.crud import get_sync_state, upsert_paper, upsert_sync_state, upsert_version
from .oaipmh_client import OaiPmhClient

logger = logging.getLogger(__name__)


async def run_harvest(engine: AsyncEngine) -> None:
    """Main harvest loop. Checks sync_state, runs incremental or full harvest."""
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        sync = await get_sync_state(session, "oaipmh")

        # Create sync state row if it doesn't exist
        if sync is None:
            sync = await upsert_sync_state(
                session,
                name="oaipmh",
                status="idle",
                records_harvested=0,
            )
            await session.commit()

        # Check for stale running state (> 1 hour)
        if sync.status == "running":
            if sync.updated_at is not None:
                elapsed = datetime.now(UTC) - sync.updated_at
                if elapsed < timedelta(hours=1):
                    logger.warning(
                        "Harvest already running (updated %s ago). Skipping.",
                        elapsed,
                    )
                    return
                else:
                    logger.warning(
                        "Stale running state detected (%s old). Taking over.",
                        elapsed,
                    )
            else:
                logger.warning("Running state with no updated_at. Taking over.")

        # Mark as running
        sync = await upsert_sync_state(
            session,
            name="oaipmh",
            status="running",
            updated_at=datetime.now(UTC),
        )
        await session.commit()

    # Run harvest outside the session scope so each page gets its own session
    client = OaiPmhClient()
    try:
        # Determine from_date for incremental harvest
        async with session_factory() as session:
            sync = await get_sync_state(session, "oaipmh")
            from_date = sync.last_response_date if sync else None

        if from_date:
            logger.info("Starting incremental harvest from %s", from_date)
        else:
            logger.info("Starting full harvest")
            from_date = None

        total_harvested = 0

        async for records, _next_token in client.harvest_all(from_date=from_date):
            async with session_factory() as session:
                for rec in records:
                    if rec.get("deleted"):
                        continue

                    # Compute normalized title
                    title = rec.get("title", "")
                    normalized_title = _normalize_title(title) if title else None

                    # Upsert paper
                    paper_kwargs: dict = {}
                    if rec.get("title"):
                        paper_kwargs["title"] = rec["title"]
                    if normalized_title:
                        paper_kwargs["normalized_title"] = normalized_title
                    if rec.get("authors") is not None:
                        paper_kwargs["authors"] = rec["authors"]
                    if rec.get("abstract") is not None:
                        paper_kwargs["abstract"] = rec["abstract"]
                    if rec.get("categories") is not None:
                        paper_kwargs["categories"] = rec["categories"]
                    if rec.get("primary_category") is not None:
                        paper_kwargs["primary_category"] = rec["primary_category"]
                    if rec.get("doi") is not None:
                        paper_kwargs["doi"] = rec["doi"]
                    if rec.get("license") is not None:
                        paper_kwargs["license"] = rec["license"]
                    if rec.get("comments") is not None:
                        paper_kwargs["comments"] = rec["comments"]
                    if rec.get("journal_ref") is not None:
                        paper_kwargs["journal_ref"] = rec["journal_ref"]
                    if rec.get("oai_datestamp") is not None:
                        paper_kwargs["oai_datestamp"] = rec["oai_datestamp"]
                    paper_kwargs["source"] = "oaipmh"
                    paper_kwargs["updated_at"] = datetime.now(UTC)

                    # Version info from OAI metadata
                    versions = rec.get("versions", [])
                    if versions:
                        latest_ver = max(
                            versions, key=lambda v: v.get("version_number", 1)
                        )
                        paper_kwargs["latest_version"] = latest_ver.get(
                            "version_number", 1
                        )

                        # Extract first and latest version dates
                        version_dates = [
                            v.get("date") for v in versions if v.get("date")
                        ]
                        if version_dates:
                            paper_kwargs["first_version_date"] = version_dates[0]
                            paper_kwargs["latest_version_date"] = version_dates[-1]

                    # Store raw XML as JSON-compatible dict
                    if rec.get("raw_xml"):
                        paper_kwargs["raw_metadata"] = {"raw_xml": rec["raw_xml"]}

                    await upsert_paper(session, id=rec["id"], **paper_kwargs)

                    # Upsert versions
                    for ver in versions:
                        ver_num = ver.get("version_number", 1)
                        ver_kwargs: dict = {
                            "versioned_id": f"{rec['id']}v{ver_num}",
                            "updated_at": datetime.now(UTC),
                        }
                        if ver.get("date"):
                            ver_kwargs["version_date"] = ver["date"]
                        if rec.get("title"):
                            ver_kwargs["title_snapshot"] = rec["title"]

                        await upsert_version(
                            session,
                            base_id=rec["id"],
                            version=ver_num,
                            **ver_kwargs,
                        )

                total_harvested += len(records)

                # Update sync state progress
                await upsert_sync_state(
                    session,
                    name="oaipmh",
                    records_harvested=(sync.records_harvested or 0) + len(records)
                    if sync
                    else total_harvested,
                    updated_at=datetime.now(UTC),
                )
                await session.commit()

                # Refresh sync reference after commit
                sync = await get_sync_state(session, "oaipmh")

                logger.info(
                    "Harvested %d records (total: %d)",
                    len(records),
                    total_harvested,
                )

        # Mark success
        async with session_factory() as session:
            await upsert_sync_state(
                session,
                name="oaipmh",
                status="idle",
                last_response_date=datetime.now(UTC),
                last_success_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            await session.commit()

        logger.info("Harvest complete. Total records: %d", total_harvested)

    except Exception as e:
        logger.exception("Harvest failed")
        async with session_factory() as session:
            await upsert_sync_state(
                session,
                name="oaipmh",
                status="error",
                error_message=str(e)[:2000],
                updated_at=datetime.now(UTC),
            )
            await session.commit()
        raise
    finally:
        await client.close()


def _normalize_title(title: str) -> str:
    """Normalize a title for exact matching: NFKC, lowercase, collapse whitespace."""
    s = unicodedata.normalize("NFKC", title)
    s = s.lower()
    s = " ".join(s.split())
    return s
