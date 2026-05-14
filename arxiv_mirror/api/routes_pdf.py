"""Routes for PDF download, asset lookup, parsed text retrieval, and health checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.crud import (
    count_pdf_assets,
    count_papers,
    create_pdf_asset,
    get_pdf_asset,
    get_parsed_text,
    update_pdf_asset,
)
from ..db.engine import get_session
from ..models.enums import DownloadStatus, ParseStatus, PdfSource, ResolverState
from ..models.schemas import (
    DownloadRequest,
    HealthResponse,
    ParsedTextResponse,
    PdfAssetResponse,
    ResolveResponse,
)
from ..resolver.normalizer import resolve as resolve_paper
from ..resolver.parser import parse_input

router = APIRouter(tags=["pdf"])


def _build_pdf_asset_response(asset) -> PdfAssetResponse:
    """Map a PdfAsset ORM object to the response schema.

    The ``source`` column stores both lifecycle states (pending, downloading,
    failed) and origin types (s3_mirror, remote, manual).  Only terminal
    origin values are valid PdfSource enum members; transient states get
    PdfSource.PENDING since the real source isn't known yet.
    """
    source = asset.source

    # Map source → download_status
    if source in ("pending", "downloading"):
        dl_status = DownloadStatus(source)
    elif source == "failed":
        dl_status = DownloadStatus.FAILED
    else:
        dl_status = DownloadStatus.COMPLETED

    # Map source → PdfSource enum (only terminal origins are valid)
    if source in ("pending", "downloading", "failed"):
        pdf_source = PdfSource.PENDING
    else:
        pdf_source = PdfSource(source)

    return PdfAssetResponse(
        versioned_id=asset.versioned_id,
        local_path=asset.local_path,
        sha256=asset.sha256,
        file_size=asset.file_size,
        source=pdf_source,
        download_status=dl_status,
        mineru_status=ParseStatus(asset.mineru_status),
    )


async def _resolve_and_get_or_create_asset(
    session: AsyncSession,
    query: str,
) -> tuple[ResolveResponse, PdfAssetResponse | None]:
    """Shared logic: parse + resolve + ensure pdf_asset exists.

    If an existing asset is stuck in ``failed`` state, reset it to
    ``pending`` so the download worker will retry.
    """
    parsed = parse_input(query)
    resolve_resp = await resolve_paper(session, parsed)

    if resolve_resp.state != ResolverState.RESOLVED or resolve_resp.result is None:
        return resolve_resp, None

    result = resolve_resp.result
    versioned_id = result.versioned_id
    arxiv_id = result.arxiv_id
    version = result.version

    asset = await get_pdf_asset(session, versioned_id)
    if asset is None:
        asset = await create_pdf_asset(
            session,
            versioned_id=versioned_id,
            arxiv_id=arxiv_id,
            version=version,
        )
    elif asset.source == "failed":
        await update_pdf_asset(session, asset.id, source="pending")
        await session.flush()
        await session.refresh(asset)

    asset_resp = _build_pdf_asset_response(asset)
    return resolve_resp, asset_resp


@router.post("/download", response_model=PdfAssetResponse)
async def download(
    req: DownloadRequest,
    session: AsyncSession = Depends(get_session),
):
    """Resolve input and create a pdf_asset if one does not already exist.

    Triggers the background download pipeline (workers pick up pending assets).
    """
    resolve_resp, asset_resp = await _resolve_and_get_or_create_asset(session, req.query)

    if resolve_resp.state == ResolverState.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Paper not found")

    if resolve_resp.state == ResolverState.AMBIGUOUS:
        raise HTTPException(
            status_code=300,
            detail={
                "message": "Ambiguous query – multiple candidates found",
                "candidates": [c.model_dump() for c in (resolve_resp.candidates or [])],
            },
        )

    if asset_resp is None:
        raise HTTPException(status_code=500, detail="Failed to create pdf asset")

    return asset_resp


@router.post("/resolve-and-download", response_model=PdfAssetResponse)
async def resolve_and_download(
    req: DownloadRequest,
    session: AsyncSession = Depends(get_session),
):
    """Resolve and download in one shot. Same logic as /download."""
    resolve_resp, asset_resp = await _resolve_and_get_or_create_asset(session, req.query)

    if resolve_resp.state == ResolverState.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Paper not found")

    if resolve_resp.state == ResolverState.AMBIGUOUS:
        raise HTTPException(
            status_code=300,
            detail={
                "message": "Ambiguous query – multiple candidates found",
                "candidates": [c.model_dump() for c in (resolve_resp.candidates or [])],
            },
        )

    if asset_resp is None:
        raise HTTPException(status_code=500, detail="Failed to create pdf asset")

    return asset_resp


@router.get("/asset/{versioned_id}", response_model=PdfAssetResponse)
async def get_asset(
    versioned_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get PDF asset info by versioned_id (e.g. ``2501.12345v2``)."""
    asset = await get_pdf_asset(session, versioned_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="PDF asset not found")
    return _build_pdf_asset_response(asset)


@router.get("/asset/{versioned_id}/parsed", response_model=ParsedTextResponse)
async def get_parsed(
    versioned_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get parsed text for a PDF by versioned_id."""
    parsed_row = await get_parsed_text(session, versioned_id)
    if parsed_row is None:
        raise HTTPException(status_code=404, detail="Parsed text not found")

    return ParsedTextResponse(
        versioned_id=parsed_row.versioned_id,
        full_text=parsed_row.full_text,
        sections=parsed_row.sections,
        parse_status=ParseStatus(parsed_row.parse_status),
    )


@router.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)):
    """Health check: counts papers and PDF assets, confirms DB connectivity."""
    try:
        paper_count = await count_papers(session)
        pdf_count = await count_pdf_assets(session)
        return HealthResponse(
            status="ok",
            paper_count=paper_count,
            pdf_count=pdf_count,
            db_connected=True,
        )
    except Exception:
        return HealthResponse(
            status="degraded",
            paper_count=0,
            pdf_count=0,
            db_connected=False,
        )
