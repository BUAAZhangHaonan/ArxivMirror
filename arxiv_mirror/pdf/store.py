from __future__ import annotations

from pathlib import Path

from ..config import get_settings


class PdfStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            base_dir = get_settings().pdf_storage_dir
        self.base_dir = Path(base_dir)

    def get_path(self, versioned_id: str) -> Path:
        """Return storage path: base_dir/YY/MM/versioned_id.pdf

        For versioned_id like '2501.12345v2', YY=25, MM=01.
        """
        prefix = versioned_id[:4]
        yy = prefix[:2]
        mm = prefix[2:4]
        return self.base_dir / yy / mm / f"{versioned_id}.pdf"

    async def save(self, versioned_id: str, content: bytes) -> Path:
        """Save PDF content to disk. Create dirs as needed."""
        path = self.get_path(versioned_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def exists(self, versioned_id: str) -> bool:
        """Check if PDF exists locally."""
        return self.get_path(versioned_id).exists()
