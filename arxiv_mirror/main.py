from __future__ import annotations

import asyncio
import sys


def run_api():
    import uvicorn

    from .config import get_settings

    settings = get_settings()
    uvicorn.run(
        "arxiv_mirror.api.app:create_app",
        host=settings.api_host,
        port=settings.api_port,
        factory=True,
        reload=False,
        loop="uvloop",
        http="httptools",
    )


def run_harvest():
    asyncio.run(_run_harvest())


async def _run_harvest():
    from .db.engine import get_engine
    from .metadata.harvester import run_harvest as _harvest

    engine = get_engine()
    try:
        await _harvest(engine)
    finally:
        from .db.engine import close_engine
        await close_engine()


def run_download_worker():
    asyncio.run(_run_download_worker())


async def _run_download_worker():
    from .db.engine import get_engine
    from .pdf.worker import run_worker as _worker

    engine = get_engine()
    try:
        await _worker(engine)
    finally:
        from .db.engine import close_engine
        await close_engine()


def run_parser_worker():
    asyncio.run(_run_parser_worker())


async def _run_parser_worker():
    from .db.engine import get_engine
    from .parser.worker import run_worker as _worker

    engine = get_engine()
    try:
        await _worker(engine)
    finally:
        from .db.engine import close_engine
        await close_engine()
