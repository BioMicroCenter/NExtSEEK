"""Guard test: e2e runner imports only stable paths.

If any import below breaks, the in-flight src/ restructure has moved a
stable path and the E2E runner needs an update. See:
  docs/superpowers/specs/2026-05-20-e2e-test-design.md §2 (Refactor-safety)
"""


def test_stable_orchestrator_path():
    from chat_nextseek.orchestrator import run_query  # noqa: F401


def test_stable_session_path():
    from chat_nextseek.session import SQLiteSessionState  # noqa: F401


def test_stable_config_path():
    from chat_nextseek.config import ChatConfig  # noqa: F401


def test_stable_portable_surface():
    # Portable.py is pinned by tests/test_portable_contract.py;
    # this guard exists to fail loudly if the import path itself moves.
    import chat_nextseek.portable as portable
    assert hasattr(portable, "__all__")


def test_e2e_package_imports():
    from e2e.catalog import load_catalog  # noqa: F401
    from e2e.criteria import check_pass, resolve_field  # noqa: F401
    from e2e.sampler import sample, env_satisfied  # noqa: F401
    from e2e.manifest import Manifest, write_manifest, load_manifest, filter_for_rerun  # noqa: F401
    from e2e.runner import run_variant, run_main  # noqa: F401
    from e2e.report import generate_html_report  # noqa: F401
