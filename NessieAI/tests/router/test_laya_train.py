"""scripts/laya/train.sh guards (JevLevROUTING SPEC s8). The runs stop at a refusal, never at an install."""
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[3]
TRAIN = REPO / "scripts/laya/train.sh"


def test_train_refuses_a_workspace_inside_any_git_repo(tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", str(other)], check=True, capture_output=True)
    out = tmp_path / "exists"
    out.mkdir()  # the next refusal: without the workspace check the run still stops before any install
    r = subprocess.run(["bash", str(TRAIN), "view.jsonl", str(out)], cwd=tmp_path, capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LAYA_WORKSPACE": str(other / "work")})
    assert r.returncode == 2 and "outside every git repo" in r.stderr, r.stderr
