# NExtSEEK

Extending SEEK for active management of scientific metadata.

## License

Copyright (c) 2021, BioMicro Center & Bioinformatics Core, Massachusetts Institute of Technology: [MIT license](LICENSE)

## Table of Contents

- [About NExtSEEK](#about-nextseek)
- [Repository Layout](#repository-layout)
- [Quick Start](#quick-start)
- [Local Docker Setup](#local-docker-setup)
- [Configuration](#configuration)
- [Runtime Notes and Troubleshooting](#runtime-notes-and-troubleshooting)
- [Developer Documentation](#developer-documentation)
- [UI Reference](#ui-reference)
- [Release Notes](#release-notes)
- [References](#references)
- [Checklist for Preparing to Use NExtSEEK](#checklist-for-preparing-to-use-nextseek)
- [Contact Us](#contact-us)

## About NExtSEEK

NExtSEEK is a modified wrapper around the [SEEK](https://github.com/seek4science/seek) platform that allows active data management by establishing more discrete sample types which are mutable to permit the expansion of the types of metadata, allowing researchers to track additional information. The use of discrete nodes also converts assays from nodes to edges, creating a network model of the study, and more accurately representing the experimental process. With these changes to SEEK, users are able to collect and organize the information that researchers need to improve reusability and reproducibility as well as to make data and metadata available to the scientific community through public repositories.

This repository contains:

- the Django/Mezzanine NExtSEEK application
- the repo-local Docker stack for SEEK, NExtSEEK, MySQL, Solr, nginx, and Neo4j
- the `themes/NextSeek/` Django theme used by the current local Docker setup
- the React chat frontend in `chat_frontend/`

The `chat_nextseek/` dependency is a separate repository cloned locally into this repo for Docker and local development. It is not vendored into this repository. It is currently a private repository; if you don't have access, request the directory contents directly. When it goes public, the install can switch back to a pinned git source (see the comment in `pyproject.toml` under `[tool.uv.sources]`).

## Repository Layout

- `seek/` - Core application: sample management, search, SEEK integration
- `dmac/` - Django project settings, URL routing, and local overrides
- `nextseek_api/` - API module for assistant and batch upload features
- `api_app/` - Legacy API endpoints
- `themes/NextSeek/` - Current Django theme templates and static assets
- `chat_frontend/` - Separate React/Vite chat frontend
- `chat_nextseek/` - Locally-cloned assistant backend (private dep, installed via `[tool.uv.sources]` local path)
- `docker/` - Environment files, nginx config, and startup scripts
- `data/` - Seed assets used by features (e.g. `seq_template.xlsx`, `geo.json`)
- `scripts/` - Repo-local helpers (`post_uv_sync.sh`, `test_batch_upload_e2e.py`)
- `bootstrap.md` - Planning note for a future one-command bootstrap workflow
- `UI.md` - Route, view, and template reference for the current UI
- `CLAUDE.md` - Developer-oriented architecture and workflow notes

## Quick Start

For the current local Docker workflow, run everything from the repository root:

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
```

Clone this repository and the local `chat_nextseek` dependency:

```bash
git clone git@github.com:BMCBCC/NExtSEEK.git
cd NExtSEEK
git clone git@github.com:cdemurjian/chat_nextseek.git chat_nextseek
```

Create the local override file:

```bash
cp dmac/local_settings.example.py dmac/local_settings.py
```

Review and adjust:

- `docker/db.env`
- `docker/nextseek.env`
- `dmac/local_settings.py`

Create required Docker volumes:

```bash
docker volume create seek-filestore
docker volume create seek-mysql-db
docker volume create seek-solr-data
docker volume create seek-cache
docker volume create nextseek-static-files
docker volume create neo4j-data
```

Start the stack:

```bash
docker compose up -d
```

Default local URLs:

- SEEK: `http://127.0.0.1:3000`
- NExtSEEK: `http://127.0.0.1:8000`
- Neo4j browser: `http://127.0.0.1:7474`

See [Local Docker Setup](#local-docker-setup) for the full import and rebuild workflow.

## Local Docker Setup

This section documents the current local Docker workflow used with the repo-local [docker-compose.yml](docker-compose.yml).

Important assumptions:

- run `docker compose` from `NExtSEEK/`, not the parent `docker/` directory
- use the compose file inside this repository
- `chat_nextseek/` must exist as a local sibling directory under `NExtSEEK/`
- the current theme is `themes/NextSeek/`; SmartAdmin is no longer part of the expected setup
- the local Docker environment uses `docker/db.env` and `docker/nextseek.env` for host, CSRF, and database settings

### Files That Matter

- [docker-compose.yml](docker-compose.yml)
- [Dockerfile](Dockerfile)
- [dmac/settings.py](dmac/settings.py)
- [dmac/local_settings.py](dmac/local_settings.py)
- [docker/db.env](docker/db.env)
- [docker/nextseek.env](docker/nextseek.env)
- [docker/nginx.conf](docker/nginx.conf)
- [docker/scripts/entrypoint.sh](docker/scripts/entrypoint.sh)
- [pyproject.toml](pyproject.toml)
- [uv.lock](uv.lock)

Database import files used by the documented workflow live outside this repo under:

- `/home/cdemu/code/dmac/docker/resources/dmac_dev_dump.sql`
- `/home/cdemu/code/dmac/docker/resources/seek_production_dump.sql`
- `/home/cdemu/code/dmac/docker/resources/neo4j_export_clean.cypher`

### Preconditions

#### 1. Docker installed and working

```bash
docker --version
docker compose version
```

#### 2. `chat_nextseek` cloned into the repo

This repo depends on a local path package:

```bash
/home/cdemu/code/dmac/docker/NExtSEEK/chat_nextseek/
```

Example setup:

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git clone git@github.com:cdemurjian/chat_nextseek.git chat_nextseek
```

#### 3. Local override file present

The Docker setup still expects `dmac/local_settings.py` to exist and contain runtime overrides.

Minimal working example:

```python
SEEK_URL = "http://seek:3000"
PUBLISH_URL = SEEK_URL

ASSISTANT_PARTICIPATING_PROJECTS = set(["1"])
TEST_CASES = {}

SAMPLE_TEMPLATES_FOLDER = "/templates"
SAMPLE_TEMPLATES_FOLDER_PROJECT = "1"
PUBLISH_STATS_FILE = "/media/reserved/published_stats_production.xlsx"
SMART_SEARCH_URL = ""

os.makedirs(os.path.join(MEDIA_ROOT, "download"), exist_ok=True)
```

### Required Local Directories

Create the host directories used by the container:

```bash
mkdir -p /home/cdemu/code/dmac/docker/NExtSEEK/logs
mkdir -p /home/cdemu/code/dmac/docker/NExtSEEK/outputs
```

### One-Time Docker Volume Setup

```bash
docker volume create seek-filestore
docker volume create seek-mysql-db
docker volume create seek-solr-data
docker volume create seek-cache
docker volume create nextseek-static-files
docker volume create neo4j-data
```

### Reset From Scratch

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose down --remove-orphans
docker volume rm seek-filestore seek-mysql-db seek-solr-data seek-cache nextseek-static-files neo4j-data
docker volume create seek-filestore
docker volume create seek-mysql-db
docker volume create seek-solr-data
docker volume create seek-cache
docker volume create nextseek-static-files
docker volume create neo4j-data
```

### Database Import Order

#### 1. Start MySQL only

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose up -d db
sleep 20
docker compose ps
```

#### 2. Import MySQL dumps

```bash
(echo "SET foreign_key_checks=0;"; cat /home/cdemu/code/dmac/docker/resources/dmac_dev_dump.sql) | docker exec -i seek-mysql mysql -u seek_db_user -p'seek_db_password' dmac
(echo "SET foreign_key_checks=0;"; cat /home/cdemu/code/dmac/docker/resources/seek_production_dump.sql) | docker exec -i seek-mysql mysql -u seek_db_user -p'seek_db_password' seek_production
```

If you already have older local schemas named `nextseek` and `seek_docker`, migrate them once:

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose exec db mysql -uroot -pseek_root -e "CREATE DATABASE IF NOT EXISTS dmac; CREATE DATABASE IF NOT EXISTS seek_production;"
docker compose exec db sh -lc 'mysqldump -uroot -pseek_root nextseek | mysql -uroot -pseek_root dmac'
docker compose exec db sh -lc 'mysqldump -uroot -pseek_root seek_docker | mysql -uroot -pseek_root seek_production'
docker compose exec db mysql -uroot -pseek_root -e "GRANT ALL PRIVILEGES ON dmac.* TO '\''seek_db_user'\''@'\''%'\''; GRANT ALL PRIVILEGES ON seek_production.* TO '\''seek_db_user'\''@'\''%'\''; FLUSH PRIVILEGES;"
docker compose up -d --force-recreate db nextseek
```

After verification:

```bash
docker compose exec db mysql -uroot -pseek_root -e "DROP DATABASE nextseek; DROP DATABASE seek_docker;"
```

#### 3. Start Neo4j only

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose up -d neo4j
sleep 20
docker compose ps
```

#### 4. Import Neo4j data

```bash
docker exec -i neo4j cypher-shell -u neo4j -p demopassword < /home/cdemu/code/dmac/docker/resources/neo4j_export_clean.cypher
```

Optional verification:

```bash
docker exec neo4j cypher-shell -u neo4j -p demopassword "MATCH (n) RETURN labels(n)[0] as label, count(n) as count ORDER BY count DESC"
```

### Build and Start the Stack

#### 1. Start SEEK-side services

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose up -d solr seek seek_workers
```

#### 2. Build NExtSEEK

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose build nextseek
```

For a clean rebuild:

```bash
docker compose build --no-cache nextseek
```

#### 3. Start NExtSEEK and nginx

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose up -d nextseek nextseek_nginx
```

The entrypoint already runs migrations and `collectstatic`. Manual `collectstatic` is only needed if startup fails midway.

## Configuration

### `docker/db.env`

Current local Docker defaults:

```bash
MYSQL_HOST="db"
MYSQL_ROOT_PASSWORD="seek_root"
MYSQL_DATABASE="seek_production"
NEXTSEEK_MYSQL_DATABASE="dmac"
MYSQL_USER="seek_db_user"
MYSQL_PASSWORD="seek_db_password"
```

### `docker/nextseek.env`

Current local Docker defaults:

```bash
SEEK_HOST="seek"
SEEK_HOSTNAME="http://seek:3000"
NEXTSEEK_HOSTNAME="127.0.0.1:8000"
NEXTSEEK_NEO4J_PASSWORD="demopassword"
NEXTSEEK_NEO4J_HOST="neo4j"
DJANGO_SECRET_KEY="fkjfmsalkflksamflkdsafms"
DJANGO_ALLOWED_HOSTS="127.0.0.1 localhost"
DJANGO_CSRF_TRUSTED_ORIGINS="http://localhost:8000 http://127.0.0.1:8000"
NEXTSEEK_BASE_URL="http://${NEXTSEEK_HOSTNAME}"
LOG_DIR="/app/logs"
NEXTSEEK_OUTPUTS_DIR="/app/outputs"
NEO4J_URI="neo4j://${NEXTSEEK_NEO4J_HOST}"
NEO4J_USER="neo4j"
NEO4J_PASSWORD=$NEXTSEEK_NEO4J_PASSWORD
```

### `dmac/settings.py`

The current branch is env-driven for:

- Django allowed hosts
- CSRF trusted origins
- MySQL connection settings
- Docker-facing runtime paths
- theme/static wiring for `themes/NextSeek`

### `dmac/local_settings.py`

This file is still required for several runtime values not carried entirely by environment variables.

Do not copy production-only values directly into local Docker. In particular, avoid copying real credentials, production hostnames, or hardcoded database settings from another environment.

### `uv.lock` and `chat_nextseek`

The Docker build expects the locked Python environment in `uv.lock` and a local path dependency at `chat_nextseek/`.

If `chat_nextseek/` is missing, `docker compose build nextseek` is expected to fail.

## Runtime Notes and Troubleshooting

### Rebuilds matter more than restarts

In this setup, source changes generally require rebuilding the `nextseek` image rather than only restarting containers:

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose build nextseek
docker compose up -d nextseek nextseek_nginx
docker compose exec nextseek uv run manage.py collectstatic --noinput
docker compose restart nextseek_nginx
```

### Browser cache can hide frontend fixes

After rebuild and `collectstatic`, do a hard refresh before assuming the frontend is still stale.

### Required post-start validation

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
docker compose ps
docker compose logs --tail=100 nextseek
docker compose logs --tail=100 nextseek_nginx
docker compose logs --tail=100 seek
docker compose exec nextseek uv run manage.py check
docker compose exec nextseek uv run manage.py shell -c "from django.conf import settings; print(settings.CSRF_TRUSTED_ORIGINS)"
```

Expected `CSRF_TRUSTED_ORIGINS`:

```python
['http://localhost:8000', 'http://127.0.0.1:8000']
```

### Compose file confusion

There is a parent-level compose file under `/home/cdemu/code/dmac/docker/` and a repo-local one under `NExtSEEK/`. For this workflow, use the repo-local compose file only.

### Missing `chat_nextseek`

Cause:

- `pyproject.toml` declares `chat_nextseek` as a local dependency

Fix:

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK
git clone git@github.com:cdemurjian/chat_nextseek.git chat_nextseek
```

### CSRF or login errors

Cause:

- `DJANGO_CSRF_TRUSTED_ORIGINS` or `DJANGO_ALLOWED_HOSTS` not set correctly in `docker/nextseek.env`

Validate:

```bash
docker compose exec nextseek uv run manage.py check
docker compose exec nextseek uv run manage.py shell -c "from django.conf import settings; print(settings.CSRF_TRUSTED_ORIGINS)"
```

### Missing values from `local_settings.py`

Confirmed runtime-sensitive settings include:

- `PUBLISH_URL`
- `SAMPLE_TEMPLATES_FOLDER`
- `ASSISTANT_PARTICIPATING_PROJECTS`
- `TEST_CASES`

If needed, compare the host and container copies:

```bash
cat dmac/local_settings.py
docker compose exec nextseek cat /app/dmac/local_settings.py
```

### Static asset failures

If static files are missing or outdated:

```bash
docker compose exec nextseek uv run manage.py collectstatic --noinput
docker compose restart nextseek_nginx
```

### Known backend SQL issue

The project detail page `/seek/projects/<id>/` may still fail under MySQL `ONLY_FULL_GROUP_BY` due to logic in [dmac/dbtable_clades.py](dmac/dbtable_clades.py).

### Batch upload `LAST_INSERT_ID()` issue

A Docker/MySQL-specific failure was observed around `LAST_INSERT_ID()` result parsing in `nextseek_api.batch_upload.policies`. If batch upload fails at `Stage 5/7: INSERT`, rebuild after applying the corresponding code fix and re-test inside Docker.

## Developer Documentation

See [CLAUDE.md](CLAUDE.md) for contributor-focused notes on architecture, key directories, important files, and development workflow. It is intended as a high-signal internal orientation document rather than end-user setup documentation.

## UI Reference

See [UI.md](UI.md) for the current UI map, including:

- frontend/backend architecture overview
- route-to-view-to-template mappings
- theme structure under `themes/NextSeek/`
- chat frontend structure under `chat_frontend/`

## Release Notes

### Version 1.3.0

Release date: *January 23, 2025*

This release adds some quality of life improvements to dropdown inputs, adds a templates page, and adds a timeline feature for NHP sample types. The timeline feature is based on code made by [Taisha Joseph](https://github.com/tavjo)

## References

NExtSEEK: Extending SEEK for active management of scientific metadata, Dikshant Pradhan, Huiming Ding, Jingzhi Zhu, Bevin P. Engelward, and Stuart S. Levin, MIT BioMicro Center, Department of Biology, Massachusetts Institute of Technology, Cambridge, MA, USA

## Checklist for Preparing to Use NExtSEEK

#### Identify key samples and data to deposit

The NIH and journals will generally require:

- The rawest form of the generated data, meaning the immediate output from the instruments used or direct observations and measurements from experiments
- Processed data which is central to the work, such as gene expression count matrices and spectra features
- Unique materials integral to the work, such as unique chemical compounds, plasmids, or cell lines
- Code necessary for processing raw data into the interpreted dataset

#### Identify FAIR-compliant field-specific repositories for data and samples

Repositories should conform to the FAIR data standards and, where possible, be well utilized in their respective fields. Examples include GEO, PRIDE, MGI, and others. Lists of available repositories can be found at Nature and FairSharing. Where field-specific repositories do not exist, researchers can deposit to general repositories such as FigShare, Dryad, and Zenodo.

#### Identify relevant ontologies for their field

When collecting metadata, users should use shared language for interoperability. Users should pull language from the most relevant ontology to their field. Researchers can search for ontologies through EMBL-EBI and BioPortal. Recommended ontologies include the NCI Thesaurus, Experimental Factor Ontology, and the BRENDA Tissue Ontology.

#### Identify key metadata required by the repository of choice

These metadata will vary by endpoint, but the collected metadata should fulfill the following criteria to be FAIR compliant:

- Use a formal and shared language for knowledge representation, drawn from established ontologies where possible
- Include qualified references to other data and metadata where relevant
- Include detailed provenance, such as references to parent samples
- Include protocols and code which are open, free, and universally implementable
- Metadata should be richly described with accurate and relevant attributes

#### Identify additional critical metadata

To maximize impact and reusability, researchers should try to collect the following information:

- Experimental groups and cohorts
- Variables and parameters that change between experiments
- Known potential covariates
- Scientists responsible for samples and experiments
- Tools and instruments, including model and software versions
- Reagents, including manufacturers and lot numbers
- Treatments
- Target analytes, antibodies, stains, reporters, and related details

## Contact Us

For questions about setup or reporting bugs, please visit [BioMicro Center, MIT](https://biology.mit.edu/tile/biomicro-center/).
