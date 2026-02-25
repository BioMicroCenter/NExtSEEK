#!/usr/bin/env python
"""Live E2E test: upload existing samples and verify Neo4j relationships.

Queries the SEEK database for 5 existing samples that have study associations,
creates a minimal .xlsx, POSTs it to the batch upload API, waits for completion,
then verifies IN_STUDY and OF_TYPE relationships in Neo4j.

Usage:
    /opt/NExtSEEK/.venv/bin/python scripts/test_batch_upload_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

# Ensure Django settings are configured
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django
django.setup()

import requests
from django.conf import settings

# ── Configuration ────────────────────────────────────────────────────────────

API_BASE = "https://nextseek-dev.mit.edu"
AUTH = ("demo", "demopassword")
HEADERS = {"Host": "nextseek-dev.mit.edu"}
POLL_INTERVAL = 2
POLL_TIMEOUT = 120

# ── Step 1: Query SQL for samples with study associations ───────────────────


def get_test_samples():
    """Find 5 existing samples that have study_id via assay_assets → assays."""
    from nextseek_api.batch_upload.db_engine import get_connection
    from sqlalchemy import text

    with get_connection() as conn:
        sql = text("""
            SELECT DISTINCT
                s.uuid AS uid,
                st.title AS sample_type,
                st.id AS sample_type_id,
                s.json_metadata,
                a.study_id,
                GROUP_CONCAT(DISTINCT a.id) AS assay_ids
            FROM samples s
            JOIN sample_types st ON s.sample_type_id = st.id
            JOIN assay_assets aa ON aa.asset_id = s.id AND aa.asset_type = 'Sample'
            JOIN assays a ON a.id = aa.assay_id
            WHERE a.study_id IS NOT NULL
              AND s.uuid IS NOT NULL
              AND s.uuid != ''
            GROUP BY s.uuid, st.title, st.id, s.json_metadata, a.study_id
            LIMIT 5
        """)
        rows = conn.execute(sql).fetchall()

    if len(rows) < 5:
        print(f"WARNING: Only found {len(rows)} samples with study associations (need 5)")
        if not rows:
            print("FAIL: No suitable samples found")
            sys.exit(1)

    samples = []
    for row in rows:
        uid, sample_type, st_id, json_meta, study_id, assay_ids_str = row
        assay_ids = assay_ids_str if assay_ids_str else ""
        samples.append({
            "UID": uid,
            "SampleType": sample_type,
            "sample_type_id": st_id,
            "json_metadata": json_meta or "{}",
            "study_id": int(study_id),
            "assay_ids": assay_ids,
        })
    return samples


# ── Step 2: Create .xlsx file ────────────────────────────────────────────────


def create_xlsx(samples: list, path: str):
    """Create a minimal .xlsx for batch upload."""
    try:
        import openpyxl
    except ImportError:
        print("FAIL: openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Samples"

    headers = ["UID", "SampleType", "json_metadata", "study_id", "assay_ids"]
    ws.append(headers)

    for s in samples:
        ws.append([s["UID"], s["SampleType"], s["json_metadata"], s["study_id"], s["assay_ids"]])

    wb.save(path)
    print(f"Created test xlsx at: {path}")


# ── Step 3: Upload via API ───────────────────────────────────────────────────


def upload_file(xlsx_path: str, project_id: int = 1) -> str:
    """POST file to batch upload API. Returns job_id."""
    url = f"{API_BASE}/nextseek_api/batch-upload/start/"
    with open(xlsx_path, "rb") as f:
        resp = requests.post(
            url,
            files={"file": ("test_e2e.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"project_id": project_id},
            auth=AUTH,
            headers=HEADERS,
            verify=True,
        )

    if resp.status_code not in (200, 201, 202):
        print(f"FAIL: Upload returned {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    job_id = data.get("job_id")
    print(f"Upload started: job_id={job_id}")
    return job_id


def poll_status(job_id: str) -> dict:
    """Poll until job completes or times out."""
    url = f"{API_BASE}/nextseek_api/batch-upload/status/{job_id}/"
    start = time.time()

    while time.time() - start < POLL_TIMEOUT:
        resp = requests.get(url, auth=AUTH, headers=HEADERS, verify=True)
        if resp.status_code != 200:
            print(f"WARNING: Status poll returned {resp.status_code}")
            time.sleep(POLL_INTERVAL)
            continue

        data = resp.json()
        state = data.get("state", "UNKNOWN")
        meta = data.get("meta", {})
        print(f"  State: {state} {meta}")

        if state == "SUCCESS":
            return data.get("result", data)
        if state in ("FAILURE", "REVOKED"):
            print(f"FAIL: Job {state}: {data}")
            sys.exit(1)
        # PENDING, STARTED, PROGRESS — keep polling

        time.sleep(POLL_INTERVAL)

    print(f"FAIL: Job timed out after {POLL_TIMEOUT}s")
    sys.exit(1)


# ── Step 4: Verify Neo4j relationships ──────────────────────────────────────


def verify_neo4j(samples: list):
    """Check IN_STUDY and OF_TYPE relationships exist in Neo4j."""
    from nextseek_api.batch_upload.config import Neo4jConfig

    cfg = Neo4jConfig.from_django_settings()
    if not cfg.NEO4J_UPLOAD_ENABLED:
        print("SKIP: Neo4j not configured, cannot verify")
        return

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(cfg.URI, auth=(cfg.NEO4J_USER, cfg.PASSWORD))
    db = cfg.NEO4J_DB

    results = []
    try:
        for s in samples:
            uid = s["UID"]
            study_id = s["study_id"]
            st_id = s["sample_type_id"]

            # Check IN_STUDY
            r = driver.execute_query(
                "MATCH (s:Sample {uuid: $uuid})-[:IN_STUDY]->(st:Study {id: $sid}) RETURN count(*) AS c",
                {"uuid": uid, "sid": study_id},
                database_=db,
            )
            in_study_ok = r.records[0]["c"] > 0

            # Check OF_TYPE
            r = driver.execute_query(
                "MATCH (s:Sample {uuid: $uuid})-[:OF_TYPE]->(st:SampleType {id: $stid}) RETURN count(*) AS c",
                {"uuid": uid, "stid": st_id},
                database_=db,
            )
            of_type_ok = r.records[0]["c"] > 0

            status = "PASS" if (in_study_ok and of_type_ok) else "FAIL"
            results.append((uid, in_study_ok, of_type_ok, status))
            print(f"  {uid}: IN_STUDY={'OK' if in_study_ok else 'MISSING'}, OF_TYPE={'OK' if of_type_ok else 'MISSING'} -> {status}")

    finally:
        driver.close()

    passed = sum(1 for _, _, _, s in results if s == "PASS")
    total = len(results)
    print(f"\nResult: {passed}/{total} samples verified")
    if passed < total:
        sys.exit(1)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("Batch Upload E2E Test: IN_STUDY + OF_TYPE relationships")
    print("=" * 60)

    print("\n1. Querying SQL for test samples...")
    samples = get_test_samples()
    print(f"   Found {len(samples)} samples")
    for s in samples:
        print(f"   {s['UID']}: type={s['SampleType']}, study_id={s['study_id']}")

    print("\n2. Creating test .xlsx...")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        xlsx_path = f.name
    create_xlsx(samples, xlsx_path)

    try:
        print("\n3. Uploading to batch upload API...")
        job_id = upload_file(xlsx_path)

        print("\n4. Polling for completion...")
        result = poll_status(job_id)
        print(f"   Job completed: {json.dumps(result.get('totals', {}), indent=2)}")

        print("\n5. Verifying Neo4j relationships...")
        verify_neo4j(samples)

    finally:
        os.unlink(xlsx_path)

    print("\n" + "=" * 60)
    print("E2E TEST PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
