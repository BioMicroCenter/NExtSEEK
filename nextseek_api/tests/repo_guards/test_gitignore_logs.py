"""The checkout's log directory never enters git.

LOG_DIR is ./logs inside the checkout (docker-compose.yml), and graph_sync writes its run directories there: the study
merge's journal, the IN_STUDY archives and every other archive hold graph ids and study titles.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_gitignore_ignores_the_log_directory():
    lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "/logs/" in lines
