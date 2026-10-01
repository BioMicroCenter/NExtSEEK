# NExtSEEK

A Django/Mezzanine extension of the FAIRDOM SEEK platform for active scientific data curation,
with a graph-backed sample database (Neo4j) and an embedded AI assistant, Nessie, for
natural-language questions about samples, assays and projects. This repo brings up the whole
stack in Docker.

## Quick start

```bash
git clone https://github.com/BioMicroCenter/NExtSEEK.git
cd NExtSEEK
./startup.sh install
```

Open http://localhost:8000 and log in with `demo / demopassword` (admin) or
`user / userpassword` (regular).

`./startup.sh doctor` runs every prerequisite and health check when something is wrong.

Requirements: Docker Engine 26+ with the Compose plugin 2.26+, and [`uv`](https://docs.astral.sh/uv/).
Disk, RAM and network needs are in [`DEPLOYMENT.md`](DEPLOYMENT.md) §2.1.

> **Going beyond localhost?** Read [`NExtSTEPS.md`](NExtSTEPS.md) first. Rotating the demo
> passwords is the minimum.

## Where to read next

| You want to | Read |
|---|---|
| Deploy, redeploy, roll back or verify a real instance | [`DEPLOYMENT.md`](DEPLOYMENT.md) |
| Harden an install before exposing it | [`NExtSTEPS.md`](NExtSTEPS.md) |
| Understand the repo: every folder, skill and sub-doc | [`CLAUDE.md`](CLAUDE.md) |
| Understand the AI assistant | [`NessieAI/README.md`](NessieAI/README.md) |
| Read or edit the user docs (the site's `/docs/` pages) | [`themes/NextSeek/docs/README.md`](themes/NextSeek/docs/README.md) |
| Find a cross-cutting doc | [`docs/INDEX.md`](docs/INDEX.md) |
| Report a bug or request a feature | [`docs/ISSUE-CONVENTIONS.md`](docs/ISSUE-CONVENTIONS.md) |
| Look up a `./startup.sh` subcommand | [`startup/README.md`](startup/README.md) |

## License

MIT. See [`LICENSE`](LICENSE).
