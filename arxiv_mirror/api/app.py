"""FastAPI application factory for the ArxivMirror service."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..db.engine import close_engine, get_session_factory
from ..pdf import PdfDownloadService
from ..pdf.downloader import PdfDownloader
from ..pdf.repository import PdfAssetRepository
from ..pdf.store import PdfStore


def create_app() -> FastAPI:
    service = PdfDownloadService(
        repository=PdfAssetRepository(get_session_factory()),
        downloader=PdfDownloader(),
        store=PdfStore(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.pdf_download_service.close()
        await close_engine()

    app = FastAPI(title="ArxivMirror", version="0.1.0", lifespan=lifespan)
    app.state.pdf_download_service = service

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routes_batch import router as batch_router
    from .routes_pdf import router as pdf_router
    from .routes_resolve import router as resolve_router

    app.include_router(resolve_router, prefix="/api/v1")
    app.include_router(pdf_router, prefix="/api/v1")
    app.include_router(batch_router, prefix="/api/v1")
    return app
