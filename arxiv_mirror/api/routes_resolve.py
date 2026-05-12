"""Routes for resolving paper inputs (arXiv ID, DOI, URL, title)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.engine import get_session
from ..models.schemas import ResolveRequest, ResolveResponse
from ..resolver.normalizer import resolve as resolve_paper
from ..resolver.parser import parse_input

router = APIRouter(tags=["resolve"])


@router.post("/resolve", response_model=ResolveResponse)
async def resolve(
    req: ResolveRequest,
    session: AsyncSession = Depends(get_session),
):
    parsed = parse_input(req.query)
    t0 = time.monotonic()
    response = await resolve_paper(session, parsed)
    # Audit logging is handled inside resolve_paper normalizer.
    # get_session auto-commits on success.
    return response
