from __future__ import annotations

import re
from pathlib import Path

from ..config import get_settings

_VERSIONED_ID = re.compile(r"^(?P<year>\d{2})(?P<month>\d{2})\.\d{4,5}v[1-9]\d*$")


class PdfStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        configured = base_dir or get_settings().pdf_storage_dir
        if configured is None:
            raise ValueError("PDF storage directory is required")
        self.base_dir = Path(configured).resolve()

    def get_path(self, versioned_id: str) -> Path:
        match = _VERSIONED_ID.fullmatch(versioned_id)
        if match is None or not 1 <= int(match.group("month")) <= 12:
            raise ValueError(f"Invalid versioned arXiv ID: {versioned_id}")

        path = (
            self.base_dir
            / match.group("year")
            / match.group("month")
            / f"{versioned_id}.pdf"
        ).resolve()
        if not path.is_relative_to(self.base_dir):
            raise ValueError("PDF path escapes the storage directory")
        return path

    def validate_ready(
        self,
        versioned_id: str,
        local_path: str | None,
        file_size: int | None,
    ) -> Path:
        expected = self.get_path(versioned_id)
        if local_path != str(expected):
            raise ValueError(f"Stored PDF path is invalid for {versioned_id}")
        if file_size is None or file_size <= 0:
            raise ValueError(f"Stored PDF size is invalid for {versioned_id}")
        if not expected.is_file() or expected.stat().st_size != file_size:
            raise ValueError(f"Stored PDF file is invalid for {versioned_id}")
        return expected
