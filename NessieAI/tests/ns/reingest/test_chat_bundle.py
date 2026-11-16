"""Reingest from a results bundle uploaded in the chat (run-harvest --bundle).

A bundle is one .tar.gz: the run's small files (the Luria harvest's allowlist), the input
samplesheet, and nessie_files.tsv, which lists the big files left out and names where they
live. stage_bundle unpacks it under the Luria stager's caps into a fresh folder and returns
the same (skipped, inventory) pair the stager does, so harvest_local reads it unchanged.
A bundle run is always one Nessie did not launch: a superuser only, #165's evidence rules.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import NessieAI.ns.granular as g
from NessieAI.ns.reingest import bundle, harvest
from NessieAI.ns.reingest import manifest as manifest_mod
from NessieAI.ns.reingest import store as store_mod

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs"
SAREK_AWS = FIXTURES / "sarek" / "3.10.0-aws-test_full_germline_ncbench_agilent"
ORIGIN = "s3://nf-core-awsmegatests/sarek/results-8ccac7ad/test_full_germline_ncbench_agilent"
CRAM = "preprocessing/recalibrated/S1/S1.recal.cram"
LISTING = f"#origin\t{ORIGIN}\n{CRAM}\t123456789\n"
SHEET = "patient,sample,lane,fastq_1,fastq_2\nS1,S1,1,https://x.example/S1_R1.fq.gz,https://x.example/S1_R2.fq.gz\n"
VERSIONS = "pipeline_info/nf_core_sarek_software_mqc_versions.yml"


def _tar_gz(members, *, gz=True) -> bytes:
    """members: [(name, bytes) | (name, tarfile type, link target)]: build a bundle in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for item in members:
            name, data = item[0], item[1]
            info = tarfile.TarInfo(name)
            if len(item) == 2:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:  # a non-regular member type, e.g. tarfile.SYMTYPE, with its link target
                info.type = data
                info.linkname = item[2] if len(item) > 2 else ""
                tar.addfile(info)
    raw = buf.getvalue()
    return gzip.compress(raw) if gz else raw


def _basic(**extra) -> list:
    members = [("pipeline_info/params_2026.json", b'{"aligner": "bwa-mem"}'),
               ("samplesheet.csv", SHEET.encode()),
               ("multiqc/multiqc_data/multiqc_general_stats.txt", b"Sample\tx\nS1\t1\n"),
               ("other/notes.bin", b"\x00" * 10),
               (bundle.LISTING_NAME, LISTING.encode())]
    members.extend(extra.get("more", []))
    return members


def _stage(data: bytes, tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    return bundle.stage_bundle(io.BytesIO(data), str(staged)), staged


def _refused(data: bytes, tmp_path) -> str:
    with pytest.raises(bundle.BundleRefused) as err:
        _stage(data, tmp_path)
    return err.value.error_type


# --- the stager ---------------------------------------------------------------------------

def test_only_the_allowlisted_small_files_are_unpacked(tmp_path):
    _, staged = _stage(_tar_gz(_basic()), tmp_path)
    assert (staged / "pipeline_info/params_2026.json").is_file()
    assert (staged / "samplesheet.csv").read_text() == SHEET
    assert (staged / "multiqc/multiqc_data/multiqc_general_stats.txt").is_file()
    assert not (staged / "other").exists()
    assert not (staged / bundle.LISTING_NAME).exists()  # the listing is read, never staged


def test_the_listing_stands_in_for_the_stagers_inventory_and_names_the_origin(tmp_path):
    out, _ = _stage(_tar_gz(_basic()), tmp_path)
    assert out.origin == ORIGIN
    assert out.inventory == [{"path": CRAM, "bytes": 123456789}]  # no checksum: not the files at the origin


def test_the_bundle_is_identified_by_its_sha256(tmp_path):
    data = _tar_gz(_basic())
    out, _ = _stage(data, tmp_path)
    assert out.sha256 == hashlib.sha256(data).hexdigest()


def test_one_top_folder_is_stripped(tmp_path):
    members = [(f"aws_sarek/{name}", data) for name, data in _basic()]
    out, staged = _stage(_tar_gz(members), tmp_path)
    assert (staged / "samplesheet.csv").is_file()
    assert out.origin == ORIGIN


def test_a_listed_type_inside_the_bundle_is_listed_with_its_size_and_no_checksum(tmp_path):
    vcf = b"##fileformat=VCFv4.2\n"
    out, _ = _stage(_tar_gz(_basic(more=[("variant_calling/S1.vcf.gz", vcf)])), tmp_path)
    assert {"path": "variant_calling/S1.vcf.gz", "bytes": len(vcf)} in out.inventory
    assert all("checksum" not in entry for entry in out.inventory)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_links_and_devices_are_skipped_with_a_reason(tmp_path, kind):
    out, staged = _stage(_tar_gz(_basic(more=[("pipeline_info/params_link.json", kind, "/etc/passwd")])),
                         tmp_path)
    assert not (staged / "pipeline_info/params_link.json").exists()
    assert [s["path"] for s in out.skipped] == ["pipeline_info/params_link.json"]


@pytest.mark.parametrize("name", ["/etc/samplesheet.csv", "../samplesheet.csv", "a/../../samplesheet.csv"])
def test_an_absolute_or_escaping_member_refuses_the_bundle(tmp_path, name):
    assert _refused(_tar_gz(_basic(more=[(name, b"x")])), tmp_path) == "bundle_unsafe_member"


def test_a_duplicate_member_refuses_the_bundle(tmp_path):
    assert _refused(_tar_gz(_basic(more=[("samplesheet.csv", b"other")])), tmp_path) == "bundle_unsafe_member"


def test_the_stagers_per_file_total_and_count_caps_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest, "MAX_FILE_BYTES", 30)
    out, staged = _stage(_tar_gz(_basic(more=[("pipeline_info/params_big.json", b"x" * 31)])), tmp_path)
    assert not (staged / "pipeline_info/params_big.json").exists()
    assert any(s["path"] == "pipeline_info/params_big.json" and "max file bytes" in s["reason"]
               for s in out.skipped)


def test_the_count_cap_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest, "MAX_FILES", 1)
    out, _ = _stage(_tar_gz(_basic()), tmp_path)
    assert sum("max file count" in s["reason"] for s in out.skipped) == 2


def test_declared_sizes_over_the_unpacked_cap_refuse_it_before_it_is_unpacked(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "MAX_UNPACKED_BYTES", 100)
    assert _refused(_tar_gz(_basic(more=[("big.dat", b"\x00" * 200)])), tmp_path) == "bundle_unpacks_too_large"


def test_a_bundle_over_the_upload_cap_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "MAX_BUNDLE_BYTES", 10)
    assert _refused(_tar_gz(_basic()), tmp_path) == "bundle_too_large"


@pytest.mark.parametrize("data", [b"plain text, not a bundle", b"PK\x03\x04zip", None])
def test_anything_but_a_tar_gz_is_refused(tmp_path, data):
    if data is None:
        data = _tar_gz(_basic(), gz=False)  # a tar, not gzipped
    assert _refused(data, tmp_path) == "bundle_not_a_tar_gz"


def test_a_bundle_with_no_listing_has_no_origin_and_is_refused(tmp_path):
    members = [m for m in _basic() if m[0] != bundle.LISTING_NAME]
    assert _refused(_tar_gz(members), tmp_path) == "bundle_has_no_origin"


@pytest.mark.parametrize("listing", [f"#origin\t{ORIGIN}\n{CRAM}\tbig\n",
                                     f"#origin\t{ORIGIN}\n/abs/{CRAM}\t1\n",
                                     f"#origin\t{ORIGIN}\n../{CRAM}\t1\n",
                                     f"#origin\t{ORIGIN}\n{CRAM}\n"])
def test_a_malformed_listing_is_refused(tmp_path, listing):
    members = [m for m in _basic() if m[0] != bundle.LISTING_NAME] + [(bundle.LISTING_NAME, listing.encode())]
    assert _refused(_tar_gz(members), tmp_path) == "bundle_listing_malformed"


@pytest.mark.parametrize("origin", ["relative/path", "", "s3://x\x07y"])
def test_the_origin_must_be_an_absolute_path_or_a_url(tmp_path, origin):
    members = [m for m in _basic() if m[0] != bundle.LISTING_NAME] + [
        (bundle.LISTING_NAME, f"#origin\t{origin}\n".encode())]
    assert _refused(_tar_gz(members), tmp_path) in ("bundle_has_no_origin", "bundle_listing_malformed")


def test_listing_lines_reingest_does_not_list_are_ignored(tmp_path):
    listing = LISTING + "reports/S1.html\t10\nfastp/S1.json\t20\n"
    members = [m for m in _basic() if m[0] != bundle.LISTING_NAME] + [(bundle.LISTING_NAME, listing.encode())]
    out, _ = _stage(_tar_gz(members), tmp_path)
    assert [e["path"] for e in out.inventory] == [CRAM]


def test_the_inventory_cap_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest, "MAX_INVENTORY_FILES", 1)
    listing = LISTING + "preprocessing/recalibrated/S2/S2.recal.cram\t5\n"
    members = [m for m in _basic() if m[0] != bundle.LISTING_NAME] + [(bundle.LISTING_NAME, listing.encode())]
    out, _ = _stage(_tar_gz(members), tmp_path)
    assert len(out.inventory) == 1
    assert any("max inventory file count" in s["reason"] for s in out.skipped)


def test_the_newest_execution_reports_verdict_is_read_and_no_report_is_unpacked(tmp_path):
    old = b"<p>Workflow execution completed unsuccessfully</p>"
    new = b"<html>\n<p>Workflow execution completed successfully, no errors</p>\n</html>"
    _, staged = _stage(_tar_gz(_basic(more=[("pipeline_info/execution_report_2026-01-01.html", old),
                                              ("pipeline_info/execution_report_2026-02-01.html", new)])),
                         tmp_path)
    assert not list(staged.glob("pipeline_info/execution_report*"))
    verdict = json.loads((staged / harvest.RUN_VERDICT_NAME).read_text())
    assert verdict == {"path": "pipeline_info/execution_report_2026-02-01.html",
                       "verdict": "Workflow execution completed successfully, no errors"}


def test_a_member_with_the_verdicts_reserved_name_is_skipped(tmp_path):
    forged = json.dumps({"path": "x", "verdict": "Workflow execution completed successfully"}).encode()
    out, staged = _stage(_tar_gz(_basic(more=[(harvest.RUN_VERDICT_NAME, forged)])), tmp_path)
    assert not (staged / harvest.RUN_VERDICT_NAME).exists()
    assert any(s["path"] == harvest.RUN_VERDICT_NAME for s in out.skipped)


def _fixture_bundle(folder: Path) -> bytes:
    """A bundle of a committed run fixture: its files, and its inventory as the listing."""
    inventory = json.loads((folder / "inventory.json").read_text())
    listing = f"#origin\t{ORIGIN}\n" + "".join(f"{e['path']}\t{e['bytes']}\n" for e in inventory)
    members = [(str(p.relative_to(folder)), p.read_bytes()) for p in sorted(folder.rglob("*"))
               if p.is_file() and p.name not in ("inventory.json", "launch_record.json", "snapshot.json",
                                                 "EVIDENCE.txt")]
    return _tar_gz([*members, (bundle.LISTING_NAME, listing.encode())])


@pytest.mark.django_db
def test_a_bundle_of_the_real_sarek_aws_run_harvests_like_its_folder(tmp_path):
    out, staged = _stage(_fixture_bundle(SAREK_AWS), tmp_path)
    run = harvest.harvest_local(str(staged), run_dir=out.origin, inventory=out.inventory)
    folder_run = harvest.harvest_local(
        str(SAREK_AWS), run_dir=out.origin,
        inventory=json.loads((SAREK_AWS / "inventory.json").read_text()))
    assert run.pipeline == folder_run.pipeline
    assert [s.model_dump() for s in run.samples] == [s.model_dump() for s in folder_run.samples]
    listed = {e["path"] for e in json.loads((SAREK_AWS / "inventory.json").read_text())
              if bundle._matches(e["path"], harvest.inventory_globs())}
    assert listed and listed <= {o.path for o in run.outputs}  # the big files, from the listing
    evidence = g._foreign_run_evidence(run, SimpleNamespace(is_superuser=True, pk=3))
    assert evidence.samplesheet_source == "samplesheet.csv"


def test_make_bundle_writes_a_bundle_the_stager_reads_like_the_folder(tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[4] / "scripts" / "reingest" / "make_bundle.py"
    spec = importlib.util.spec_from_file_location("make_bundle", script)
    make_bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_bundle)
    listing = tmp_path / "listing.tsv"
    listing.write_text("".join(f"{e['path']}\t{e['bytes']}\n"
                               for e in json.loads((SAREK_AWS / "inventory.json").read_text())))
    out = tmp_path / "sarek.tar.gz"
    make_bundle.build(SAREK_AWS, ORIGIN, out, listing=listing)
    made, made_dir = _stage(out.read_bytes(), tmp_path)
    (tmp_path / "ref").mkdir()
    reference, reference_dir = _stage(_fixture_bundle(SAREK_AWS), tmp_path / "ref")
    assert made.origin == ORIGIN
    assert made.inventory == reference.inventory
    def files(root):
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert files(made_dir) == files(reference_dir)


# --- the op -------------------------------------------------------------------------------

ADMIN = SimpleNamespace(is_superuser=True, pk=3)
CURATOR = SimpleNamespace(is_superuser=False, pk=7)


class _NoLuria:
    LURIA_ENV = None


def _session(user, data: bytes | None = None, *, opened: list | None = None, missing=False):
    def open_bundle(name):
        if opened is not None:
            opened.append(name)
        if missing:
            raise FileNotFoundError(name)
        return io.BytesIO(data)
    return SimpleNamespace(user=user, open_bundle=open_bundle)


def _op(session, **args):
    return g.run_op("run-harvest", {"bundle": "sarek.tar.gz", **args}, config=_NoLuria(), session=session,
                    write_gate=None, neo4j_exec=None, outputs_dir=None)


@pytest.fixture
def stores(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "_ROOT", str(tmp_path / "manifests"))
    monkeypatch.setattr(g, "_d_seq_by_fastq", lambda path, types=("D.SEQ",): [])
    monkeypatch.setattr(g, "_sample_types_for_uids", lambda uids: {})


@pytest.mark.django_db
def test_a_non_admin_is_refused_before_the_bundle_is_opened(stores):
    opened: list = []
    with pytest.raises(g.OpValidationError) as err:
        _op(_session(CURATOR, b"", opened=opened))
    assert err.value.error_type == "foreign_run_needs_an_admin"
    assert opened == []


@pytest.mark.django_db
def test_a_bundle_and_a_run_dir_together_are_refused(stores):
    with pytest.raises(g.OpValidationError) as err:
        _op(_session(ADMIN, b""), run_dir="/net/cluster/runs/x")
    assert err.value.error_type == "bundle_or_run_dir"


@pytest.mark.django_db
def test_an_admins_bundle_is_harvested_as_a_foreign_run_without_luria(stores):
    from nextseek_api.assistant.models_db import PipelineRun

    data = _fixture_bundle(SAREK_AWS)
    opened: list = []
    out = _op(_session(ADMIN, data, opened=opened))
    assert opened == ["sarek.tar.gz"]
    run = store_mod.load_manifest(out["manifest_id"])
    assert run.run_dir == ORIGIN and out["run_dir"] == ORIGIN
    assert run.launch_record_id is None and run.test_only is False
    assert run.foreign_run.bundle.model_dump() == {
        "name": "sarek.tar.gz", "sha256": hashlib.sha256(data).hexdigest(), "origin": ORIGIN}
    assert PipelineRun.objects.count() == 0


@pytest.mark.django_db
def test_a_missing_bundle_is_named_as_such(stores):
    with pytest.raises(g.OpValidationError) as err:
        _op(_session(ADMIN, missing=True))
    assert (err.value.field, err.value.error_type) == ("bundle", "bundle_not_found")


@pytest.mark.django_db
def test_a_refused_bundle_reaches_the_agent_as_a_bundle_error(stores):
    with pytest.raises(g.OpValidationError) as err:
        _op(_session(ADMIN, b"not a bundle"))
    assert (err.value.field, err.value.error_type) == ("bundle", "bundle_not_a_tar_gz")


@pytest.mark.django_db
def test_the_foreign_evidence_rules_still_apply_to_a_bundle(stores):
    members = [(n, d) for n, d in _basic() if n != "samplesheet.csv"]
    with pytest.raises(g.OpValidationError) as err:
        _op(_session(ADMIN, _tar_gz(members)))
    assert err.value.error_type == "foreign_run_names_no_pipeline"


def test_the_build_header_names_the_bundle_and_its_stated_origin():
    run = manifest_mod.RunManifest(
        run_dir=ORIGIN, pipeline=manifest_mod.PipelineInfo(name="nf-core/sarek", version="3.10.0"),
        execution=manifest_mod.ExecutionInfo(processes=1), samples=[],
        foreign_run=manifest_mod.ForeignRun(
            harvested_by_user_id=3, harvested_at="2026-11-16T00:00:00+00:00",
            pipeline_source=VERSIONS, samplesheet_source="samplesheet.csv",
            bundle=manifest_mod.BundleSource(name="sarek.tar.gz", sha256="ab" * 32, origin=ORIGIN)))
    header = g._foreign_run_header(run)
    assert header.startswith(
        f"NOT LAUNCHED BY NESSIE: nf-core/sarek 3.10.0, read from {VERSIONS} in the chat bundle "
        f"sarek.tar.gz (its file list says the files are at {ORIGIN}); samples matched to their parents "
        "through samplesheet.csv.")


def test_a_folder_runs_header_is_unchanged():
    run = manifest_mod.RunManifest(
        run_dir="/RUN", pipeline=manifest_mod.PipelineInfo(name="nf-core/sarek", version="3.10.0"),
        execution=manifest_mod.ExecutionInfo(processes=1), samples=[],
        foreign_run=manifest_mod.ForeignRun(
            harvested_by_user_id=3, harvested_at="2026-11-16T00:00:00+00:00",
            pipeline_source=VERSIONS, samplesheet_source="samplesheet.csv"))
    assert g._foreign_run_header(run).startswith(
        f"NOT LAUNCHED BY NESSIE: nf-core/sarek 3.10.0, read from {VERSIONS}; samples matched")


@pytest.mark.django_db
def test_run_checksum_refuses_a_bundle_manifest(stores, monkeypatch):
    out = _op(_session(ADMIN, _fixture_bundle(SAREK_AWS)))

    cfg = SimpleNamespace(LURIA_ENV={"working_path": "/net/cluster", "key": "/dev/null", "user": "u", "host": "h"})

    run = store_mod.load_manifest(out["manifest_id"])
    run.run_dir = "/net/cluster/runs/aws"  # even where its stated origin is a cluster folder
    manifest_id = store_mod.save_manifest(run)
    with pytest.raises(g.OpValidationError) as err:
        g.run_op("run-checksum", {"run_dir": "/net/cluster/runs/aws", "paths": CRAM, "manifest_id": manifest_id},
                 config=cfg, session=_session(ADMIN), write_gate=None, neo4j_exec=None, outputs_dir=None)
    assert err.value.error_type == "bundle_has_no_cluster_files"
