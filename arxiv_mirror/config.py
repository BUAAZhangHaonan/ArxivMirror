import os
from pathlib import Path

from pydantic_settings import BaseSettings


def _default_data_dir() -> Path:
    """Default data directory: ./data relative to the project root."""
    return Path(os.getenv("ARXIV_MIRROR_ROOT", ".")).resolve() / "data"


class Settings(BaseSettings):
    data_dir: Path = _default_data_dir()
    pdf_storage_dir: Path | None = None

    database_url: str = "postgresql+asyncpg://arxiv_mirror:arxiv_mirror@localhost:5432/arxiv_mirror"

    api_host: str = "127.0.0.1"
    api_port: int = 8900

    oaipmh_base_url: str = "https://oaipmh.arxiv.org/oai"
    oaipmh_metadata_prefix: str = "arXivRaw"
    oaipmh_polite_delay_seconds: float = 3.0
    oaipmh_page_size: int = 1000
    oaipmh_request_timeout: float = 60.0

    pdf_download_concurrency: int = 8
    pdf_download_timeout: float = 120.0
    pdf_download_max_retries: int = 3
    arxiv_download_delay_seconds: float = 3.0
    pdf_max_file_size: int = 100 * 1024 * 1024

    s3_mirror_enabled: bool = False
    s3_bucket: str = "arxiv"
    s3_region: str = "us-east-1"
    s5cmd_path: str = "s5cmd"

    mineru_enabled: bool = False
    mineru_concurrency: int = 2
    mineru_timeout: float = 300.0
    mineru_binary: str = "magic-pdf"

    http_proxy: str = ""
    https_proxy: str = ""

    model_config = {"env_prefix": "ARXIV_MIRROR_", "env_file": ".env"}

    def model_post_init(self, __context):
        if self.pdf_storage_dir is None:
            object.__setattr__(self, "pdf_storage_dir", self.data_dir / "pdfs")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
