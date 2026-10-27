"""The container runs a copy of curation_check.py: it has no pydantic and no NessieAI package."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_the_container_copy_is_byte_identical():
    server = ROOT / "NessieAI/ns/paper/curation_check.py"
    container = ROOT / "NessieAI/docker/cc-runtime/build_context/plugins/nextseek/bin/_paper_curation_check.py"
    assert container.read_bytes() == server.read_bytes(), "copy NessieAI/ns/paper/curation_check.py over the bin copy"
