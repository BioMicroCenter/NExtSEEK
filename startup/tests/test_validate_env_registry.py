"""Doctor's advisory check of the rendered env files against the registry (#103 Task 5)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock, patch

from startup.steps import validate
from startup.steps.validate import check_env_registry

SECRET = "s3cr3t-value-xyz"


def _box(tmp_path: Path, nextseek: str | None = None, db: str | None = None, root: str | None = None) -> Path:
    (tmp_path / "docker").mkdir()
    for rel, text in (("docker/nextseek.env", nextseek), ("docker/db.env", db), (".env", root)):
        if text is not None:
            (tmp_path / rel).write_text(text)
    return tmp_path


def _clean_ns() -> str:
    """Every required nextseek.env key filled, and the two Neo4j password lines a rendered file has."""
    from startup.lib.env_registry import load
    skip = {"NEXTSEEK_NEO4J_PASSWORD", "NEO4J_PASSWORD"}
    lines = [f"{k}=v" for k, e in load().items() if e.required and "nextseek.env" in e.files and k not in skip]
    return "\n".join(lines) + "\nNEXTSEEK_NEO4J_PASSWORD='%s'\nNEO4J_PASSWORD=$NEXTSEEK_NEO4J_PASSWORD\n" % SECRET


CLEAN_NS = _clean_ns()


def _ns(extra: str = "") -> str:
    return CLEAN_NS + extra


def test_absent_files_are_ok_and_say_not_rendered(tmp_path: Path) -> None:
    r = check_env_registry(tmp_path)
    assert r.ok and not r.warn and "not rendered" in r.detail


def test_unknown_key_warns_and_names_it(tmp_path: Path) -> None:
    r = check_env_registry(_box(tmp_path, nextseek=_ns("NEXTSEEK_TYPO_KEY=1\n")))
    assert r.ok and r.warn and "NEXTSEEK_TYPO_KEY" in r.detail


def test_placeholder_in_a_required_key_warns(tmp_path: Path) -> None:
    r = check_env_registry(_box(tmp_path, nextseek=_ns("SEEK_HOST=SET_IN_LOCAL_ENV\n")))
    assert r.warn and "SEEK_HOST" in r.detail


def test_missing_required_key_warns(tmp_path: Path) -> None:
    r = check_env_registry(_box(tmp_path, nextseek=CLEAN_NS.replace("SEEK_HOSTNAME=v\n", "")))
    assert r.warn and "SEEK_HOSTNAME" in r.detail


def test_forbidden_key_warns(tmp_path: Path) -> None:
    from startup.lib.env_registry import load
    key = next((k for k, e in load().items() if e.forbidden), None)
    assert key, "the registry names no forbidden key"
    r = check_env_registry(_box(tmp_path, nextseek=_ns(f"{key}=x\n")))
    assert r.warn and key in r.detail


def test_aliases_that_differ_warn_without_printing_a_value(tmp_path: Path) -> None:
    ns = _ns().replace("\nNEO4J_PASSWORD=$NEXTSEEK_NEO4J_PASSWORD", "\nNEO4J_PASSWORD='other-" + SECRET + "'")
    r = check_env_registry(_box(tmp_path, nextseek=ns))
    assert r.warn and "NEO4J_PASSWORD" in r.detail
    assert SECRET not in r.detail and SECRET not in r.name


def test_root_env_neo4j_password_is_compared_to_the_app_one(tmp_path: Path) -> None:
    r = check_env_registry(_box(tmp_path, nextseek=CLEAN_NS, root="NEO4J_PASSWORD=different\n"))
    assert r.warn and "NEO4J_PASSWORD" in r.detail and "different" not in r.detail


def test_root_env_with_the_same_password_does_not_warn(tmp_path: Path) -> None:
    r = check_env_registry(_box(tmp_path, nextseek=CLEAN_NS, root="NEO4J_PASSWORD=%s\n" % SECRET))
    assert not r.warn, r.detail


def test_dollar_reference_to_an_equal_value_does_not_warn(tmp_path: Path) -> None:
    r = check_env_registry(_box(tmp_path, nextseek=CLEAN_NS))
    assert r.ok and not r.warn, r.detail


def test_root_env_doubled_dollar_is_read_as_one(tmp_path: Path) -> None:
    ns = CLEAN_NS.replace(SECRET, "pa$$")
    r = check_env_registry(_box(tmp_path, nextseek=ns, root="NEO4J_PASSWORD='pa$$$$'\n"))
    assert not r.warn, r.detail


def test_doctor_prints_a_warned_result_with_the_warning_prefix(tmp_path: Path) -> None:
    from startup.steps.doctor import diagnose
    warned = SimpleNamespace(name="env registry", ok=True, detail="unknown key X", warn=True)
    plain = SimpleNamespace(name="http", ok=True, detail="200")  # a mock without .warn must still work
    state = MagicMock(); state.name, state.prefix, state.ports = "n", "", {}
    with patch("startup.steps.doctor.validate") as v, patch("startup.steps.doctor.load_instance", return_value=state), \
            patch("startup.steps.doctor.prereqs") as p, patch("startup.steps.doctor.registry_push.check_registry_baseline",
                                                              return_value=("b", True, "x")):
        p.run_all.return_value = []
        v.run_all_health_checks.return_value = [warned, plain]
        out = {n: (ok, d) for n, ok, d in diagnose(tmp_path)}
    assert out["env registry"] == (True, "warning: unknown key X")
    assert out["http"] == (True, "200")


def test_doctor_command_uses_the_warning_style(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from startup import cli
    with patch("startup.steps.doctor.diagnose", return_value=[("env registry", True, "warning: x"), ("a", True, "fine")]), \
            patch.object(cli.ui, "warn") as w, patch.object(cli.ui, "ok") as ok:
        CliRunner().invoke(cli.app, ["doctor"])
    assert w.call_args_list and "env registry" in w.call_args_list[0].args[0]
    assert all("env registry" not in c.args[0] for c in ok.call_args_list)


def test_check_is_in_both_health_suites() -> None:
    for fn in (validate.run_all_health_checks, validate.run_app_health_checks):
        with patch.multiple(
            validate, check_http=DEFAULT, run_django_check=DEFAULT, check_prod_overlay_guard=DEFAULT,
            check_stale_nessie_env=DEFAULT, check_seek_url_consistency=DEFAULT, check_proxy_token=DEFAULT,
            check_cc_services=DEFAULT, check_first_party_images=DEFAULT, check_cc_runner=DEFAULT,
            load_instance=DEFAULT, check_env_registry=DEFAULT,
        ) as m:
            fn({}, Path("."), {})
            m["check_env_registry"].assert_called_once()
