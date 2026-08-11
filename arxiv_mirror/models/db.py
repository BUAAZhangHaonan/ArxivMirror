from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
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
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    versions: Mapped[list[PaperVersion]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    pdf_assets: Mapped[list[PdfAsset]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "idx_papers_doi",
            "doi",
            unique=True,
            postgresql_where=text("doi IS NOT NULL"),
        ),
        Index("idx_papers_normalized_title", "normalized_title"),
        Index(
            "idx_papers_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index("idx_papers_categories", "categories", postgresql_using="gin"),
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
        Text,
        ForeignKey("arxiv_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    local_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    paper: Mapped[ArxivPaper | None] = relationship(back_populates="pdf_assets")

    __table_args__ = (Index("idx_pdf_arxiv_id", "arxiv_id"),)


class SyncState(Base):
    __tablename__ = "sync_state"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_response_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="idle")
    records_harvested: Mapped[int | None] = mapped_column(BigInteger, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
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
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
