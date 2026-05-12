from __future__ import annotations

import asyncio
import hashlib
import logging
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import aiobotocore.session

from ..config import get_settings

logger = logging.getLogger(__name__)

TAR_KEY_PREFIX = "pdf/"


@dataclass
class S3FetchResult:
    success: bool
    local_path: Path | None = None
    sha256: str | None = None
    file_size: int | None = None
    error: str | None = None


class S3Mirror:
    """Fetch PDFs from the arXiv S3 requester-pays bucket.

    Strategy:
    1. Download and cache the manifest (arXiv_pdf_manifest.xml)
    2. Look up arxiv_id → tar file mapping
    3. Download the tar to local cache (only once per tar)
    4. Extract the individual PDF from the cached tar
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.enabled: bool = settings.s3_mirror_enabled
        self.bucket: str = settings.s3_bucket
        self.region: str = settings.s3_region
        self.data_dir = settings.data_dir
        self.manifest_path = self.data_dir / "s3_manifest.xml"
        self.tar_cache_dir = self.data_dir / "s3_tar_cache"
        self._manifest: dict[str, dict] | None = None
        self._client = None
        self._tar_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_client(self):
        if self._client is None:
            session = aiobotocore.session.get_session()
            self._client = session.create_client("s3", region_name=self.region)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def fetch_pdf(self, versioned_id: str, dest_path: Path) -> S3FetchResult:
        """Try to fetch a PDF from the S3 arXiv bucket."""
        if not self.enabled:
            return S3FetchResult(success=False, error="S3 mirror disabled")

        base_id = versioned_id.rsplit("v", 1)[0] if "v" in versioned_id else versioned_id

        tar_info = await self._lookup_manifest(base_id)
        if tar_info is None:
            return S3FetchResult(success=False, error=f"arxiv_id {base_id} not in manifest")

        tar_path = await self._ensure_tar(tar_info)
        if tar_path is None:
            return S3FetchResult(success=False, error=f"failed to download tar {tar_info['filename']}")

        return await asyncio.to_thread(
            self._extract_pdf, tar_path, versioned_id, base_id, dest_path
        )

    # ── manifest ──────────────────────────────────────────────

    async def _lookup_manifest(self, arxiv_id: str) -> dict | None:
        if self._manifest is None:
            await self._load_manifest()

        yymm = arxiv_id[:4]
        for info in self._manifest.values():
            if info["yymm"] == yymm and info["first_item"] <= arxiv_id <= info["last_item"]:
                return info
        return None

    async def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            await self._download_manifest()
        self._manifest = _parse_manifest(self.manifest_path.read_bytes())

    async def _download_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        client = await self._get_client()
        resp = await client.get_object(
            Bucket=self.bucket,
            Key=f"{TAR_KEY_PREFIX}arXiv_pdf_manifest.xml",
            RequestPayer="requester",
        )
        data = await resp["Body"].read()
        self.manifest_path.write_bytes(data)
        logger.info("Downloaded manifest (%d bytes)", len(data))

    # ── tar download ──────────────────────────────────────────

    async def _ensure_tar(self, tar_info: dict) -> Path | None:
        filename = tar_info["filename"]
        tar_path = self.tar_cache_dir / filename

        if tar_path.exists():
            return tar_path

        async with self._global_lock:
            if filename not in self._tar_locks:
                self._tar_locks[filename] = asyncio.Lock()

        async with self._tar_locks[filename]:
            if tar_path.exists():
                return tar_path
            return await self._download_tar(tar_info)

    async def _download_tar(self, tar_info: dict) -> Path | None:
        s3_key = f"{TAR_KEY_PREFIX}{tar_info['filename']}"
        tar_path = self.tar_cache_dir / tar_info["filename"]
        tmp_path = tar_path.with_suffix(".tar.tmp")
        self.tar_cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            client = await self._get_client()
            resp = await client.get_object(
                Bucket=self.bucket,
                Key=s3_key,
                RequestPayer="requester",
            )

            with open(tmp_path, "wb") as f:
                async for chunk in resp["Body"].iter_chunks(1024 * 1024):
                    f.write(chunk)

            tmp_path.rename(tar_path)
            logger.info("Downloaded tar %s", tar_info["filename"])
            return tar_path
        except Exception:
            logger.exception("Failed to download tar %s", tar_info["filename"])
            if tmp_path.exists():
                tmp_path.unlink()
            return None

    # ── extraction ────────────────────────────────────────────

    @staticmethod
    def _extract_pdf(
        tar_path: Path, versioned_id: str, base_id: str, dest_path: Path
    ) -> S3FetchResult:
        try:
            with tarfile.open(tar_path, "r:") as tf:
                candidates = [f"{versioned_id}.pdf", f"{base_id}.pdf"]
                member = None
                for name in candidates:
                    try:
                        member = tf.getmember(name)
                        break
                    except KeyError:
                        continue

                if member is None:
                    return S3FetchResult(
                        success=False,
                        error=f"PDF not found in tar: tried {candidates}",
                    )

                src = tf.extractfile(member)
                if src is None:
                    return S3FetchResult(success=False, error="tar member has no data")

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                sha256 = hashlib.sha256()
                file_size = 0

                with src, open(dest_path, "wb") as out:
                    while True:
                        chunk = src.read(256 * 1024)
                        if not chunk:
                            break
                        sha256.update(chunk)
                        file_size += len(chunk)
                        out.write(chunk)

                return S3FetchResult(
                    success=True,
                    local_path=dest_path,
                    sha256=sha256.hexdigest(),
                    file_size=file_size,
                )
        except Exception as exc:
            logger.exception("Extract failed for %s from %s", versioned_id, tar_path)
            return S3FetchResult(success=False, error=str(exc))


def _parse_manifest(xml_content: bytes) -> dict[str, dict]:
    root = ET.fromstring(xml_content)
    manifest: dict[str, dict] = {}

    for file_elem in root.iter("file"):
        first = _text(file_elem, "first_item")
        last = _text(file_elem, "last_item")
        filename = _text(file_elem, "filename")
        yymm = _text(file_elem, "yymm")
        num_items = int(_text(file_elem, "num_items", "0"))

        if not all([first, last, filename, yymm]):
            continue

        manifest[filename] = {
            "filename": filename,
            "yymm": yymm,
            "first_item": first,
            "last_item": last,
            "num_items": num_items,
        }

    return manifest


def _text(elem: ET.Element, tag: str, default: str = "") -> str:
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else default
