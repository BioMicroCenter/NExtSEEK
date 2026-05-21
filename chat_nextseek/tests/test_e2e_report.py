"""Report HTML generation tests."""
from pathlib import Path

from e2e.manifest import Manifest, ManifestEntry
from e2e.report import generate_html_report


def test_generate_html_smoke(tmp_path: Path):
    out_dir = tmp_path / "e2e_20260520_120000"
    out_dir.mkdir()
    m = Manifest(
        started_at="2026-05-20T12:00:00+00:00",
        ended_at="2026-05-20T12:05:00+00:00",
        ratio=0.33, seed=42, profile="mixed",
        variants=[
            ManifestEntry(id="adv.basic", family="search_advanced", status="passed", elapsed_s=2.1),
            ManifestEntry(id="graph.count", family="graph_query", status="failed", elapsed_s=1.4,
                          failed_criteria=["main: parser_plan.mode"]),
            ManifestEntry(id="p.tower", family="pipeline_nfcore", status="skipped",
                          reason="requires_env=TOWER_ACCESS_TOKEN unset"),
        ],
    )
    path = generate_html_report(m, out_dir)
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "adv.basic" in html
    assert "graph.count" in html
    assert "p.tower" in html
    assert "main: parser_plan.mode" in html  # failed criterion surfaced
    assert "TOWER_ACCESS_TOKEN" in html       # skip reason surfaced
    # 1 passed of 3 total appears in the totals header
    assert "1/3" in html or "1 / 3" in html


def test_generate_html_groups_by_family(tmp_path: Path):
    """Each family is a section in the report."""
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    m = Manifest(
        started_at="x", ended_at="x", ratio=1.0, seed=1, profile="mixed",
        variants=[
            ManifestEntry(id="adv.a", family="search_advanced", status="passed", elapsed_s=1.0),
            ManifestEntry(id="adv.b", family="search_advanced", status="passed", elapsed_s=1.0),
            ManifestEntry(id="graph.a", family="graph_query", status="failed", elapsed_s=2.0),
        ],
    )
    path = generate_html_report(m, out_dir)
    html = path.read_text()
    # Family headers present
    assert "search_advanced" in html
    assert "graph_query" in html
    # 2/2 vs 1/0 totals per family
    assert "2/2" in html
    assert "0/1" in html


def test_generate_html_returns_path(tmp_path: Path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    m = Manifest(
        started_at="x", ended_at="x", ratio=1.0, seed=1, profile="mixed",
        variants=[ManifestEntry(id="a.b", family="f", status="passed", elapsed_s=0.5)],
    )
    path = generate_html_report(m, out_dir)
    assert path == out_dir / "report.html"
    assert path.exists()


def test_generate_html_html_escapes_user_content(tmp_path: Path):
    """ID/reason content is HTML-escaped to prevent injection in the report."""
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    m = Manifest(
        started_at="x", ended_at="x", ratio=1.0, seed=1, profile="mixed",
        variants=[ManifestEntry(id="<script>alert(1)</script>", family="f", status="passed", elapsed_s=0)],
    )
    path = generate_html_report(m, out_dir)
    html = path.read_text()
    assert "<script>alert(1)</script>" not in html  # raw must not appear
    assert "&lt;script&gt;" in html  # escaped form appears
