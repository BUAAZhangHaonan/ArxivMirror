"""Pressure test for PaperMirror: 30k resolve + 300 download + bandwidth.

This script:
1. Seeds the database with test papers (30k+ records)
2. Starts the API server
3. Runs resolve benchmarks
4. Simulates 300 concurrent download claims
5. Reports results
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from arxiv_mirror.config import get_settings

NUM_PAPERS = 30_000
NUM_DOWNLOAD_SIM = 300


async def seed_data(engine):
    """Bulk-insert test papers and versions using raw SQL for speed."""
    async with engine.begin() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM arxiv_papers"))).scalar()
        if count >= NUM_PAPERS:
            print(f"  Already have {count} papers, skipping seed.")
            return

        print(f"  Seeding {NUM_PAPERS} papers...")
        t0 = time.monotonic()

        batch_size = 5000
        inserted = 0

        for batch_start in range(0, NUM_PAPERS, batch_size):
            batch_end = min(batch_start + batch_size, NUM_PAPERS)
            paper_values = []
            version_values = []

            for i in range(batch_start, batch_end):
                yy = (i % 30) + 1
                mm = (i % 12) + 1
                nnn = i % 100000
                arxiv_id = f"{yy:02d}{mm:02d}.{nnn:05d}"
                title = f"Test Paper {i}: A Study on Topic {i % 500}"
                normalized_title = title.lower()
                doi = f"10.1234/test.{i}" if i % 3 == 0 else None

                paper_values.append({
                    "id": arxiv_id,
                    "title": title,
                    "normalized_title": normalized_title,
                    "authors": [f"Author {i % 100}"],
                    "abstract": f"Abstract for paper {i}. " * 5,
                    "categories": ["cs.AI", "cs.LG"],
                    "primary_category": "cs.AI",
                    "doi": doi,
                    "latest_version": 1,
                    "source": "oaipmh",
                    "first_version_date": date(2025, 1, 1),
                    "latest_version_date": date(2025, 1, 1),
                    "inserted_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                })

                version_values.append({
                    "base_id": arxiv_id,
                    "version": 1,
                    "versioned_id": f"{arxiv_id}v1",
                    "version_date": date(2025, 1, 1),
                    "is_withdrawn": False,
                    "title_snapshot": title,
                    "pdf_status": "pending",
                })

            await conn.execute(
                text("""
                    INSERT INTO arxiv_papers (id, title, normalized_title, authors, abstract,
                        categories, primary_category, doi, latest_version, source,
                        first_version_date, latest_version_date, inserted_at, updated_at)
                    VALUES (:id, :title, :normalized_title, :authors, :abstract,
                        :categories, :primary_category, :doi, :latest_version, :source,
                        :first_version_date, :latest_version_date, :inserted_at, :updated_at)
                    ON CONFLICT (id) DO NOTHING
                """),
                paper_values,
            )

            await conn.execute(
                text("""
                    INSERT INTO paper_versions (base_id, version, versioned_id, version_date,
                        is_withdrawn, title_snapshot, pdf_status)
                    VALUES (:base_id, :version, :versioned_id, :version_date,
                        :is_withdrawn, :title_snapshot, :pdf_status)
                    ON CONFLICT (base_id, version) DO NOTHING
                """),
                version_values,
            )

            inserted += (batch_end - batch_start)
            if inserted % 10000 == 0 or batch_end == NUM_PAPERS:
                print(f"    {inserted}/{NUM_PAPERS} inserted...")

        elapsed = time.monotonic() - t0
        print(f"  Seeded {NUM_PAPERS} papers in {elapsed:.1f}s ({NUM_PAPERS/elapsed:.0f} rows/s)")


async def benchmark_resolve(engine):
    """Benchmark resolving papers by arXiv ID, DOI, and title."""
    from arxiv_mirror.db.crud import get_paper
    from arxiv_mirror.resolver.parser import parse_input
    from arxiv_mirror.resolver.normalizer import resolve

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # --- Test 1: Direct ID lookup (the hot path) ---
    print("\n--- Resolve Benchmark: Direct ID Lookup ---")
    async with session_factory() as session:
        await get_paper(session, "0101.00000")

        test_ids = [f"{(i % 30)+1:02d}{(i % 12)+1:02d}.{i % 100000:05d}" for i in range(NUM_PAPERS)]

        t0 = time.monotonic()
        resolved = 0
        for arxiv_id in test_ids[:30_000]:
            paper = await get_paper(session, arxiv_id)
            if paper is not None:
                resolved += 1
        elapsed = time.monotonic() - t0

        rps = resolved / elapsed if elapsed > 0 else 0
        print(f"  {resolved}/{len(test_ids[:30_000])} resolved in {elapsed:.2f}s")
        print(f"  Throughput: {rps:.0f} resolves/s")

    # --- Test 2: Full resolve pipeline (parse + normalize + DB lookup) ---
    print("\n--- Resolve Benchmark: Full Pipeline (parse+resolve) ---")
    async with session_factory() as session:
        queries = [f"{(i % 30)+1:02d}{(i % 12)+1:02d}.{i % 100000:05d}" for i in range(30_000)]

        t0 = time.monotonic()
        resolved = 0
        for q in queries:
            parsed = parse_input(q)
            resp = await resolve(session, parsed)
            if resp.state.value == "resolved":
                resolved += 1
        elapsed = time.monotonic() - t0

        rps = resolved / elapsed if elapsed > 0 else 0
        print(f"  {resolved}/{len(queries)} resolved in {elapsed:.2f}s")
        print(f"  Throughput: {rps:.0f} resolves/s")

    # --- Test 3: Concurrent resolve (simulating API load) ---
    concurrent_resolve = 50
    print(f"\n--- Resolve Benchmark: Concurrent ({concurrent_resolve} coroutines) ---")

    queries = [f"{(i % 30)+1:02d}{(i % 12)+1:02d}.{i % 100000:05d}" for i in range(30_000)]
    sem = asyncio.Semaphore(concurrent_resolve)

    async def resolve_one(q):
        async with sem:
            async with session_factory() as session:
                parsed = parse_input(q)
                return await resolve(session, parsed)

    t0 = time.monotonic()
    tasks = [resolve_one(q) for q in queries]
    results = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - t0

    resolved = sum(1 for r in results if r.state.value == "resolved")
    rps = resolved / elapsed if elapsed > 0 else 0
    print(f"  {resolved}/{len(queries)} resolved in {elapsed:.2f}s")
    print(f"  Throughput: {rps:.0f} resolves/s (concurrent)")

    # --- Test 4: Title-based resolve (trigram search) ---
    print("\n--- Resolve Benchmark: Title Search (trigram) ---")
    async with session_factory() as session:
        test_titles = [f"Test Paper {i}: A Study on Topic {i % 500}" for i in range(0, 1000, 10)]

        t0 = time.monotonic()
        found = 0
        for title in test_titles:
            parsed = parse_input(title)
            resp = await resolve(session, parsed)
            if resp.state.value == "resolved":
                found += 1
        elapsed = time.monotonic() - t0

        tps = found / elapsed if elapsed > 0 else 0
        print(f"  {found}/{len(test_titles)} found in {elapsed:.2f}s")
        print(f"  Throughput: {tps:.0f} title searches/s")


async def simulate_concurrent_downloads(engine):
    """Simulate 300 concurrent PDF download claims (DB-level concurrency test)."""
    from arxiv_mirror.db.crud import claim_pdf_assets, create_pdf_asset, update_pdf_asset

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    print("\n--- Download Simulation: 300 Concurrent Claims ---")
    async with session_factory() as session:
        await session.execute(text("DELETE FROM pdf_assets WHERE arxiv_id LIKE 'bench_%'"))
        await session.commit()

    arxiv_ids = [f"{(i % 30)+1:02d}{(i % 12)+1:02d}.{i % 100000:05d}" for i in range(NUM_DOWNLOAD_SIM)]

    async with session_factory() as session:
        t0 = time.monotonic()
        for i, arxiv_id in enumerate(arxiv_ids):
            vid = f"{arxiv_id}v1"
            await create_pdf_asset(
                session,
                versioned_id=vid,
                arxiv_id=arxiv_id,
                version=1,
            )
        await session.commit()
        create_time = time.monotonic() - t0
        print(f"  Created {NUM_DOWNLOAD_SIM} pdf_assets in {create_time:.2f}s")

    async def worker_claim(worker_id: int, num_claims: int):
        claimed = 0
        while claimed < num_claims:
            async with session_factory() as session:
                assets = await claim_pdf_assets(session, limit=10)
                await session.commit()
                if not assets:
                    break
                claimed += len(assets)
                await asyncio.sleep(0.01)
                async with session_factory() as s2:
                    for a in assets:
                        await update_pdf_asset(
                            s2, a.id, source="remote",
                            local_path=f"/tmp/bench/{a.versioned_id}.pdf",
                            sha256=f"fake_sha256_{a.versioned_id}",
                            file_size=5 * 1024 * 1024,
                            fetched_at=datetime.now(timezone.utc),
                        )
                    await s2.commit()
        return claimed

    claims_per_worker = NUM_DOWNLOAD_SIM // 8
    t0 = time.monotonic()
    results = await asyncio.gather(*[
        worker_claim(i, claims_per_worker + (1 if i < NUM_DOWNLOAD_SIM % 8 else 0))
        for i in range(8)
    ])
    elapsed = time.monotonic() - t0

    total_claimed = sum(results)
    throughput = total_claimed / elapsed if elapsed > 0 else 0
    print(f"  {total_claimed}/{NUM_DOWNLOAD_SIM} assets claimed and processed by 8 workers in {elapsed:.2f}s")
    print(f"  Throughput: {throughput:.0f} assets/s")
    print(f"  Per-asset latency: {elapsed/total_claimed*1000:.1f}ms (avg)")


async def estimate_bandwidth_potential():
    """Calculate theoretical bandwidth for 300 PDF downloads."""
    print("\n--- Bandwidth Estimation ---")
    pdf_size_mb = 5
    total_data_gb = NUM_DOWNLOAD_SIM * pdf_size_mb / 1024
    gigabit_mbps = 1000
    theoretical_sec = (total_data_gb * 8 * 1024) / gigabit_mbps

    print(f"  Average PDF size: {pdf_size_mb} MB")
    print(f"  Total data ({NUM_DOWNLOAD_SIM} PDFs): {total_data_gb:.2f} GB")
    print(f"  Theoretical min (1 Gbps): {theoretical_sec:.1f}s")
    print(f"  Required throughput: {total_data_gb * 1024 / theoretical_sec:.1f} MB/s")

    with_ratelimit = NUM_DOWNLOAD_SIM * 3.0 / 8
    print(f"\n  With 8 concurrent + 3s rate limit: {with_ratelimit:.0f}s")
    print(f"  Effective throughput: {total_data_gb * 1024 / with_ratelimit:.1f} MB/s")

    per_conn_speed = 100
    with_s3 = total_data_gb * 1024 / (8 * per_conn_speed)
    print(f"\n  With S3 mirror (8 conn @ {per_conn_speed} MB/s each): {with_s3:.1f}s")
    print(f"  Effective throughput: {8 * per_conn_speed:.0f} MB/s")


async def main():
    print("=" * 60)
    print("PaperMirror Pressure Test")
    print("=" * 60)

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_size=60, max_overflow=20)

    try:
        print("\n[1/4] Seeding test data...")
        await seed_data(engine)

        print("\n[2/4] Running resolve benchmarks...")
        await benchmark_resolve(engine)

        print("\n[3/4] Simulating concurrent downloads...")
        await simulate_concurrent_downloads(engine)

        print("\n[4/4] Bandwidth analysis...")
        await estimate_bandwidth_potential()

        print("\n" + "=" * 60)
        print("Pressure test complete.")
        print("=" * 60)

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
