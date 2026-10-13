"""Load the three seeds into fresh <prefix>-verify-* containers with install's own code (startup/steps/seed.py), in
install's order (startup/cli.py phase 6): seed_files_present, then dmac, seek_production, neo4j, each gated by its
populated check. Only the docker transport is swapped: compose_exec -> docker exec on the verify container, and
compose_port -> the verify container's published bolt port.
Usage (startup venv): verify.py ROOT   where ROOT/startup/seed/ holds the three seeds.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))
from startup.lib.docker_ops import DockerOpsError  # noqa: E402
from startup.steps import seed  # noqa: E402

import seedlib as s  # noqa: E402

CONTAINERS = {"db": f"{s.PREFIX}-verify-mysql", "neo4j": f"{s.PREFIX}-verify-neo4j"}
NEO4J_PW = s.NEO4J_PW


def fake_exec(service, command, project_dir, env, interactive=False, stdin=None):
    p = subprocess.run(["docker", "exec", "-i", CONTAINERS[service], *command], input=stdin, capture_output=True)
    if p.returncode != 0:
        raise DockerOpsError(f"exec {service} failed: {p.stderr.decode()[:2000]}")
    return p.stdout.decode()


seed.compose_exec = fake_exec
seed.compose_port = lambda service, port, project_dir, env: int(os.environ.get("VERIFY_NEO4J_PORT", "17688"))


def main(root):
    repo = Path(root)
    env = {"MYSQL_ROOT_PASSWORD": s.PW}
    missing = seed.seed_files_present(repo)
    assert not missing, f"missing seed files: {missing}"
    for db in ("dmac", "seek_production"):
        if seed.mysql_db_is_populated(db, repo, env):
            print(f"{db} already populated; skipping")
        else:
            t = time.time()
            seed.load_mysql_dump(repo / "startup" / "seed" / f"{db}.sql.gz", db, repo, env)
            print(f"{db} loaded in {time.time() - t:.0f}s")
    if seed.neo4j_is_populated(NEO4J_PW, repo, env):
        print("neo4j already populated; skipping")
    else:
        t = time.time()
        seed.load_neo4j_dump(repo / "startup" / "seed" / "neo4j.cypher.gz", NEO4J_PW, repo, env)
        print(f"neo4j loaded in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1])
