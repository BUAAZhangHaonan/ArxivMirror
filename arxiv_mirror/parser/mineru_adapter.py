"""MinerU CLI adapter for PDF-to-text parsing."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    success: bool
    full_text: str | None = None
    sections: list[dict] | None = None
    error: str | None = None


class MineruAdapter:
    """Wraps the magic-pdf CLI for PDF parsing."""

    def __init__(self, binary: str = "magic-pdf", timeout: float = 300.0):
        self.binary = binary
        self.timeout = timeout
        self._available = shutil.which(binary) is not None

    @property
    def available(self) -> bool:
        return self._available

    async def parse(self, pdf_path: Path) -> ParseResult:
        if not self._available:
            return ParseResult(success=False, error="MinerU (magic-pdf) not installed")

        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "-p", str(pdf_path),
                "-o", str(pdf_path.parent / "mineru_output"),
                "-m", "auto",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            if proc.returncode != 0:
                return ParseResult(
                    success=False,
                    error=f"magic-pdf exited {proc.returncode}: {stderr.decode()[:500]}",
                )

            # Read the output markdown file
            output_dir = pdf_path.parent / "mineru_output" / "auto"
            md_files = list(output_dir.glob("*.md"))
            if not md_files:
                return ParseResult(success=False, error="No markdown output from MinerU")

            text = md_files[0].read_text(encoding="utf-8")
            return ParseResult(success=True, full_text=text, sections=[])

        except asyncio.TimeoutError:
            return ParseResult(success=False, error=f"MinerU timed out after {self.timeout}s")
        except Exception as e:
            return ParseResult(success=False, error=str(e))
