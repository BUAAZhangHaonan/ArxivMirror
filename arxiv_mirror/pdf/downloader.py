from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    success: bool
    local_path: Path | None = None
    sha256: str | None = None
    file_size: int | None = None
    error: str | None = None


class PdfDownloader:
    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            settings = get_settings()
            timeout = aiohttp.ClientTimeout(total=settings.pdf_download_timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                trust_env=False,
            )
        return self._session

    async def _enforce_rate_limit(self) -> None:
        settings = get_settings()
        delay = settings.arxiv_download_delay_seconds
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request_time = time.monotonic()

    async def download(self, versioned_id: str, dest_path: Path) -> DownloadResult:
        settings = get_settings()
        url = f"https://arxiv.org/pdf/{versioned_id}.pdf"

        last_error: str | None = None
        for attempt in range(1, settings.pdf_download_max_retries + 1):
            try:
                await self._enforce_rate_limit()
                result = await self._do_download(url, dest_path, settings)
                if result.success:
                    return result
                last_error = result.error
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Download attempt %d/%d failed for %s: %s",
                    attempt,
                    settings.pdf_download_max_retries,
                    versioned_id,
                    exc,
                )

            if attempt < settings.pdf_download_max_retries:
                backoff = min(2**attempt, 30)
                await asyncio.sleep(backoff)

        return DownloadResult(success=False, error=last_error)

    async def _do_download(
        self, url: str, dest_path: Path, settings
    ) -> DownloadResult:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        proxy = settings.https_proxy or settings.http_proxy or None

        session = self._get_session()
        async with session.get(url, proxy=proxy) as resp:
            if resp.status != 200:
                text = await resp.text()
                return DownloadResult(
                    success=False,
                    error=f"HTTP {resp.status}: {text[:500]}",
                )

            content_length = resp.content_length
            if content_length is not None and content_length > settings.pdf_max_file_size:
                return DownloadResult(
                    success=False,
                    error=f"Content-Length {content_length} exceeds max {settings.pdf_max_file_size}",
                )

            sha256 = hashlib.sha256()
            total_bytes = 0
            tmp_path = dest_path.with_suffix(".pdf.tmp")

            try:
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > settings.pdf_max_file_size:
                            return DownloadResult(
                                success=False,
                                error=f"File exceeded max size {settings.pdf_max_file_size} during download",
                            )
                        sha256.update(chunk)
                        f.write(chunk)

                tmp_path.replace(dest_path)
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

            return DownloadResult(
                success=True,
                local_path=dest_path,
                sha256=sha256.hexdigest(),
                file_size=total_bytes,
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
