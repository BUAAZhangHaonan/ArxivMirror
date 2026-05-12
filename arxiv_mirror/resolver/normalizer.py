"""Resolve a ParsedInput to a ResolvedPaper by querying the database."""

from __future__ import annotations

import re
import time
import unicodedata

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.crud import (
    find_paper_by_normalized_title,
    get_latest_version,
    get_paper,
    get_paper_by_doi,
    search_papers_by_title_trgm,
)
from ..db.crud import create_resolver_audit
from ..models.db import ArxivPaper, PaperVersion
from ..models.enums import ResolverState
from ..models.schemas import ParsedInput, ResolvedPaper, ResolveResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Old-style category prefix: e.g. "cs/", "math.GT/", "hep-th/"
_OLD_STYLE_PREFIX_RE = re.compile(
    r"^[a-z][a-z0-9-]*(?:\.[A-Za-z][A-Za-z0-9]*)?/",
)


def normalize_arxiv_id(raw_id: str) -> str:
    """Normalize any arXiv ID form to the canonical base_id.

    Accepts:
      - New-style: ``2501.12345``  (already canonical)
      - Old-style: ``cs/0701001``  -> ``0701.00001``
      - Versioned: ``2501.12345v2`` -> ``2501.12345``
    """
    s = raw_id.strip().lower()

    # Strip trailing version suffix
    s = re.sub(r"v\d+$", "", s)

    # Old-style: extract the YYMM and NNN components
    m = _OLD_STYLE_PREFIX_RE.match(s)
    if m:
        # Remove the prefix, leaving YYMMNNN
        digits = s[m.end():]
        if len(digits) == 7 and digits.isdigit():
            yy = digits[:2]
            mm = digits[2:4]
            nnn = digits[4:]
            seq = f"{int(nnn):05d}"
            return f"{yy}{mm}.{seq}"

    return s


def make_versioned_id(base_id: str, version: int | None) -> str:
    """Create a versioned_id string like ``2501.12345v2``.

    If *version* is ``None`` the base ID is returned without a suffix.
    """
    if version is None:
        return base_id
    return f"{base_id}v{version}"


def _normalize_title_for_lookup(title: str) -> str:
    """NFKC-normalize, collapse whitespace, lower-case a title for DB lookup."""
    t = unicodedata.normalize("NFKC", title)
    t = re.sub(r"\s+", " ", t).strip()
    return t.lower()


def _build_resolved_paper(
    paper: ArxivPaper,
    version: PaperVersion,
    state: ResolverState = ResolverState.RESOLVED,
) -> ResolvedPaper:
    """Construct a ``ResolvedPaper`` from a DB row + version."""
    versioned_id = make_versioned_id(paper.id, version.version)
    return ResolvedPaper(
        versioned_id=versioned_id,
        arxiv_id=paper.id,
        version=version.version,
        state=state,
        title=paper.title or version.title_snapshot,
        authors=paper.authors or [],
        abstract=paper.abstract,
        categories=paper.categories or [],
        doi=paper.doi,
        created_date=paper.first_version_date,
    )


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

async def resolve(
    session: AsyncSession,
    parsed: ParsedInput,
) -> ResolveResponse:
    """Resolve a ``ParsedInput`` to a ``ResolveResponse``.

    Strategy order:
      1. arxiv_id  -> direct DB lookup by primary key
      2. doi       -> DB lookup by DOI column
      3. title_hint -> exact normalized_title match, then trigram fallback
    """
    t0 = time.monotonic()
    strategy: str | None = None
    resolved_versioned_id: str | None = None

    try:
        # --- 1. Resolve by arXiv ID ----------------------------------------
        if parsed.arxiv_id is not None:
            strategy = "arxiv_id"
            base_id = normalize_arxiv_id(parsed.arxiv_id)

            paper = await get_paper(session, base_id)
            if paper is not None:
                # Full metadata available from DB
                if parsed.version is not None:
                    ver = await get_latest_version(session, base_id)
                    if ver is not None and parsed.version <= ver.version:
                        from ..db.crud import get_version
                        ver = await get_version(session, base_id, parsed.version)
                else:
                    ver = await get_latest_version(session, base_id)

                if ver is not None:
                    resolved_versioned_id = make_versioned_id(paper.id, ver.version)
                    return ResolveResponse(
                        state=ResolverState.RESOLVED,
                        result=_build_resolved_paper(paper, ver),
                    )

            # No DB metadata, but the arXiv ID is valid — resolve without metadata.
            # Default to version 1 if not specified.
            version = parsed.version or 1
            vid = make_versioned_id(base_id, version)
            resolved_versioned_id = vid
            return ResolveResponse(
                state=ResolverState.RESOLVED,
                result=ResolvedPaper(
                    versioned_id=vid,
                    arxiv_id=base_id,
                    version=version,
                    state=ResolverState.RESOLVED,
                ),
            )

        # --- 2. Resolve by DOI ---------------------------------------------
        if parsed.doi is not None:
            strategy = "doi"
            paper = await get_paper_by_doi(session, parsed.doi)
            if paper is None:
                return ResolveResponse(state=ResolverState.NOT_FOUND)

            ver = await get_latest_version(session, paper.id)
            if ver is None:
                return ResolveResponse(state=ResolverState.NOT_FOUND)

            resolved_versioned_id = make_versioned_id(paper.id, ver.version)
            return ResolveResponse(
                state=ResolverState.RESOLVED,
                result=_build_resolved_paper(paper, ver),
            )

        # --- 3. Resolve by title -------------------------------------------
        if parsed.title_hint is not None:
            normalized = _normalize_title_for_lookup(parsed.title_hint)

            # Exact match on normalized_title
            strategy = "title_exact"
            paper = await find_paper_by_normalized_title(session, normalized)
            if paper is not None:
                ver = await get_latest_version(session, paper.id)
                if ver is not None:
                    resolved_versioned_id = make_versioned_id(paper.id, ver.version)
                    return ResolveResponse(
                        state=ResolverState.RESOLVED,
                        result=_build_resolved_paper(paper, ver),
                    )

            # Trigram similarity fallback
            strategy = "title_trgm"
            candidates = await search_papers_by_title_trgm(
                session, parsed.title_hint, limit=5,
            )

            if not candidates:
                return ResolveResponse(state=ResolverState.NOT_FOUND)

            if len(candidates) == 1:
                paper = candidates[0]
                ver = await get_latest_version(session, paper.id)
                if ver is not None:
                    resolved_versioned_id = make_versioned_id(paper.id, ver.version)
                    return ResolveResponse(
                        state=ResolverState.RESOLVED,
                        result=_build_resolved_paper(paper, ver),
                    )
                return ResolveResponse(state=ResolverState.NOT_FOUND)

            # Multiple candidates -> AMBIGUOUS
            ambiguous_results: list[ResolvedPaper] = []
            for cand in candidates:
                ver = await get_latest_version(session, cand.id)
                if ver is not None:
                    ambiguous_results.append(
                        _build_resolved_paper(cand, ver, state=ResolverState.AMBIGUOUS),
                    )

            if not ambiguous_results:
                return ResolveResponse(state=ResolverState.NOT_FOUND)

            return ResolveResponse(
                state=ResolverState.AMBIGUOUS,
                candidates=ambiguous_results,
            )

        # Nothing to resolve
        return ResolveResponse(state=ResolverState.NOT_FOUND)

    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        await create_resolver_audit(
            session,
            input=parsed.raw_input,
            input_type=parsed.input_type,
            resolved_versioned_id=resolved_versioned_id,
            strategy=strategy,
            latency_ms=latency_ms,
        )
