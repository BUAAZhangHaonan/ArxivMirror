from __future__ import annotations

import logging
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)


class S3Mirror:
    def __init__(self) -> None:
        settings = get_settings()
        self.enabled: bool = settings.s3_mirror_enabled
        self.s5cmd_path: str = settings.s5cmd_path

    async def fetch_pdf(self, versioned_id: str, dest_path: Path) -> bool:
        """Try to fetch PDF from S3 arXiv bucket using s5cmd.

        Returns True if successful, False otherwise.
        If not enabled, return False.
        """
        if not self.enabled:
            return False

        logger.debug("S3 mirror fetch not yet implemented for %s", versioned_id)
        return False
