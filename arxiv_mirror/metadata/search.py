"""Simple title search using CRUD functions."""

from __future__ import annotations

import unicodedata

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.crud import find_paper_by_normalized_title, search_papers_by_title_trgm
from ..models.db import ArxivPaper


async def search_by_title(
    session: AsyncSession, query: str, limit: int = 10
) -> list[ArxivPaper]:
    """Search papers by title.

    First tries exact match on normalized_title (NFKC + lowercase + collapse
    whitespace). If no exact match, falls back to trigram similarity search.
    """
    normalized = _normalize_title(query)

    # Try exact match first
    exact = await find_paper_by_normalized_title(session, normalized)
    if exact is not None:
        return [exact]

    # Fall back to trigram fuzzy search
    results = await search_papers_by_title_trgm(session, query, limit=limit)
    return list(results)


def _normalize_title(title: str) -> str:
    """Normalize a title for exact matching: NFKC, lowercase, collapse whitespace."""
    s = unicodedata.normalize("NFKC", title)
    s = s.lower()
    s = " ".join(s.split())
    return s
