from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import subprocess
import sys


BIN = pathlib.Path("build_context/plugins/nextseek/bin")
sys.path.insert(0, str(BIN))
import _batch_upload_runner as runner
NEW_SHIMS = {
    "nextseek-sample-search": "sample-search",
    "nextseek-project-resolve": "project-resolve",
    "nextseek-assay-resolve": "assay-resolve",
}
CORE_SHIMS = {
    "nextseek-build-payload": "build-payload",
    "nextseek-validate-upload": "build-validate",
    "nextseek-sampletype-attrs": "attrs",
    "nextseek-extract-text": "extract",
}
FORBIDDEN_WORDS = {
    "start",
    "upload",
    "sample-read",
    "--merge-existing",
    "--as-merge-map",
    "read_samples",
}
FORBIDDEN_PATH_RE = re.compile(r"(^|/)batch-upload/start/?$|/start/")


def _shim_names() -> list[str]:
    return sorted([*NEW_SHIMS, *CORE_SHIMS])


def _fake_python(tmp_path: pathlib.Path) -> pathlib.Path:
    fake = tmp_path / "python"
    fake.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in start|upload|sample-read|--merge-existing|--as-merge-map|*/start/*|*batch-upload/start*) exit 97 ;;\n"
        "  esac\n"
        "done\n"
        "printf '%s\\n' \"$@\" > \"$NEXTSEEK_ARGV_CAPTURE\"\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def _run_with_fake_python(tmp_path: pathlib.Path, shim: str, *args: str) -> list[str]:
    _fake_python(tmp_path)
    capture = tmp_path / "argv.txt"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "NEXTSEEK_ARGV_CAPTURE": str(capture),
        "NEXTSEEK_OUTPUTS_DIR": str(tmp_path / "out"),
    }
    result = subprocess.run([str(BIN / shim), *args], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return capture.read_text().splitlines()


def _non_comment_tokens(path: pathlib.Path) -> list[str]:
    tokens: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens.extend(re.findall(r"--?[A-Za-z0-9_-]+|/[A-Za-z0-9_/-]+|[A-Za-z0-9_/-]+", line))
    return tokens


def _expanded_shell_words(text: str) -> list[str]:
    values: dict[str, str] = {}
    words: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assign = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(['\"]?)([^'\"]+)\2$", line)
        if assign:
            values[assign.group(1)] = assign.group(3)
        expanded = line
        for name, value in values.items():
            expanded = expanded.replace(f"${name}", value).replace(f"${{{name}}}", value)
        words.extend(re.findall(r"--?[A-Za-z0-9_-]+|/[A-Za-z0-9_/-]+|[A-Za-z0-9_/-]+", expanded))
    return words


def _assert_no_forbidden_expanded_words(text: str) -> None:
    words = [word.strip("'\";") for word in _expanded_shell_words(text)]
    assert not (set(words) & (FORBIDDEN_WORDS - {"--upload"}))
    assert not any(FORBIDDEN_PATH_RE.search(word) for word in words)


def test_build_payload_shim_writes_staging_only(tmp_path):
    rows = tmp_path / "rows.json"
    schema = tmp_path / "schema.json"
    titles = tmp_path / "titles.json"
    rows.write_text("[]")
    schema.write_text("[]")
    titles.write_text("{}")

    result = subprocess.run(
        [
            str(BIN / "nextseek-build-payload"),
            "--rows",
            str(rows),
            "--schema",
            str(schema),
            "--id-to-title",
            str(titles),
            "--out",
            "/data/scratch/out",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert '"gate":"staging"' in result.stdout
    assert not pathlib.Path("/data/scratch/out/payload_flat.xlsx").exists()


def test_project_resolve_shim_forwards_name(tmp_path):
    # SKILL step 1 prescribes `nextseek-project-resolve --name '<project name>'`; the shim MUST
    # forward it to the runner (W8R remediation review H1: the shim rejected --name with exit 3,
    # forcing the agent to bypass the shim and call the runner module directly).
    argv = _run_with_fake_python(
        tmp_path, "nextseek-project-resolve",
        "--name", "Published Data", "--confirmed", "--out", str(tmp_path / "token.json"),
    )
    assert argv[0].endswith("_batch_upload_runner.py")
    assert argv[1] == "project-resolve"
    name_idx = argv.index("--name")
    assert argv[name_idx + 1] == "Published Data"
    assert "--confirmed" in argv


def test_project_resolve_shim_two_step_unconfirmed(tmp_path):
    # Step 1 of the curator flow: resolve WITHOUT --confirmed (the runner emits the id+title for
    # curator confirmation). The shim must allow it and must NOT inject --confirmed itself.
    argv = _run_with_fake_python(
        tmp_path, "nextseek-project-resolve",
        "--name", "Published Data", "--out", str(tmp_path / "token.json"),
    )
    assert "--name" in argv
    assert "--confirmed" not in argv


def test_project_resolve_shim_still_forwards_project_id(tmp_path):
    argv = _run_with_fake_python(
        tmp_path, "nextseek-project-resolve",
        "--project-id", "1", "--confirmed", "--out", str(tmp_path / "token.json"),
    )
    id_idx = argv.index("--project-id")
    assert argv[id_idx + 1] == "1"


def test_project_resolve_shim_requires_id_or_name(tmp_path):
    result = subprocess.run(
        [str(BIN / "nextseek-project-resolve"), "--out", str(tmp_path / "token.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 3
    assert "--project-id or --name" in result.stderr


def test_shim_no_dynamic_forbidden_subcommand():
    assert not (BIN / "nextseek-sample-read").exists()
    for name in _shim_names():
        path = BIN / name
        assert path.exists(), f"{name} missing"
        assert path.stat().st_mode & stat.S_IXUSR, f"{name} not executable"
        text = "\n".join(
            line
            for line in path.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "/start/" not in text
        assert "batch-upload/start" not in text
        assert "sample-read" not in text
        assert "--merge-existing" not in text
        assert "--as-merge-map" not in text
        tokens = _non_comment_tokens(path)
        joined = " ".join(tokens)
        assert "read_samples" not in joined
        assert "upload" not in [token.strip("'\";") for token in tokens if token != "--upload"]
        _assert_no_forbidden_expanded_words(path.read_text())

    for mutant in [
        "cmd=start\nexec python runner \"$cmd\"\n",
        "flag=--merge-existing\nexec python runner build-payload \"$flag\"\n",
        "path=/batch-upload/start/\nexec python runner \"$path\"\n",
        "cmd=sample-read\nexec python runner \"$cmd\"\n",
    ]:
        try:
            _assert_no_forbidden_expanded_words(mutant)
        except AssertionError:
            continue
        raise AssertionError(f"static forbidden mutant passed: {mutant!r}")


def test_shim_dynamic_execution_rejects_forbidden_argv(tmp_path):
    expected = {
        "nextseek-sample-search": ["sample-search", "--uid", "U1"],
        "nextseek-project-resolve": ["project-resolve", "--project-id", "1", "--confirmed", "--out", str(tmp_path / "project.json")],
        "nextseek-assay-resolve": ["assay-resolve", "--project-id", "1", "--title", "RNA-seq"],
    }
    registered = set(runner._CMDS)

    for shim, expected_argv in expected.items():
        argv = _run_with_fake_python(tmp_path, shim, *expected_argv[1:])
        assert pathlib.Path(argv[0]).name == "_batch_upload_runner.py"
        assert argv[1:] == expected_argv
        assert argv[1] in registered
        assert not (set(argv[1:]) & FORBIDDEN_WORDS)
        assert not any("/start/" in item or "batch-upload/start" in item for item in argv)

    bad = tmp_path / "bad-shim"
    bad.write_text(
        "#!/bin/sh\n"
        "cmd=start\n"
        "exec python runner \"$cmd\"\n"
    )
    bad.chmod(bad.stat().st_mode | stat.S_IXUSR)
    _fake_python(tmp_path)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "NEXTSEEK_ARGV_CAPTURE": str(tmp_path / "bad.txt")}
    result = subprocess.run([str(bad)], capture_output=True, text=True, env=env)
    assert result.returncode == 97

    for shim in _shim_names():
        result = subprocess.run([str(BIN / shim), "--help"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "sample-read" not in result.stdout
        assert "--merge-existing" not in result.stdout

    assert json.dumps(sorted(registered))
