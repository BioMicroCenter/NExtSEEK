import importlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def test_eval_package_is_importable():
    assert importlib.import_module("nextseek_api.eval") is not None


def test_the_dangling_exporter_reference_is_now_satisfied():
    mod = importlib.import_module("nextseek_api.eval.exporter")
    assert hasattr(mod, "FailureMode")


def test_no_module_imports_from_a_dmac_assistant_eval_checkout():
    offenders = []
    for p in (_REPO / "nextseek_api" / "eval").rglob("*.py"):
        text = p.read_text()
        if "dmac_assistant.eval" in text or "from tools.hibayes" in text:
            offenders.append(str(p.relative_to(_repo_root())))
    assert offenders == [], f"external eval imports remain: {offenders}"


def test_eval_dockerfile_builds_from_this_repo_not_a_bind_mount():
    df = (_REPO / "docker" / "eval" / "Dockerfile").read_text()
    assert "COPY nextseek_api/eval" in df
    assert "/work/src" not in df, "still expects a bind-mounted external checkout"


def _repo_root() -> Path:
    return _REPO
