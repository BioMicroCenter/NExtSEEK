# Installation

NExtSEEK runs as one Docker Compose stack: the NExtSEEK app, a companion SEEK instance, MySQL, Neo4j and the services behind the Nessie assistant. One command, `./startup.sh`, brings it all up. Installing SEEK and NExtSEEK by hand is no longer supported.

!!! note
    This guide sets up a **local demo**: well-known passwords and no TLS, with the chat assistant switched off until you add keys. Before anyone else can reach it, follow [Production setup](production-setup.md).

## What you need

* Docker Engine 26 or newer, with the Compose plugin 2.26 or newer
* [`uv`](https://docs.astral.sh/uv/), the Python package manager that runs the startup tool
* About 60 GB of free disk for a comfortable single instance (images, seed data and build cache)
* 8 GB of free RAM or more
* Internet access to pull images, download the SEEK file seed (about 215 MB) and, the first time you use chat, download embedding models

Check Docker with `docker version` and `docker compose version`.

## Quick start

```bash
git clone -b main https://github.com/BioMicroCenter/NExtSEEK.git
cd NExtSEEK
./startup.sh install
```

`install` shows a summary and asks you to confirm. Add `--yes` to skip the question.

Then open <http://localhost:8000> and sign in with `demo` / `demopassword` (administrator) or `user` / `userpassword` (regular user).

If port 8000 is busy, the installer picks the next free port and tells you which.

## What runs

`install` starts these services (ports are on localhost only):

| Service | What it is | Port |
|---|---|---|
| `nextseek` | The Django app, with its background workers | behind nginx |
| `nextseek_nginx` | Static files and reverse proxy | 8000 |
| `db` | MySQL 8.0 with two schemas: `dmac` (NExtSEEK) and `seek_production` (SEEK) | 3306 |
| `neo4j` | The sample graph | 7474 and 7687 |
| `seek` and `seek_workers` | The companion FAIRDOM-SEEK app and its workers | 3000 |
| `solr` | SEEK's search index | none |
| `bedrock-proxy`, `nextseek-sidecar`, `cc-agent` | Services behind the Nessie assistant (see [Nessie](nessie.md)) | none |

## What install does

Nine phases, in order:

1. Check prerequisites.
2. Verify the vendored code.
3. Resolve the instance name and ports.
4. Render the config files.
5. Create the Docker volumes.
6. Seed MySQL and Neo4j from the committed dumps (skipped if they already hold data).
7. Build the images and start the stack.
8. Verify the demo users.
9. Run health checks.

Running it again keeps your data: the volumes stay, and each seed is skipped when its database already holds data. It does write the three config files below again from their templates, so your edits to them are lost and the Django secret key changes. Redo those edits afterwards.

## Configuration

Install writes three files for you to edit. All are ignored by git:

| File | Holds |
|---|---|
| `docker/db.env` | MySQL credentials |
| `docker/nextseek.env` | The Django secret key, the Neo4j password and the LLM API keys (chat stays off until you add keys) |
| `dmac/local_settings.py` | Django settings overlay |

Edit a file, then apply it with `docker compose up -d --no-deps --force-recreate nextseek`.

!!! warning
    Do not use `./startup.sh reset` to re-render config. `reset` drops the Docker volumes and re-seeds, which erases your data. It also writes the config files back to the demo values, even with `--keep-config`.

## Day-to-day commands

| Command | What it does |
|---|---|
| `./startup.sh doctor` | Read-only check of prerequisites and health. Run this first when something is wrong |
| `./startup.sh rebuild` | Rebuild and restart the app image without touching data, then run the smoke tests (`--no-ci` skips them). Pass `--component` to pick another: `app` (the default), `cc-agent`, `nextseek-sidecar`, `bedrock-proxy` or `custom-stack` |
| `./startup.sh reset` | **Destructive.** Drops the volumes, re-seeds and writes the config files again (also with `--keep-config`) |
| `./startup.sh ci` | Run the smoke test suite against the running stack |
| `./startup.sh seed-filestore` | Load the SEEK file blobs into a running stack (skipped if the volume already has files, unless you pass `--force`) |
| `./startup.sh dump-db` | For maintainers: regenerate the seed dumps |

To run a second, separate copy beside the first, use `./startup.sh install --instance test`. Each instance gets its own volumes and free ports. The Nessie assistant's containers, volume and network have fixed names, so only one instance per machine can run the assistant.

## Next steps

If the instance will be reachable by anyone but you, go to [Production setup](production-setup.md). To learn how to use NExtSEEK once it is running, start at the [Overview](overview.md).
