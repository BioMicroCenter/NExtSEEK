"""scripts/reingest/sanitize_fixture.py: rewrite cluster prefixes to /RUN, refuse
absolute paths that remain, email addresses, URLs, and files that are not UTF-8
text."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "reingest" / "sanitize_fixture.py"
CLEAN = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs" / "rnavar" / "1.3.0"


def run(folder, *prefixes):
    return subprocess.run([sys.executable, str(SCRIPT), str(folder), *prefixes],
                          capture_output=True, text=True)


def test_clean_run_fixture_passes_unchanged(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    before = {}
    for src in CLEAN.rglob("*"):
        if src.is_file():
            dst = folder / src.relative_to(CLEAN)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            before[dst] = src.read_bytes()
    result = run(folder, "/RUN")
    assert result.returncode == 0, result.stderr
    assert {p: p.read_bytes() for p in before} == before


def test_prefix_is_rewritten_in_any_file_type(tmp_path):
    log = tmp_path / "star" / "log" / "S.Log.final.out"
    log.parent.mkdir(parents=True)
    log.write_text("Started: /a/b/work/x/S.fastq\nline two\n")
    result = run(tmp_path, "/a/b")
    assert result.returncode == 0, result.stderr
    assert log.read_text() == "Started: /RUN/work/x/S.fastq\nline two\n"


def test_prefix_matches_whole_segments_only(tmp_path):
    f = tmp_path / "p.json"
    f.write_text('{"x": "/a/bc/x"}\n')
    result = run(tmp_path, "/a/b")
    assert result.returncode == 1
    assert "p.json: /a/bc/x" in result.stderr
    assert f.read_text() == '{"x": "/a/bc/x"}\n'


def test_run_lookalike_directories_are_refused(tmp_path):
    for i, name in enumerate(("/RUN-old/a/b", "/RUN.x/a/b")):
        folder = tmp_path / str(i)
        folder.mkdir()
        (folder / "p.json").write_text('{"x": "%s"}\n' % name)
        result = run(folder, "/RUN")
        assert result.returncode == 1
        assert name in result.stderr


def test_non_utf8_file_is_refused_and_left_alone(tmp_path):
    blob = tmp_path / "pipeline_info" / "trace.bin"
    blob.parent.mkdir()
    blob.write_bytes(b"\xff\xfe\x00bad")
    other = tmp_path / "p.txt"
    other.write_text("/a/b/c\n")
    result = run(tmp_path, "/a/b")
    assert result.returncode == 1
    assert "trace.bin" in result.stderr
    assert blob.read_bytes() == b"\xff\xfe\x00bad"
    assert other.read_text() == "/a/b/c\n"   # nothing is written on a refusal


def test_other_prefixes_are_rewritten_too(tmp_path):
    (tmp_path / "a.csv").write_text("/launch/dir/f,/work/dir/g\n")
    result = run(tmp_path, "/launch/dir", "/work/dir")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "a.csv").read_text() == "/RUN/f,/RUN/g\n"


def test_an_email_address_is_refused_and_left_alone(tmp_path):
    params = tmp_path / "pipeline_info" / "params_2026-01-01_00-00-00.json"
    params.parent.mkdir()
    body = '{"outdir": "/a/b/out", "email": "someone@example.org"}\n'
    params.write_text(body)
    result = run(tmp_path, "/a/b")
    assert result.returncode == 1
    assert "params_2026-01-01_00-00-00.json: someone@example.org" in result.stderr
    assert params.read_text() == body


def test_a_url_is_refused_and_left_alone(tmp_path):
    params = tmp_path / "params.json"
    body = '{"hook_url": "https://hooks.example/services/T000/B000/XXXX"}\n'
    params.write_text(body)
    result = run(tmp_path, "/RUN")
    assert result.returncode == 1
    assert "params.json: https://hooks.example/services/T000/B000/XXXX" in result.stderr
    assert params.read_text() == body


def test_a_url_of_any_scheme_is_refused(tmp_path):
    (tmp_path / "params.json").write_text('{"igenomes_base": "s3://bucket/igenomes"}\n')
    result = run(tmp_path, "/RUN")
    assert result.returncode == 1
    assert "params.json: s3://bucket/igenomes" in result.stderr


def test_missing_arguments_print_usage_and_exit_2():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "Usage: sanitize_fixture.py" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_public_nf_core_test_data_url_is_allowed(tmp_path):
    f = tmp_path / "samplesheet.csv"
    f.write_text("sample,fastq_1\nS1,https://raw.githubusercontent.com/nf-core/test-datasets/sarek/x/test_1.fastq.gz\n")
    result = run(tmp_path, "/RUN")
    assert result.returncode == 0, result.stderr


def test_a_lookalike_of_the_test_data_url_is_still_refused(tmp_path):
    f = tmp_path / "p.json"
    f.write_text('{"x": "https://raw.githubusercontent.com/nf-core-evil/test-datasets/x"}')
    result = run(tmp_path, "/RUN")
    assert result.returncode == 1 and "nf-core-evil" in result.stderr


def test_an_allowed_url_followed_by_a_private_one_after_a_comma_is_refused(tmp_path):
    (tmp_path / "s.csv").write_text(
        "a,https://raw.githubusercontent.com/nf-core/test-datasets/x/1.fq.gz,https://private.example/y\n")
    result = run(tmp_path, "/RUN")
    assert result.returncode == 1 and "private.example" in result.stderr


def test_an_allowed_url_followed_by_a_private_one_after_a_semicolon_is_refused(tmp_path):
    (tmp_path / "s.csv").write_text(
        "https://github.com/nf-core/test-datasets/x;https://private.example/y\n")
    result = run(tmp_path, "/RUN")
    assert result.returncode == 1 and "private.example" in result.stderr


def test_two_allowed_urls_in_one_row_are_allowed(tmp_path):
    (tmp_path / "s.csv").write_text(
        "S1,https://raw.githubusercontent.com/nf-core/test-datasets/x/1.fq.gz,"
        "https://raw.githubusercontent.com/nf-core/test-datasets/x/2.fq.gz\n")
    assert run(tmp_path, "/RUN").returncode == 0


def test_an_escaped_json_newline_does_not_fold_a_second_url_into_an_allowed_one(tmp_path):
    (tmp_path / "p.json").write_text(
        '{"x": "https://github.com/nf-core/test-datasets/a\\nhttps://private.example/y"}')
    result = run(tmp_path, "/RUN")
    assert result.returncode == 1 and "private.example" in result.stderr


def test_a_dot_dot_segment_in_an_allowed_url_is_refused(tmp_path):
    (tmp_path / "p.json").write_text('{"x": "https://raw.githubusercontent.com/nf-core/test-datasets/../../evil/repo/x"}')
    assert run(tmp_path, "/RUN").returncode == 1


def test_a_query_or_fragment_on_an_allowed_url_is_refused(tmp_path):
    for i, tail in enumerate(("?token=abc", "#frag")):
        folder = tmp_path / str(i)
        folder.mkdir()
        (folder / "p.json").write_text('{"x": "https://github.com/nf-core/test-datasets/x%s"}' % tail)
        assert run(folder, "/RUN").returncode == 1, tail


def test_a_userinfo_or_port_host_lookalike_is_refused(tmp_path):
    (tmp_path / "p.json").write_text('{"x": "https://github.com@evil.example/nf-core/test-datasets/x"}')
    assert run(tmp_path, "/RUN").returncode == 1


def test_glue_characters_do_not_hide_a_second_url_behind_an_allowed_one(tmp_path):
    ok = "https://raw.githubusercontent.com/nf-core/test-datasets/a.fq.gz"
    for i, glued in enumerate((ok + "|https://private.example.org/b", ok + ")https://private.example.org/b",
                               ok + "}https://private.example.org/b", ok + "https://private.example.org/b")):
        folder = tmp_path / str(i)
        folder.mkdir()
        (folder / "s.csv").write_text(f"S1,{glued}\n")
        result = run(folder, "/RUN")
        assert result.returncode == 1 and "private.example.org" in result.stderr, glued


def test_a_markdown_link_pair_does_not_hide_a_private_url(tmp_path):
    (tmp_path / "n.md").write_text(
        "[a](https://github.com/nf-core/test-datasets/x)[b](https://private.example.org/y)\n")
    result = run(tmp_path, "/RUN")
    assert result.returncode == 1 and "private.example.org" in result.stderr


def test_an_allowed_host_path_with_odd_characters_is_refused(tmp_path):
    (tmp_path / "p.json").write_text('{"x": "https://github.com/nf-core/test-datasets/a%20b|c"}')
    assert run(tmp_path, "/RUN").returncode == 1


def test_closing_punctuation_after_an_allowed_url_is_not_part_of_it(tmp_path):
    ok = "https://raw.githubusercontent.com/nf-core/test-datasets/x/1.fq.gz"
    for i, line in enumerate((f"[a]({ok})", f"(data at {ok})", f"see {ok}.", f'"{ok}",', f"<{ok}>;")):
        folder = tmp_path / str(i)
        folder.mkdir()
        (folder / "n.md").write_text(line + "\n")
        result = run(folder, "/RUN")
        assert result.returncode == 0, (line, result.stderr)
