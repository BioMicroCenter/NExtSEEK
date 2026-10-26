"""Phase 4 uid guard: app, cc-agent and ns-sidecar share ONE uid/gid (10001) because Claude
Code writes 0600 transcripts into the shared dmac-cc-users volume that the app must read.
The bedrock proxy shares no files with them and takes 10002 (a uid with no host account).
Hermetic: stdlib only. The app-side checks skip until the app Dockerfile / startup/lib/ownership.py
from the other phase-4 packets are merged (the integrator un-skips them by merging, nothing to edit).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CC_UID, PROXY_UID = 10001, 10002


def _useradd(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.relative_to(ROOT)} is not in this tree (image lane strips it)")
    m = re.search(r"^RUN\s+useradd\b[^\n]*$", path.read_text(), re.M)
    assert m, f"{path} has no useradd line"
    return m.group(0)


def _assert_uid_gid(line: str, uid: int):
    assert re.search(rf"-u {uid}\b", line), line
    # the group is created with the same number: -U (user group, gid = uid) or an explicit -g
    assert re.search(rf"(\s-U\b|-g {uid}\b)", line), line


@pytest.mark.parametrize("rel", ["NessieAI/docker/cc-runtime/Dockerfile", "NessieAI/docker/ns-sidecar/Dockerfile"])
def test_cc_images_use_the_shared_uid(rel):
    _assert_uid_gid(_useradd(ROOT / rel), CC_UID)


def test_bedrock_proxy_has_its_own_uid():
    _assert_uid_gid(_useradd(ROOT / "NessieAI/docker/bedrock-proxy/Dockerfile"), PROXY_UID)


def test_bedrock_proxy_shares_no_volume():
    compose = ROOT / "docker-compose.yml"
    if not compose.is_file():
        pytest.skip("docker-compose.yml is not in this tree")
    block = re.search(r"^  bedrock-proxy:\n(.*?)(?=^  \S)", compose.read_text(), re.M | re.S)
    assert block, "bedrock-proxy service not found"
    assert not re.search(r"^\s{4}(volumes|tmpfs):", block.group(1), re.M)


def test_app_dockerfile_uses_the_shared_uid():
    df = ROOT / "Dockerfile"
    if not df.is_file() or "useradd" not in df.read_text():
        pytest.skip("app Dockerfile non-root user is on the P1 branch (cm/w9-phase4-image-compose-non-root)")
    _assert_uid_gid(_useradd(df), CC_UID)


def test_ownership_app_uid_is_the_shared_uid():
    mod = ROOT / "startup/lib/ownership.py"
    if not mod.is_file():
        pytest.skip("startup/lib/ownership.py is on the P2 branch (cm/w9-phase4-startup-ownership-pass)")
    assert re.search(rf"^APP_UID\s*=\s*(APP_GID\s*=\s*)?{CC_UID}\b", mod.read_text(), re.M)
