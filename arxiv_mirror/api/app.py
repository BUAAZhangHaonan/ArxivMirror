"""FastAPI application factory for the ArxivMirror service."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="ArxivMirror", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routes_resolve import router as resolve_router
    from .routes_pdf import router as pdf_router
    from .routes_batch import router as batch_router

    app.include_router(resolve_router, prefix="/api/v1")
    app.include_router(pdf_router, prefix="/api/v1")
    app.include_router(batch_router, prefix="/api/v1")

    @app.on_event("shutdown")
    async def shutdown():
        from ..db.engine import close_engine

        await close_engine()

    return app
