"""DD-19: hash_identity is the single source of truth for identity hashing."""

from pathlib import Path


def test_no_inline_hashing_outside_identity_module():
    pkg = Path(__file__).resolve().parents[1]
    offenders = []

    for py_file in pkg.rglob("*.py"):
        if py_file.name == "identity.py":
            continue
        if "tests" in py_file.parts:
            continue

        content = py_file.read_text(encoding="utf-8")
        if "hashlib" in content or "sha256" in content or ".hexdigest()" in content:
            offenders.append(str(py_file.relative_to(pkg)))

    assert offenders == [], (
        f"DD-19 violated — inline hashing outside identity.py: {offenders}. "
        "Import hash_identity from nextseek_api.batch_upload.identity instead."
    )
