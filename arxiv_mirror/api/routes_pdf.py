"""Routes for synchronous PDF download, asset lookup, and service health."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.crud import count_papers, count_pdf_assets, get_pdf_asset
from ..db.engine import get_session
from ..models.enums import DownloadStatus, ResolverState
from ..models.schemas import (
    DownloadRequest,
    HealthResponse,
    PdfAssetResponse,
    ResolvedPaper,
)
from ..pdf import PdfAssetStateError, PdfDownloadError, PdfDownloadService
from ..resolver.normalizer import resolve as resolve_paper
from ..resolver.parser import parse_input

router = APIRouter(tags=["pdf"])

Session = Annotated[AsyncSession, Depends(get_session)]


def get_pdf_download_service(request: Request) -> PdfDownloadService:
    return request.app.state.pdf_download_service


DownloadService = Annotated[
    PdfDownloadService,
    Depends(get_pdf_download_service),
]


def _download_status(source: str) -> DownloadStatus:
    if source in {"pending", "downloading", "failed"}:
        return DownloadStatus(source)
    if source == "remote":
        return DownloadStatus.COMPLETED
    raise ValueError(f"Unsupported PDF asset state: {source}")


def _build_pdf_asset_response(asset) -> PdfAssetResponse:
    return PdfAssetResponse(
        versioned_id=asset.versioned_id,
        local_path=asset.local_path,
        file_size=asset.file_size,
        download_status=_download_status(asset.source),
    )


async def _resolve_paper_or_error(
    session: AsyncSession,
    query: str,
) -> ResolvedPaper:
    response = await resolve_paper(session, parse_input(query))
    if response.state == ResolverState.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Paper not found")
    if response.state == ResolverState.AMBIGUOUS:
        raise HTTPException(
            status_code=300,
            detail={
                "message": "Multiple papers match this query",
                "candidates": [
                    candidate.model_dump() for candidate in (response.candidates or [])
                ],
            },
        )
    if response.result is None:
        raise HTTPException(status_code=500, detail="Resolved paper is missing")
    return response.result


@router.post("/resolve-and-download", response_model=PdfAssetResponse)
async def resolve_and_download(
    req: DownloadRequest,
    session: Session,
    service: DownloadService,
):
    paper = await _resolve_paper_or_error(session, req.query)
    try:
        asset = await service.fetch(paper)
    except PdfAssetStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PdfDownloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _build_pdf_asset_response(asset)


@router.get("/asset/{versioned_id}", response_model=PdfAssetResponse)
async def get_asset(versioned_id: str, session: Session):
    asset = await get_pdf_asset(session, versioned_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="PDF asset not found")
    return _build_pdf_asset_response(asset)


@router.get("/health", response_model=HealthResponse)
async def health(session: Session):
    try:
        return HealthResponse(
            status="ok",
            paper_count=await count_papers(session),
            pdf_count=await count_pdf_assets(session),
            db_connected=True,
        )
    except SQLAlchemyError:
        return HealthResponse(status="degraded")
