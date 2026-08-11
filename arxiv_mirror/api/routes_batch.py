"""Batch paper resolution without download side effects."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..db.engine import get_session_factory
from ..models.schemas import BatchResolveRequest, ParsedInput, ResolveResponse
from ..resolver.normalizer import resolve as resolve_paper
from ..resolver.parser import parse_input

router = APIRouter(tags=["batch"])

_MAX_CONCURRENCY = 16


async def _resolve_with_semaphore(
    semaphore: asyncio.Semaphore,
    parsed: ParsedInput,
) -> ResolveResponse:
    async with semaphore, get_session_factory()() as session:
        response = await resolve_paper(session, parsed)
        await session.commit()
        return response


def _dedup_key(parsed: ParsedInput) -> str | None:
    if parsed.arxiv_id is not None:
        if parsed.version is not None:
            return f"{parsed.arxiv_id}v{parsed.version}"
        return parsed.arxiv_id
    if parsed.doi is not None:
        return parsed.doi
    return parsed.title_hint


@router.post("/batch/resolve", response_model=list[ResolveResponse])
async def batch_resolve(req: BatchResolveRequest):
    unique: list[ParsedInput] = []
    key_to_index: dict[str, int] = {}
    original_indices: list[int] = []

    for query in req.queries:
        parsed = parse_input(query)
        key = _dedup_key(parsed) or query
        if key not in key_to_index:
            key_to_index[key] = len(unique)
            unique.append(parsed)
        original_indices.append(key_to_index[key])

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    results = await asyncio.gather(
        *(_resolve_with_semaphore(semaphore, parsed) for parsed in unique)
    )
    return [results[index] for index in original_indices]
