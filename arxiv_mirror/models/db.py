from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ArxivPaper(Base):
    __tablename__ = "arxiv_papers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    normalized_title: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    abstract: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    primary_category: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    license: Mapped[str | None] = mapped_column(Text)
    comments: Mapped[str | None] = mapped_column(Text)
    journal_ref: Mapped[str | None] = mapped_column(Text)
    first_version_date: Mapped[str | None] = mapped_column(Date)
    latest_version_date: Mapped[str | None] = mapped_column(Date)
    oai_datestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text, nullable=False, default="oaipmh")
    raw_metadata: Mapped[dict | None] = mapped_column(JSON)
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    versions: Mapped[list[PaperVersion]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    pdf_assets: Mapped[list[PdfAsset]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )


class PaperVersion(Base):
    __tablename__ = "paper_versions"

    base_id: Mapped[str] = mapped_column(
        Text, ForeignKey("arxiv_papers.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    versioned_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_date: Mapped[str | None] = mapped_column(Date)
    is_withdrawn: Mapped[bool | None] = mapped_column(Boolean, default=False)
    title_snapshot: Mapped[str | None] = mapped_column(Text)
    pdf_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")

    paper: Mapped[ArxivPaper] = relationship(back_populates="versions")


class PdfAsset(Base):
    __tablename__ = "pdf_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    versioned_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    arxiv_id: Mapped[str] = mapped_column(
        Text, ForeignKey("arxiv_papers.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    local_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mineru_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    paper: Mapped[ArxivPaper] = relationship(back_populates="pdf_assets")
    parsed_text: Mapped[ParsedText | None] = relationship(
        back_populates="pdf_asset", uselist=False, cascade="all, delete-orphan"
    )


class ParsedText(Base):
    __tablename__ = "parsed_texts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pdf_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pdf_assets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    versioned_id: Mapped[str] = mapped_column(Text, nullable=False)
    full_text: Mapped[str | None] = mapped_column(Text)
    sections: Mapped[list | None] = mapped_column(JSON)
    parse_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending"
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    pdf_asset: Mapped[PdfAsset] = relationship(back_populates="parsed_text")


class SyncState(Base):
    __tablename__ = "sync_state"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_response_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_manifest_md5: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="idle")
    records_harvested: Mapped[int | None] = mapped_column(BigInteger, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ResolverAudit(Base):
    __tablename__ = "resolver_audit"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    input: Mapped[str | None] = mapped_column(Text)
    input_type: Mapped[str | None] = mapped_column(Text)
    resolved_versioned_id: Mapped[str | None] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    total_requested: Mapped[int] = mapped_column(Integer, default=0)
    total_completed: Mapped[int] = mapped_column(Integer, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, default=0)
    total_deduplicated: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    items: Mapped[list[BatchItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class BatchItem(Base):
    __tablename__ = "batch_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("batch_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    versioned_id: Mapped[str] = mapped_column(Text, nullable=False)
    arxiv_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    pdf_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pdf_assets.id")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    batch: Mapped[BatchJob] = relationship(back_populates="items")

    __table_args__ = (UniqueConstraint("batch_id", "versioned_id"),)
