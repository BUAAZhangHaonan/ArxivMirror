from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from .enums import DownloadStatus, ParseStatus, PdfSource, ResolverState


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


class BatchDownloadRequest(BaseModel):
    queries: list[str]
    max_concurrent: int = 5


class BatchDownloadResponse(BaseModel):
    batch_id: uuid.UUID
    total_requested: int
    total_deduplicated: int
    status: str


class BatchStatusResponse(BaseModel):
    batch_id: uuid.UUID
    status: str
    total_requested: int
    total_completed: int
    total_failed: int
    total_deduplicated: int
    items: list[dict] = Field(default_factory=list)


class PdfAssetResponse(BaseModel):
    versioned_id: str
    local_path: str | None = None
    sha256: str | None = None
    file_size: int | None = None
    source: PdfSource = PdfSource.PENDING
    download_status: DownloadStatus = DownloadStatus.PENDING
    mineru_status: ParseStatus = ParseStatus.PENDING


class ParsedTextResponse(BaseModel):
    versioned_id: str
    full_text: str | None = None
    sections: list[dict] | None = None
    parse_status: ParseStatus = ParseStatus.PENDING


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    total: int
    completed: int
    failed: int
    items: list[dict] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    paper_count: int = 0
    pdf_count: int = 0
    db_connected: bool = False
