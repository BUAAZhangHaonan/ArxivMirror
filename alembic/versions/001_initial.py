"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    op.create_table(
        "arxiv_papers",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("normalized_title", sa.Text),
        sa.Column("authors", ARRAY(sa.Text)),
        sa.Column("abstract", sa.Text),
        sa.Column("categories", ARRAY(sa.Text)),
        sa.Column("primary_category", sa.Text),
        sa.Column("doi", sa.Text),
        sa.Column("latest_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("license", sa.Text),
        sa.Column("comments", sa.Text),
        sa.Column("journal_ref", sa.Text),
        sa.Column("first_version_date", sa.Date),
        sa.Column("latest_version_date", sa.Date),
        sa.Column("oai_datestamp", sa.DateTime(timezone=True)),
        sa.Column("source", sa.Text, nullable=False, server_default="oaipmh"),
        sa.Column("raw_metadata", JSON),
        sa.Column("inserted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_papers_doi", "arxiv_papers", ["doi"], unique=True, postgresql_where=sa.text("doi IS NOT NULL"))
    op.create_index("idx_papers_normalized_title", "arxiv_papers", ["normalized_title"])
    op.execute("CREATE INDEX idx_papers_title_trgm ON arxiv_papers USING GIN (title gin_trgm_ops)")
    op.execute("CREATE INDEX idx_papers_categories ON arxiv_papers USING GIN (categories)")

    op.create_table(
        "paper_versions",
        sa.Column("base_id", sa.Text, sa.ForeignKey("arxiv_papers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("versioned_id", sa.Text, nullable=False),
        sa.Column("version_date", sa.Date),
        sa.Column("is_withdrawn", sa.Boolean, server_default=sa.text("FALSE")),
        sa.Column("title_snapshot", sa.Text),
        sa.Column("pdf_status", sa.Text, nullable=False, server_default="pending"),
    )

    op.create_table(
        "pdf_assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("versioned_id", sa.Text, nullable=False, unique=True),
        sa.Column("arxiv_id", sa.Text, sa.ForeignKey("arxiv_papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("source", sa.Text, nullable=False, server_default="pending"),
        sa.Column("local_path", sa.Text),
        sa.Column("sha256", sa.Text),
        sa.Column("file_size", sa.BigInteger),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("mineru_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_pdf_sha256", "pdf_assets", ["sha256"], postgresql_where=sa.text("sha256 IS NOT NULL"))
    op.create_index("idx_pdf_arxiv_id", "pdf_assets", ["arxiv_id"])

    op.create_table(
        "parsed_texts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("pdf_asset_id", UUID(as_uuid=True), sa.ForeignKey("pdf_assets.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("versioned_id", sa.Text, nullable=False),
        sa.Column("full_text", sa.Text),
        sa.Column("sections", JSON),
        sa.Column("parse_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "sync_state",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("last_response_date", sa.DateTime(timezone=True)),
        sa.Column("last_manifest_md5", sa.Text),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="idle"),
        sa.Column("records_harvested", sa.BigInteger, server_default="0"),
        sa.Column("error_message", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "resolver_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("input", sa.Text),
        sa.Column("input_type", sa.Text),
        sa.Column("resolved_versioned_id", sa.Text),
        sa.Column("strategy", sa.Text),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "batch_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("total_requested", sa.Integer, server_default="0"),
        sa.Column("total_completed", sa.Integer, server_default="0"),
        sa.Column("total_failed", sa.Integer, server_default="0"),
        sa.Column("total_deduplicated", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "batch_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("batch_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("versioned_id", sa.Text, nullable=False),
        sa.Column("arxiv_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("pdf_asset_id", UUID(as_uuid=True), sa.ForeignKey("pdf_assets.id")),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("batch_id", "versioned_id"),
    )

    # set_updated_at function (must come before triggers)
    op.execute(
        "CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$ "
        "BEGIN NEW.updated_at = NOW(); RETURN NEW; END; "
        "$$ LANGUAGE plpgsql"
    )

    # updated_at auto-update triggers
    for table in ("arxiv_papers", "pdf_assets", "parsed_texts", "sync_state", "batch_jobs"):
        op.execute(
            f"CREATE TRIGGER set_updated_at_{table} "
            f"BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in ("batch_items", "batch_jobs", "resolver_audit", "sync_state",
                  "parsed_texts", "pdf_assets", "paper_versions", "arxiv_papers"):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS \"uuid-ossp\"")
