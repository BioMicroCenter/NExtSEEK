from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path

from nextseek_api.attributes.tests.real_boundary import DisposableAttributeDatabase

DEPENDENCIES = {
    "task-00": (), "task-01": ("task-00",), "task-02": ("task-01",),
    "task-03": ("task-00", "task-01"), "task-04": ("task-01", "task-03"),
    "task-05": ("task-01", "task-02", "task-03", "task-04"),
    "task-06": ("task-00", "task-03"), "task-07": ("task-03", "task-05", "task-06"),
    "task-08": ("task-02", "task-03", "task-07"),
    "task-09": ("task-02", "task-04", "task-05", "task-07", "task-08"),
    "task-10": ("task-09",), "task-11": ("task-10",),
}


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def atomic_new_json(path, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def dependencies(repo, task):
    head = git(repo, "rev-parse", "HEAD")
    output = []
    for dependency in DEPENDENCIES[task]:
        matches = git(repo, "log", head, "--format=%H", "--grep", f"^Attribute-Task: {dependency}$").splitlines()
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one ancestor trailer for {dependency}")
        output.append({"task_id": dependency, "sha": matches[0]})
    return output


def prepare(args):
    database_container = os.environ.get("ATTRIBUTE_TEST_DB_CONTAINER")
    if not database_container:
        raise RuntimeError("ATTRIBUTE_TEST_DB_CONTAINER is required; an external/shared DB boundary is forbidden")
    network = os.environ["ATTRIBUTE_TEST_DOCKER_NETWORK"]
    network_id = os.environ["ATTRIBUTE_TEST_NETWORK_ID"]
    database = None
    try:
        database = DisposableAttributeDatabase.from_environment()
        database.detach_django_alias()
        identity = {
            "server_identity": database.server_identity, "database_uuid": database.database_uuid,
            "database_name": database.database_name, "network_name": network,
            "network_id": network_id, "database_container": database_container,
            "torn_down": False,
        }
        atomic_new_json(Path(args.identity), identity)
        atomic_new_json(Path(args.dependencies), dependencies(Path(args.repo), args.task))
        values = {
            "ATTRIBUTE_TEST_DOCKER_NETWORK": network,
            "ATTRIBUTE_TEST_DATABASE_NAME": database.database_name,
            "ATTRIBUTE_TEST_DISPOSABLE_DB_UUID": database.database_uuid,
            "ATTRIBUTE_TEST_DB_HOST": database_container,
            "ATTRIBUTE_TEST_DB_PORT": os.environ.get("ATTRIBUTE_TEST_DB_PORT", "3306"),
        }
        descriptor = os.open(args.env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            for key, value in values.items():
                stream.write(f"export {key}={shlex.quote(str(value))}\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if database is not None:
            database.teardown()
        raise


def finalize(args):
    path = Path(args.identity)
    identity = json.loads(path.read_text())
    if identity.get("torn_down"):
        raise RuntimeError("boundary already finalized")
    database = DisposableAttributeDatabase.owner_from_identity(identity)
    database.teardown()
    database.assert_torn_down()
    if os.environ.get("ATTRIBUTE_TEST_NETWORK_ID") != identity["network_id"]:
        raise RuntimeError("disposable network identity changed")
    identity["torn_down"] = True
    identity["teardown_server_uuid"] = database.server_identity["server_uuid"]
    temporary = path.with_name(f".{path.name}.final-{uuid.uuid4()}")
    atomic_new_json(temporary, identity)
    os.replace(temporary, path)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--repo", required=True)
    prepare_parser.add_argument("--task", required=True)
    prepare_parser.add_argument("--run-root", required=True)
    prepare_parser.add_argument("--identity", required=True)
    prepare_parser.add_argument("--dependencies", required=True)
    prepare_parser.add_argument("--env-file", required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--identity", required=True)
    finalize_parser.add_argument("--run-root", required=True)
    args = parser.parse_args()
    return prepare(args) if args.command == "prepare" else finalize(args)


if __name__ == "__main__":
    main()
