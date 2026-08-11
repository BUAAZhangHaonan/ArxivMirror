from __future__ import annotations

from arxiv_mirror.api.app import create_app
from arxiv_mirror.models.db import Base, PdfAsset, SyncState
from arxiv_mirror.models.schemas import PdfAssetResponse


def test_openapi_exposes_only_synchronous_single_download() -> None:
    paths = create_app().openapi()["paths"]

    assert "/api/v1/resolve-and-download" in paths
    assert "/api/v1/download" not in paths
    assert "/api/v1/batch/download" not in paths
    assert not any(path.startswith("/api/v1/job/") for path in paths)


def test_pdf_response_contains_only_active_contract_fields() -> None:
    assert set(PdfAssetResponse.model_fields) == {
        "versioned_id",
        "local_path",
        "file_size",
        "download_status",
    }


def test_fresh_schema_excludes_removed_download_state() -> None:
    assert "batch_jobs" not in Base.metadata.tables
    assert "batch_items" not in Base.metadata.tables
    assert set(PdfAsset.__table__.columns.keys()) == {
        "id",
        "versioned_id",
        "arxiv_id",
        "version",
        "source",
        "local_path",
        "file_size",
        "fetched_at",
        "created_at",
        "updated_at",
    }
    assert set(SyncState.__table__.columns.keys()) == {
        "name",
        "last_response_date",
        "last_success_at",
        "status",
        "records_harvested",
        "error_message",
        "updated_at",
    }
