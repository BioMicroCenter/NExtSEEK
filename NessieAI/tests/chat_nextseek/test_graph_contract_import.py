"""chat_nextseek finds the graph contract where the checkout root is not importable (``graph_contract.py``).

Step 1 (``from nextseek_graph import schema``) is what every other test in this lane exercises. These two start a
fresh interpreter whose path holds only a chat_nextseek source tree and whose working directory is outside the
checkout, as the evaluator runs:

- over the real source tree, ``schema.py`` is loaded by its path from the checkout four levels up, and
  ``nextseek_graph`` itself is never imported or registered (step 2);
- over a copy of the package with no ``nextseek_graph/`` four levels up, the ImportError names the remedy (step 3).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "NessieAI" / "chat_nextseek" / "src"

PROBE = (
    "import sys\n"
    "from chat_nextseek.graph_contract import schema\n"
    "print(schema.SCHEMA_VERSION)\n"
    "print('nextseek_graph' in sys.modules)\n"
)


def _run(src: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(src), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-c", PROBE], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=60)


def test_the_contract_loads_by_path_when_the_checkout_root_is_not_importable(tmp_path):
    result = _run(SRC, tmp_path)
    assert result.returncode == 0, result.stderr
    version, registered = result.stdout.split()
    expected = (REPO_ROOT / "nextseek_graph" / "schema.py").read_text(encoding="utf-8")
    assert f'SCHEMA_VERSION: Final[str] = "{version}"' in expected
    assert registered == "False"


def test_without_a_contract_the_import_error_names_the_remedy(tmp_path):
    fake_src = tmp_path / "NessieAI" / "chat_nextseek" / "src"
    package = fake_src / "chat_nextseek"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    shutil.copy(SRC / "chat_nextseek" / "graph_contract.py", package / "graph_contract.py")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = _run(fake_src, outside)
    assert result.returncode != 0
    assert "ImportError" in result.stderr
    assert "nextseek_graph/schema.py" in result.stderr
    assert "checkout root on sys.path" in result.stderr
    assert "app image built from a commit that has nextseek_graph/" in result.stderr


# Runs graph_contract.py's source as if the file sat at the file system root, where no checkout can be around it.
SHALLOW_PROBE = (
    "import sys\n"
    "namespace = {'__name__': 'shallow_graph_contract', '__file__': '/graph_contract.py'}\n"
    "try:\n"
    "    exec(compile(open(sys.argv[1], encoding='utf-8').read(), '/graph_contract.py', 'exec'), namespace)\n"
    "except ImportError as exc:\n"
    "    print('ImportError:', exc)\n"
    "else:\n"
    "    print('loaded', namespace['schema'].SCHEMA_VERSION)\n"
)


def test_where_the_file_sits_changes_neither_step_1_nor_step_3(tmp_path):
    source = str(SRC / "chat_nextseek" / "graph_contract.py")

    def run(pythonpath: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(pythonpath), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, "-c", SHALLOW_PROBE, source], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=60)

    # The checkout root on the path: step 1 imports the contract, whatever the file's depth.
    importable = run(REPO_ROOT)
    assert importable.returncode == 0 and importable.stdout.startswith("loaded "), importable.stderr
    # No contract anywhere and no checkout around the file: step 3's message.
    absent = run(tmp_path)
    assert absent.returncode == 0 and absent.stdout.startswith("ImportError:"), absent.stderr
    assert "checkout root on sys.path" in absent.stdout
