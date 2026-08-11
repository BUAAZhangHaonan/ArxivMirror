from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


class PdfDownloadError(RuntimeError):
    """Raised when one explicit arXiv PDF download cannot complete."""


@dataclass(frozen=True)
class DownloadResult:
    local_path: Path
    file_size: int


class PdfDownloader:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._last_request_time = 0.0
        self._rate_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._settings.pdf_download_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout, trust_env=False)
        return self._session

    async def _wait_for_request_slot(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            wait_time = max(
                0.0,
                self._last_request_time
                + self._settings.arxiv_download_delay_seconds
                - now,
            )
            if wait_time:
                await asyncio.sleep(wait_time)
            self._last_request_time = time.monotonic()

    async def download(self, versioned_id: str, destination: Path) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.parent.chmod(0o700)
        temporary = destination.with_suffix(".pdf.tmp")
        if destination.exists() or temporary.exists():
            raise PdfDownloadError(f"PDF path is not empty for {versioned_id}")

        await self._wait_for_request_slot()
        proxy = self._settings.https_proxy or self._settings.http_proxy or None
        url = f"https://arxiv.org/pdf/{versioned_id}.pdf"

        try:
            session = self._get_session()
            async with session.get(url, proxy=proxy) as response:
                if response.status != 200:
                    raise PdfDownloadError(
                        f"arXiv returned HTTP {response.status} for {versioned_id}"
                    )
                if (
                    response.content_length is not None
                    and response.content_length > self._settings.pdf_max_file_size
                ):
                    raise PdfDownloadError(f"PDF is too large for {versioned_id}")

                file_size = await self._write_response(response, temporary)

            temporary.replace(destination)
            destination.chmod(0o600)
            return DownloadResult(local_path=destination, file_size=file_size)
        except asyncio.CancelledError:
            self._remove_created_files(temporary, destination)
            raise
        except PdfDownloadError:
            self._remove_created_files(temporary, destination)
            raise
        except Exception as exc:
            self._remove_created_files(temporary, destination)
            logger.warning("PDF download failed for %s: %s", versioned_id, exc)
            raise PdfDownloadError(f"PDF download failed for {versioned_id}") from exc

    async def _write_response(
        self,
        response: aiohttp.ClientResponse,
        temporary: Path,
    ) -> int:
        file_size = 0
        prefix = bytearray()
        with temporary.open("xb") as output:
            temporary.chmod(0o600)
            async for chunk in response.content.iter_chunked(64 * 1024):
                if not chunk:
                    continue
                file_size += len(chunk)
                if file_size > self._settings.pdf_max_file_size:
                    raise PdfDownloadError("PDF exceeded the configured size limit")
                if len(prefix) < 5:
                    prefix.extend(chunk[: 5 - len(prefix)])
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())

        if file_size == 0 or bytes(prefix) != b"%PDF-":
            raise PdfDownloadError("arXiv response is not a PDF")
        return file_size

    @staticmethod
    def _remove_created_files(temporary: Path, destination: Path) -> None:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
