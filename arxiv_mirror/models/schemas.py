from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from .enums import DownloadStatus, ResolverState


class ParsedInput(BaseModel):
    raw_input: str
    arxiv_id: str | None = None
    version: int | None = None
    doi: str | None = None
    title_hint: str | None = None
    input_type: str | None = None  # url/id/doi/title


class ResolvedPaper(BaseModel):
    versioned_id: str
    arxiv_id: str
    version: int
    state: ResolverState
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    categories: list[str] = Field(default_factory=list)
    doi: str | None = None
    created_date: date | None = None


class ResolveRequest(BaseModel):
    query: str


class ResolveResponse(BaseModel):
    state: ResolverState
    result: ResolvedPaper | None = None
    candidates: list[ResolvedPaper] | None = None


class DownloadRequest(BaseModel):
    query: str


class BatchResolveRequest(BaseModel):
    queries: list[str]


class PdfAssetResponse(BaseModel):
    versioned_id: str
    local_path: str | None = None
    file_size: int | None = None
    download_status: DownloadStatus = DownloadStatus.PENDING


class HealthResponse(BaseModel):
    status: str = "ok"
    paper_count: int = 0
    pdf_count: int = 0
    db_connected: bool = False
