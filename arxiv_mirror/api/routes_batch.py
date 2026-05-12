"""Routes for batch resolve and batch download with deduplication."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.crud import (
    create_batch_item,
    create_batch_job,
    create_pdf_asset,
    get_batch_items,
    get_batch_job,
    get_pdf_asset,
    update_batch_job,
    update_batch_item,
)
from ..db.engine import get_session, get_session_factory
from ..models.enums import ResolverState
from ..models.schemas import (
    BatchDownloadRequest,
    BatchDownloadResponse,
    BatchResolveRequest,
    BatchStatusResponse,
    ResolveResponse,
    ResolvedPaper,
)
from ..resolver.normalizer import resolve as resolve_paper
from ..resolver.parser import parse_input

router = APIRouter(tags=["batch"])

# Bounded concurrency for outbound resolve calls.
_DEFAULT_CONCURRENCY = 16


async def _resolve_with_semaphore(
    sem: asyncio.Semaphore,
    parsed,
) -> ResolveResponse:
    """Resolve a single paper with its own session, respecting concurrency semaphore."""
    async with sem:
        session_factory = get_session_factory()
        async with session_factory() as session:
            return await resolve_paper(session, parsed)


def _dedup_key_from_parsed(parsed) -> str | None:
    """Return a deduplication key from a ParsedInput.

    Priority: arxiv_id > doi > title_hint.  Returns None if nothing is set.
    """
    if parsed.arxiv_id is not None:
        key = parsed.arxiv_id
        if parsed.version is not None:
            key = f"{key}v{parsed.version}"
        return key
    if parsed.doi is not None:
        return parsed.doi
    if parsed.title_hint is not None:
        return parsed.title_hint
    return None


@router.post("/batch/resolve", response_model=list[ResolveResponse])
async def batch_resolve(
    req: BatchResolveRequest,
    session: AsyncSession = Depends(get_session),
):
    """Batch resolve queries. Deduplicate by canonical form before resolving."""
    # Parse all inputs and deduplicate.
    seen_keys: dict[str, int] = {}
    unique_parsed = []
    original_to_unique: list[int] = []

    for query in req.queries:
        parsed = parse_input(query)
        key = _dedup_key_from_parsed(parsed) or query

        if key in seen_keys:
            original_to_unique.append(seen_keys[key])
        else:
            idx = len(unique_parsed)
            seen_keys[key] = idx
            unique_parsed.append(parsed)
            original_to_unique.append(idx)

    # Resolve each unique input concurrently with bounded concurrency.
    sem = asyncio.Semaphore(_DEFAULT_CONCURRENCY)
    tasks = [_resolve_with_semaphore(sem, p) for p in unique_parsed]
    unique_results: list[ResolveResponse] = list(await asyncio.gather(*tasks))

    # Map back to original order.
    responses = [unique_results[original_to_unique[i]] for i in range(len(req.queries))]
    return responses


@router.post("/batch/download", response_model=BatchDownloadResponse)
async def batch_download(
    req: BatchDownloadRequest,
    session: AsyncSession = Depends(get_session),
):
    """Batch download: deduplicate queries, create a batch job with items."""
    total_requested = len(req.queries)
    max_concurrent = max(1, req.max_concurrent)

    # Phase 1: Parse all inputs.
    all_parsed = [parse_input(query) for query in req.queries]

    # Phase 2: Resolve all parsed inputs concurrently with bounded concurrency.
    sem = asyncio.Semaphore(max_concurrent)
    resolve_tasks = [_resolve_with_semaphore(sem, p) for p in all_parsed]
    all_resolve_resps: list[ResolveResponse] = list(await asyncio.gather(*resolve_tasks))

    # Phase 3: Deduplicate by versioned_id and count correctly.
    seen_versioned_ids: dict[str, int] = {}
    unique_entries: list[tuple] = []
    total_unresolved = 0
    total_duplicates = 0

    for resolve_resp in all_resolve_resps:
        if resolve_resp.state != ResolverState.RESOLVED or resolve_resp.result is None:
            total_unresolved += 1
            continue

        result: ResolvedPaper = resolve_resp.result
        vid = result.versioned_id

        if vid in seen_versioned_ids:
            total_duplicates += 1
        else:
            seen_versioned_ids[vid] = len(unique_entries)
            unique_entries.append((resolve_resp,))

    # total_deduplicated counts only actual duplicate versioned_ids,
    # not unresolved queries (NOT_FOUND, AMBIGUOUS).
    total_deduplicated = total_duplicates

    # Create the batch job.
    job = await create_batch_job(
        session,
        status="pending",
        total_requested=total_requested,
        total_completed=0,
        total_failed=0,
        total_deduplicated=total_deduplicated,
    )

    # Create batch items and ensure pdf_assets exist.
    for (resolve_resp,) in unique_entries:
        result = resolve_resp.result
        versioned_id = result.versioned_id

        # Ensure a pdf_asset row exists.
        asset = await get_pdf_asset(session, versioned_id)
        if asset is None:
            asset = await create_pdf_asset(
                session,
                versioned_id=versioned_id,
                arxiv_id=result.arxiv_id,
                version=result.version,
            )

        await create_batch_item(
            session,
            batch_id=job.id,
            versioned_id=versioned_id,
            arxiv_id=result.arxiv_id,
            pdf_asset_id=asset.id,
        )

    return BatchDownloadResponse(
        batch_id=job.id,
        total_requested=total_requested,
        total_deduplicated=total_deduplicated,
        status=job.status,
    )


@router.get("/job/{job_id}", response_model=BatchStatusResponse)
async def get_job_status(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get batch job status by job ID."""
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    job = await get_batch_job(session, uid)
    if job is None:
        raise HTTPException(status_code=404, detail="Batch job not found")

    items = await get_batch_items(session, job.id)

    item_dicts = [
        {
            "id": str(item.id),
            "versioned_id": item.versioned_id,
            "arxiv_id": item.arxiv_id,
            "status": item.status,
            "pdf_asset_id": str(item.pdf_asset_id) if item.pdf_asset_id else None,
            "error_message": item.error_message,
        }
        for item in items
    ]

    return BatchStatusResponse(
        batch_id=job.id,
        status=job.status,
        total_requested=job.total_requested,
        total_completed=job.total_completed,
        total_failed=job.total_failed,
        total_deduplicated=job.total_deduplicated,
        items=item_dicts,
    )
