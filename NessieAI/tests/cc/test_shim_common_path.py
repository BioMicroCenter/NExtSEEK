"""P12: every nextseek-* shim runs a bare `python`; the common file must put the image venv first on PATH."""
import subprocess
from pathlib import Path

COMMON = (Path(__file__).resolve().parents[2]
          / "docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_common.sh")


def test_the_shim_common_file_puts_the_image_venv_first_on_path():
    out = subprocess.run(
        ["sh", "-c", f'PATH=/usr/bin:/bin; . "{COMMON}"; printf %s "$PATH"'],
        capture_output=True, text=True, check=True).stdout
    assert out.split(":")[0] == "/opt/dmac-venv/bin"
